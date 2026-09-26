# funstation — instructions for AI agents

A Raspberry Pi Zero 2 W plugged into a TV/projector over HDMI. It boots into a couch launcher with Jellyfin and mGBA games. You control it with an Xbox controller over Bluetooth, or from a browser anywhere on the tailnet.

## Architecture

- **OS**: Raspberry Pi OS Lite (Trixie, arm64). There's no desktop. tty1 autologin runs `startx /opt/funstation/xinitrc`.
- **`pi/xinitrc`** starts `matchbox-window-manager` (fullscreen, one window at a time), `x11vnc` (localhost only) and `funstation.py`. It then runs Chromium in kiosk mode on `http://localhost:8080` and restarts it if it dies.
- **`pi/funstation.py`** is one process with two jobs:
  - An HTTP server on `127.0.0.1:8080`:
    - serves `pi/ui/index.html`
    - `GET /api/state` returns the games and the Jellyfin URL
    - `GET /cover/<rom>` returns the box art. It uses `<rom>.png`/`.jpg` next to the ROM if present, otherwise it downloads libretro thumbnails and caches them in `~/.cache/funstation`.
    - `POST /api/launch` starts `mgba -f <rom>`.
  - A controller loop (python-evdev):
    - In desktop mode it grabs the pad and turns it into a virtual mouse and keyboard through uinput. Left stick moves the mouse, right stick scrolls, A is left click, B is back (Alt+Left), X is Space, Y is right click, Menu is Enter, View is Esc and the D-pad sends arrow keys.
    - While mGBA runs it releases the grab so mGBA reads the pad directly.
    - The Xbox button quits mGBA (SIGTERM, which is a clean quit, so battery saves are flushed). Outside a game it sends Esc and Alt+Home. A Chromium policy sets the home page to the launcher, so Alt+Home returns there.
- **Jellyfin** is simply Chromium navigating to `JELLYFIN_URL`.
- **Web mirror**:
  - `funstation-web.service` runs websockify and noVNC on `127.0.0.1:6080`.
  - `tailscale serve --bg --https=443 http://127.0.0.1:6080` publishes it at `https://<host>.<tailnet>.ts.net/`.
  - `/usr/share/novnc/index.html` redirects to `vnc.html?autoconnect=1&resize=scale`.
- **Audio**: PipeWire with the bluez plugin. A Bluetooth speaker becomes the default output once it connects. `funstation-bt.service` (`pi/bt-autoconnect.sh`) reconnects paired audio devices every 15 s. Controllers reconnect by themselves once they're trusted.

## Replicating from scratch

1. **Flash** the latest `raspios_lite_arm64` image.
   - Before first boot, edit the boot partition. It uses cloud-init.
   - `user-data`: hostname, user `fun` with an SSH public key, `sudo: ALL=(ALL) NOPASSWD:ALL`, `lock_passwd: true` and `runcmd: [[systemctl, enable, --now, ssh]]`. Also create an empty `ssh` file.
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
6. **Pair Bluetooth** (the user has to press the pair buttons):
   ```
   sudo bluetoothctl --timeout 30 scan on          # while the device is in pairing mode
   sudo bluetoothctl pair <MAC> && sudo bluetoothctl trust <MAC> && sudo bluetoothctl connect <MAC>
   ```
   Check the controller with `python3 -m evdev.evtest`. The name must contain "Xbox" or "Controller" for `funstation.py` to pick it up.

## Debugging on the Pi

- Launcher/pad logs: the X session has no journal, so run `DISPLAY=:0 python3 /opt/funstation/funstation.py` by hand after `pkill -f funstation.py`.
- Screen: open the web mirror, or run `DISPLAY=:0 import -window root /tmp/s.png` if imagemagick is installed.
- Boot time: `systemd-analyze` and `systemd-analyze blame`.
- Audio: `wpctl status` (run as the `fun` user).
