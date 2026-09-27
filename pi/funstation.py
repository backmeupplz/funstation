#!/usr/bin/env python3
"""Funstation: launcher web server + Xbox pad -> mouse/keyboard + app switching."""
import glob, json, os, select, subprocess, threading, time, urllib.parse, urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import evdev
from evdev import UInput, ecodes as e

ROMS = os.path.expanduser(os.environ.get("ROMS_DIR", "~/roms"))
JELLYFIN = os.environ.get("JELLYFIN_URL", "")
COVERS = os.path.expanduser("~/.cache/funstation/covers")
SYSTEMS = {".gba": "Nintendo - Game Boy Advance", ".gbc": "Nintendo - Game Boy Color", ".gb": "Nintendo - Game Boy"}
game = None  # running mgba process

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


def launch(rom):
    global game
    if game or rom not in os.listdir(ROMS):
        return
    game = subprocess.Popen(["mgba", "-f", os.path.join(ROMS, rom)])
    threading.Thread(target=lambda: (game.wait(), globals().update(game=None)), daemon=True).start()


def tap(*keys):
    for k in keys:
        kbd.write(e.EV_KEY, k, 1)
    for k in reversed(keys):
        kbd.write(e.EV_KEY, k, 0)
    kbd.syn()


def home():
    if game:
        game.terminate()  # SDL turns SIGTERM into a clean quit, battery save is flushed
    else:
        tap(e.KEY_ESC)
        tap(e.KEY_LEFTALT, e.KEY_HOME)  # Chromium "go to home page" (policy points it at the launcher)


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
            return self.send_json({"games": games(), "jellyfin": JELLYFIN})
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
            launch(json.loads(self.rfile.read(int(self.headers["Content-Length"])))["file"])
            return self.send_json({"ok": True})
        self.send_error(404)

    def log_message(self, *a):
        pass


# --- controller -> mouse/keyboard -------------------------------------------------
BUTTONS = {  # pad button -> (device, key...)
    e.BTN_SOUTH: (mouse, e.BTN_LEFT),               # A: click
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


def volume(step):
    subprocess.run(["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", step])


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
    last = time.monotonic()
    while True:
        if not pads:
            pads, grabbed = find_pads(), None
            if not pads:
                time.sleep(2)
                continue
        want = game is None  # grab in desktop mode so Chromium/Jellyfin don't see raw gamepad; release for mGBA
        if grabbed != want:
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
                        if ev.value:
                            guide_held, guide_combo = True, False
                        else:
                            guide_held = False
                            if not guide_combo:
                                home()  # tap = home; hold + D-pad = volume
                    elif guide_held and ev.type == e.EV_ABS and ev.code == e.ABS_HAT0Y and ev.value:
                        volume("5%+" if ev.value < 0 else "5%-")
                        guide_combo = True
                    elif not want:
                        continue
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
        moves = [(e.REL_X, 0), (e.REL_Y, 1), (e.REL_HWHEEL, 2), (e.REL_WHEEL, 3)]
        sent = False
        for code, i in moves:
            n = int(acc[i])
            if n:
                mouse.write(e.EV_REL, code, -n if code == e.REL_WHEEL else n)
                acc[i] -= n
                sent = True
        if sent:
            mouse.syn()


if __name__ == "__main__":
    threading.Thread(target=pad_loop, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", 8080), Handler).serve_forever()
