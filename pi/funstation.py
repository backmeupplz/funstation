#!/usr/bin/env python3
"""Funstation: launcher backend, app switching (with backgrounding), Xbox pad -> mouse/keyboard, volume OSD."""
import json, logging, os, queue, select, signal, subprocess, threading, time, urllib.parse, urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import tkinter as tk

import evdev
import websocket
from evdev import UInput, ecodes as e

ROMS = os.path.expanduser(os.environ.get("ROMS_DIR", "~/roms"))
JELLYFIN = os.environ.get("JELLYFIN_URL", "")
# web apps, each in its own Chromium tab: id -> URL (unset ones are hidden)
WEBAPPS = {k: v for k, v in (("jellyfin", JELLYFIN), ("navidrome", os.environ.get("NAVIDROME_URL", ""))) if v}
LAUNCHER = "http://localhost:8080/"
CDP = "http://127.0.0.1:9222"  # Chromium remote debugging, localhost only
COVERS = os.path.expanduser("~/.cache/funstation/covers")
SYSTEMS = {".gba": "Nintendo - Game Boy Advance", ".gbc": "Nintendo - Game Boy Color", ".gb": "Nintendo - Game Boy"}

fg = "launcher"      # what's on screen: launcher | <web app id> | <rom file>
bg = []              # backgrounded apps, most recent last
games_running = {}   # rom file -> mgba Popen
lock = threading.RLock()
log = logging.getLogger("funstation")
osd = queue.Queue()  # volume levels for the on-screen overlay

kbd = UInput({e.EV_KEY: list(range(1, 249))}, name="funstation-kbd")
mouse = UInput({e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE],
                e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL, e.REL_HWHEEL]}, name="funstation-mouse")


def games():
    roms = [f for f in os.listdir(ROMS) if os.path.splitext(f)[1].lower() in SYSTEMS] if os.path.isdir(ROMS) else []
    return [{"file": f, "name": os.path.splitext(f)[0].split(" (")[0]} for f in sorted(roms)]


def cover(rom):
    """Local <rom>.png/.jpg next to the ROM, else libretro box art (cached)."""
    base, ext = os.path.splitext(rom)
    for c in (os.path.join(ROMS, base + x) for x in (".png", ".jpg")):
        if os.path.exists(c):
            return c
    cached = os.path.join(COVERS, base + ".png")
    if not os.path.exists(cached):
        name = "".join("_" if ch in '&*/:`<>?\\|"' else ch for ch in base)
        url = "https://thumbnails.libretro.com/%s/Named_Boxarts/%s.png" % (
            urllib.parse.quote(SYSTEMS[ext.lower()]), urllib.parse.quote(name))
        try:
            data = urllib.request.urlopen(url, timeout=10).read()
        except OSError:
            return None
        os.makedirs(COVERS, exist_ok=True)
        with open(cached, "wb") as f:
            f.write(data)
    return cached


# --- Chromium tabs (the launcher and each web app live in separate tabs) ----------
def cdp(path, method="GET"):
    with urllib.request.urlopen(urllib.request.Request(CDP + path, method=method), timeout=5) as r:
        return r.read()  # JSON for /json/list, plain text for /json/activate


def tab(prefix):
    return next((t for t in json.loads(cdp("/json/list")) if t["type"] == "page" and t["url"].startswith(prefix)), None)


def js(t, expr):
    ws = websocket.create_connection(t["webSocketDebuggerUrl"], timeout=5, suppress_origin=True)
    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": expr}}))
    ws.recv()
    ws.close()


def raise_window(*search):
    # look the id up first: chained "xdotool search ... windowactivate" silently does nothing under matchbox
    ids = subprocess.run(["xdotool", "search", *search], capture_output=True, text=True).stdout.split()
    if ids:
        subprocess.run(["xdotool", "windowactivate", ids[-1]], stderr=subprocess.DEVNULL)


def show_tab(url):
    raise_window("--name", " - Chromium$")  # the browser window (Chromium also has unnamed helper windows)
    t = tab(url)
    if t:
        return cdp("/json/activate/" + t["id"])
    cdp("/json/new?" + url, "PUT")
    if url == JELLYFIN:
        threading.Thread(target=jellyfin_tv_layout, daemon=True).start()


def jellyfin_tv_layout():
    """Jellyfin's TV layout gives D-pad (arrow key) navigation; it's a per-origin localStorage setting."""
    for _ in range(30):
        time.sleep(1)
        try:
            js(tab(JELLYFIN), "localStorage.getItem('layout')!=='tv'&&(localStorage.setItem('layout','tv'),location.reload())")
            return
        except Exception:
            pass


# --- apps: "launcher", a web app id, or a ROM file name --------------------------------
def running():
    try:
        urls = [t["url"] for t in json.loads(cdp("/json/list")) if t["type"] == "page"]
    except OSError:
        urls = []
    return [a for a, u in WEBAPPS.items() if any(x.startswith(u) for x in urls)] + list(games_running)


def suspend(app):
    """Backgrounded apps go quiet: games are frozen, web app media is paused."""
    if app in games_running:
        games_running[app].send_signal(signal.SIGSTOP)
    elif app in WEBAPPS:
        try:
            js(tab(WEBAPPS[app]), "document.querySelectorAll('video,audio').forEach(m=>m.pause())")
        except Exception:
            pass


def open_app(app, why=""):
    global fg
    log.info("open %s (fg=%s bg=%s) %s", app, fg, bg, why)
    with lock:
        if fg not in ("launcher", app):  # re-opening the current app just re-shows it (e.g. after a Chromium restart)
            suspend(fg)
            bg.append(fg)
        if app in games_running:
            games_running[app].send_signal(signal.SIGCONT)
            raise_window("--pid", str(games_running[app].pid))
        elif app in WEBAPPS:
            show_tab(WEBAPPS[app])
        elif app == "launcher":
            show_tab(LAUNCHER)
        elif app in os.listdir(ROMS):
            p = games_running[app] = subprocess.Popen(["mgba", "-f", os.path.join(ROMS, app)])
            threading.Thread(target=watch_game, args=(app, p), daemon=True).start()
        else:
            return
        if app in bg:
            bg.remove(app)
        fg = app


def close_app(app):
    with lock:
        if app in games_running:
            p = games_running.pop(app)
            p.send_signal(signal.SIGCONT)  # a frozen process can't handle SIGTERM
            p.terminate()  # SDL quits cleanly on SIGTERM; mGBA flushes the battery save
        elif app in WEBAPPS:
            t = tab(WEBAPPS[app])
            if t:
                cdp("/json/close/" + t["id"])
        if app in bg:
            bg.remove(app)
        if fg == app:
            open_app("launcher")


def watch_game(app, p):
    global fg
    p.wait()
    with lock:
        if games_running.get(app) is p:
            del games_running[app]
        if app in bg:
            bg.remove(app)
        if fg == app:
            fg = "launcher"  # it's gone; nothing to suspend
            show_tab(LAUNCHER)


def home():
    """Xbox button: background the current app and show the launcher; from the launcher, resume the last app."""
    with lock:
        if fg != "launcher":
            open_app("launcher", "xbox")
        elif bg:
            open_app(bg[-1], "xbox resume")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui"), **kw)

    def send_json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/state":
            return self.send_json({"games": games(), "apps": list(WEBAPPS), "running": running()})
        if self.path.startswith("/cover/"):
            c = cover(os.path.basename(urllib.parse.unquote(self.path[7:])))
            if not c:
                return self.send_error(404)
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg" if c.endswith(".jpg") else "image/png")
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            with open(c, "rb") as f:
                return self.wfile.write(f.read())
        super().do_GET()

    def do_POST(self):
        if self.path == "/api/launch":
            app = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["app"]
            threading.Thread(target=open_app, args=(app, "from launcher page"), daemon=True).start()
            return self.send_json({"ok": True})
        if self.path == "/api/close":
            close_app(json.loads(self.rfile.read(int(self.headers["Content-Length"])))["app"])
            return self.send_json({"ok": True})
        if self.path == "/api/home":  # same as the Xbox button
            threading.Thread(target=home, daemon=True).start()
            return self.send_json({"ok": True})
        self.send_error(404)

    def log_message(self, *a):
        pass


# --- controller -> mouse/keyboard -------------------------------------------------
BUTTONS = {  # pad button -> (device, key)
    e.BTN_NORTH: (kbd, e.KEY_SPACE),                # X: play/pause
    e.BTN_WEST: (mouse, e.BTN_RIGHT),               # Y: right click
    e.BTN_START: (kbd, e.KEY_ENTER),                # Menu: enter
    e.BTN_SELECT: (kbd, e.KEY_ESC),                 # View: escape
}
HATS = {(e.ABS_HAT0X, -1): e.KEY_LEFT, (e.ABS_HAT0X, 1): e.KEY_RIGHT,
        (e.ABS_HAT0Y, -1): e.KEY_UP, (e.ABS_HAT0Y, 1): e.KEY_DOWN}
GUIDE = (e.BTN_MODE, e.KEY_HOMEPAGE)
VOLUME = {e.BTN_TL: "5%-", e.BTN_TR: "5%+"}  # LB/RB outside games; Xbox + D-pad up/down everywhere
SPEED, SCROLL, DEAD = 1400.0, 12.0, 0.15  # px/s, notches/s at full tilt, stick deadzone


def tap(*keys):
    for k in keys:
        kbd.write(e.EV_KEY, k, 1)
    for k in reversed(keys):
        kbd.write(e.EV_KEY, k, 0)
    kbd.syn()


def volume(step):
    subprocess.run(["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", step], stderr=subprocess.DEVNULL)
    out = subprocess.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], capture_output=True, text=True).stdout
    try:
        osd.put(float(out.split()[1]))
    except (IndexError, ValueError):
        pass


def find_pads():
    pads = []
    for p in evdev.list_devices():
        d = evdev.InputDevice(p)
        if "funstation" not in d.name and ("Xbox" in d.name or "Controller" in d.name):
            pads.append(d)
    return pads


def pad_loop():
    pads, grabbed, axes, hat, acc = [], None, {}, {}, [0.0] * 4
    guide_held = guide_combo = False
    a_key = None  # what A is currently holding down: Enter after D-pad navigation, click after stick movement
    nav = "mouse"
    last = time.monotonic()
    while True:
        if not pads:
            pads, grabbed = find_pads(), None
            if not pads:
                time.sleep(2)
                continue
        want = fg not in games_running  # grab outside games so Chromium/Jellyfin don't see the raw gamepad; release for mGBA
        if grabbed != want:
            if not want:  # handing the pad to a game: release anything we're holding, or it sticks (and auto-repeats)
                for dev in (kbd, mouse):
                    for key in dev.capabilities()[e.EV_KEY]:
                        dev.write(e.EV_KEY, key, 0)
                    dev.syn()
                hat, a_key = {}, None
            for d in pads:
                try:
                    d.grab() if want else d.ungrab()
                except OSError:
                    pass
            grabbed = want
        r, _, _ = select.select(pads, [], [], 1 / 60)
        try:
            for d in r:
                for ev in d.read():
                    if ev.type == e.EV_KEY and ev.code in GUIDE and ev.value in (0, 1):
                        log.info("xbox %s from %s", "down" if ev.value else "up", d.path)
                        if ev.value:
                            guide_held, guide_combo = True, False
                        else:
                            guide_held = False
                            if not guide_combo:
                                threading.Thread(target=home, daemon=True).start()  # tap = home; hold + D-pad = volume
                    elif guide_held and ev.type == e.EV_ABS and ev.code == e.ABS_HAT0Y and ev.value:
                        volume("5%+" if ev.value < 0 else "5%-")
                        guide_combo = True
                    elif not want:
                        continue
                    elif ev.type == e.EV_KEY and ev.code == e.BTN_SOUTH and ev.value in (0, 1):
                        if ev.value:
                            a_key = (kbd, e.KEY_ENTER) if nav == "dpad" else (mouse, e.BTN_LEFT)
                        if a_key:
                            a_key[0].write(e.EV_KEY, a_key[1], ev.value)
                            a_key[0].syn()
                    elif ev.type == e.EV_KEY and ev.code == e.BTN_WEST and ev.value == 1 and fg == "launcher":
                        tap(e.KEY_DELETE)  # Y in the launcher: close the selected app
                    elif ev.type == e.EV_KEY and ev.code in VOLUME and ev.value == 1:
                        volume(VOLUME[ev.code])
                    elif ev.type == e.EV_KEY and ev.code == e.BTN_EAST and ev.value == 1:
                        tap(e.KEY_LEFTALT, e.KEY_LEFT)  # B: back
                    elif ev.type == e.EV_KEY and ev.code in BUTTONS and ev.value in (0, 1):
                        dev, key = BUTTONS[ev.code]
                        dev.write(e.EV_KEY, key, ev.value)
                        dev.syn()
                    elif ev.type == e.EV_ABS and ev.code in (e.ABS_HAT0X, e.ABS_HAT0Y):
                        if hat.get(ev.code):
                            kbd.write(e.EV_KEY, HATS[ev.code, hat[ev.code]], 0)
                        if ev.value:
                            kbd.write(e.EV_KEY, HATS[ev.code, ev.value], 1)
                            nav = "dpad"
                        kbd.syn()
                        hat[ev.code] = ev.value
                    elif ev.type == e.EV_ABS:
                        info = d.absinfo(ev.code)
                        mid, half = (info.max + info.min) / 2, (info.max - info.min) / 2
                        v = (ev.value - mid) / half if half else 0
                        axes[ev.code] = 0 if abs(v) < DEAD else v
        except OSError:  # controller went away
            pads, axes, hat = [], {}, {}
            continue
        now = time.monotonic()
        dt, last = min(now - last, 0.1), now
        if not want:
            continue
        # BLE Xbox pads report the right stick as Z/RZ, USB (xpad) as RX/RY
        rx, ry = (e.ABS_RX, e.ABS_RY) if e.ABS_RX in axes else (e.ABS_Z, e.ABS_RZ)
        vel = [axes.get(e.ABS_X, 0), axes.get(e.ABS_Y, 0), axes.get(rx, 0), axes.get(ry, 0)]
        for i, v in enumerate(vel):
            acc[i] += v * abs(v) * (SPEED if i < 2 else SCROLL) * dt  # quadratic curve: fine control near center
        sent = False
        for code, i in [(e.REL_X, 0), (e.REL_Y, 1), (e.REL_HWHEEL, 2), (e.REL_WHEEL, 3)]:
            n = int(acc[i])
            if n:
                mouse.write(e.EV_REL, code, -n if code == e.REL_WHEEL else n)
                acc[i] -= n
                sent = True
                if i < 2:
                    nav = "mouse"
        if sent:
            mouse.syn()


# --- volume overlay (Tk owns the main thread) ------------------------------------
def overlay():
    root = tk.Tk()
    root.withdraw()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    sw = root.winfo_screenwidth()
    w, h = sw // 4, sw // 22
    root.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, h // 2))
    c = tk.Canvas(root, width=w, height=h, bg="#0d0f1a", highlightthickness=0)
    c.pack()
    font = ("DejaVu Sans", -(h // 3), "bold")
    hide_at = [0.0]

    def poll():
        try:
            level = osd.get_nowait()
        except queue.Empty:
            if hide_at[0] and time.monotonic() > hide_at[0]:
                root.withdraw()
                hide_at[0] = 0
        else:
            pad, bar = h // 4, h // 6
            c.delete("all")
            c.create_text(pad, h // 2, text="%d%%" % round(level * 100), fill="#eef0ff", font=font, anchor="w")
            x0 = w // 3
            c.create_rectangle(x0, (h - bar) // 2, w - pad, (h + bar) // 2, fill="#1a1e33", outline="")
            c.create_rectangle(x0, (h - bar) // 2, x0 + (w - pad - x0) * min(level, 1), (h + bar) // 2, fill="#ffcc33", outline="")
            root.deiconify()
            root.lift()
            hide_at[0] = time.monotonic() + 1.5
        root.after(50, poll)

    poll()
    root.mainloop()


if __name__ == "__main__":
    logging.basicConfig(filename=os.path.expanduser("~/.cache/funstation/log"), level=logging.INFO,
                        format="%(asctime)s %(threadName)s %(message)s")
    threading.Thread(target=pad_loop, daemon=True).start()
    threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", 8080), Handler).serve_forever, daemon=True).start()
    overlay()
