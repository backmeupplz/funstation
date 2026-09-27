# funstation — instructions for AI agents

A Raspberry Pi 4/400 (a Zero 2 W should work too) plugged into a TV/projector over HDMI. It boots into a couch launcher with Jellyfin, Navidrome (music) and mGBA games. You control it with an Xbox controller over Bluetooth, or from a browser anywhere on the tailnet.

## Architecture

- **OS**: Raspberry Pi OS Lite (Trixie, arm64). There's no desktop. tty1 autologin runs `startx /opt/funstation/xinitrc`.
- **`pi/xinitrc`** starts `matchbox-window-manager` (fullscreen, one window at a time), `x11vnc` (localhost only) and `funstation.py` (restarted if it dies). It then runs Chromium in kiosk mode on `http://localhost:8080`, with `--remote-debugging-port=9222` (localhost) and its own profile dir `~/.config/funstation-browser` (Chromium refuses remote debugging on the default profile). It also restarts Chromium if it dies.
- **`pi/funstation.py`** is one process:
  - **HTTP server** on `127.0.0.1:8080`:
    - serves `pi/ui/index.html`
    - `GET /api/state` returns games, the configured web apps, and the running apps
    - `GET /cover/<rom>` returns box art: `<rom>.png`/`.jpg` next to the ROM if present, otherwise libretro thumbnails, cached in `~/.cache/funstation`
    - `POST /api/launch {"app": "jellyfin" | "navidrome" | "<rom file>"}` opens or resumes an app
    - `POST /api/close {"app": ...}` closes an app
    - `POST /api/home` does the same as the Xbox button
  - **Apps**: an app is `launcher`, a web app id, or a ROM file name. Several can run at once.
    - Web apps are `WEBAPPS` in `funstation.py` (id → URL from `config.env`: `JELLYFIN_URL`, `NAVIDROME_URL`), shown on the launcher via `WEBAPPS` in `ui/index.html`. Each runs in its own Chromium tab, logged in once by hand; the login lives in the browser profile. For Navidrome this is its built-in web player: nothing to install, and it fits the tab model, unlike Electron clients such as Feishin. Tabs are switched and closed through the DevTools HTTP endpoints (`/json/activate`, `/json/new`, `/json/close`). The same connection is used to pause media (`<video>`/`<audio>`) and to set Jellyfin's `layout=tv` localStorage key, which gives it D-pad navigation.
    - Each game is its own `mgba -f` process.
    - Backgrounding an app freezes a game (SIGSTOP) or pauses the web app's `<video>`/`<audio>`, so it goes silent.
    - Windows are brought to the front with `xdotool windowactivate <id>`. Look up the id first: chained `xdotool search … windowactivate` does nothing under matchbox, and matchbox unmaps windows that aren't in front.
  - **Xbox button**: a tap backgrounds the current app and shows the launcher. From the launcher, it resumes the most recent app. It acts on release, so hold + D-pad up/down can be the volume combo.
  - **Controller loop** (python-evdev):
    - Outside games it grabs the pad and drives a uinput mouse and keyboard. Left stick moves the mouse and the right stick scrolls.
    - A sends Enter if the D-pad was used last, or a click if the stick was.
    - B is back (Alt+Left). X is Space. Y is right click, or Delete in the launcher, which closes the selected running app. Menu is Enter, View is Esc, and the D-pad sends arrow keys.
    - LB/RB change the volume.
    - While a game is in front it releases the grab, so mGBA reads the pad directly. mGBA bindings are in the `config.ini` written by `setup.sh`.
  - **Volume overlay**: Tk owns the main thread and shows an always-on-top override-redirect bar for 1.5 s after each change (`wpctl`).
- **Display hotplug**: `xinitrc` polls `/sys/class/drm/*HDMI*/status` and runs `xrandr --auto` when a display connects. With nothing connected, it sets a 1920×1080 virtual screen so the web mirror stays usable.
- **Web mirror**:
  - `funstation-web.service` runs websockify and noVNC on `127.0.0.1:6080`.
  - `tailscale serve --bg --https=443 http://127.0.0.1:6080` publishes it at `https://<host>.<tailnet>.ts.net/`.
  - `/usr/share/novnc/index.html` redirects to `vnc.html?autoconnect=1&resize=scale`.
- **Audio**: PipeWire with the bluez plugin. A Bluetooth speaker becomes the default output once it connects. `funstation-bt.service` (`pi/bt-autoconnect.sh`) reconnects paired audio devices every 15 s. Controllers reconnect by themselves once they're trusted.

## Replicating from scratch

1. **Flash** the latest `raspios_lite_arm64` image. The original Pi Zero W (ARMv6) is too slow for this, and no current Chromium runs on it.
   - Before first boot, edit the boot partition. It uses cloud-init.
   - `user-data`: hostname, user `fun` with an SSH public key, `sudo: ALL=(ALL) NOPASSWD:ALL`, `lock_passwd: true` and `runcmd: [[systemctl, enable, --now, ssh]]`. Also create an empty `ssh` file.
   - `meta-data`: set a new `instance-id` (hyphen; the template's `instance_id` is ignored). The image ships with the default ID already marked as done, so cloud-init skips the users/SSH-key/sudo modules unless the ID changes.
   - `network-config`: netplan v2 with `wifis.wlan0.access-points` and `regulatory-domain` (Wi-Fi stays rfkill-blocked without it).
   - `config.txt`: `disable_splash=1`, `boot_delay=0`, `camera_auto_detect=0`.
   - Writing the raw device usually needs the user's sudo (`dd ... conv=fsync`), so ask them to run it.
2. **Find the Pi** at `funstation.local`, or ping-sweep the LAN and look for Raspberry Pi MAC prefixes in `ip neigh`.
3. **Configure**: `cp config.example.env config.env` and fill it in. `config.env` is gitignored.
4. **Install**: `./install.sh`. It copies `pi/` over SSH and runs `pi/setup.sh` as root. It's idempotent, so re-run it after every change. ROMs from `LOCAL_ROMS` are copied with `--skip-old-files`, so saves made on the Pi are never overwritten.
5. **Tailscale**:
   - On the Pi, run `sudo tailscale up --hostname=funstation`, give the user the login URL, then re-run `./install.sh` so `tailscale serve` gets configured.
   - After that, set `FUN_HOST` to the MagicDNS name.
   - Plain OpenSSH works over the tailnet. Don't use `--ssh`: Tailscale SSH check mode would keep asking for browser re-auth.
6. **Pair Bluetooth** (the user has to press the pair buttons). Pair from a single `bluetoothctl` session so its default agent handles the pairing; one-shot `bluetoothctl pair` has no agent and fails with Authentication Failed:
   ```
   { echo "scan on"; sleep 10; echo "pair <MAC>"; sleep 12; echo "trust <MAC>"; echo "connect <MAC>"; sleep 8; echo quit; } | sudo bluetoothctl
   ```
   Xbox controllers are BLE and show up in a normal scan. Classic speakers (e.g. JBL Charge 4) may only appear with `menu scan` → `transport bredr` → `back` before `scan on`.
   Check the controller with `python3 -m evdev.evtest`. The name must contain "Xbox" or "Controller" for `funstation.py` to pick it up.

## Debugging on the Pi

- Launcher/pad logs: the X session has no journal. `pkill -f '^python3 /opt/funstation/funstation.py'` and xinitrc restarts it; to see output, run it by hand with `DISPLAY=:0` and `/etc/funstation.env` loaded.
- Drive it without a controller: `curl -XPOST localhost:8080/api/launch -d '{"app":"jellyfin"}'`, `curl -XPOST localhost:8080/api/home`, `DISPLAY=:0 xdotool key Right`.
- Screen: open the web mirror, or `DISPLAY=:0 scrot -o /tmp/s.png` (`apt install scrot`).
- Boot time: `systemd-analyze` and `systemd-analyze blame`.
- Audio: `wpctl status` (run as the `fun` user).
