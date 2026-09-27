#!/bin/bash
# Runs on the Pi as root (install.sh does this). Idempotent: safe to re-run after any change.
set -euo pipefail
cd "$(dirname "$0")"
. ./funstation.env
U=${FUN_USER:-fun}
H=$(getent passwd "$U" | cut -d: -f6)

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends \
  xserver-xorg-core xserver-xorg-input-libinput xinit x11-xserver-utils matchbox-window-manager unclutter-xfixes \
  chromium chromium-sandbox rpi-chromium-mods mgba-sdl python3-evdev fonts-noto-color-emoji \
  x11vnc novnc python3-websockify \
  pipewire pipewire-pulse pipewire-alsa wireplumber libspa-0.2-bluetooth bluez
command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh

# app
install -d /opt/funstation/ui
install -m755 funstation.py xinitrc bt-autoconnect.sh /opt/funstation/
install -m644 ui/* /opt/funstation/ui/
install -m644 funstation.env /etc/funstation.env

# Chromium: Alt+Home (sent on Xbox button) returns to the launcher
install -d /etc/chromium/policies/managed
echo '{"HomepageLocation":"http://localhost:8080","HomepageIsNewTabPage":false,"TranslateEnabled":false,"PasswordManagerEnabled":false}' \
  > /etc/chromium/policies/managed/funstation.json

# mGBA: fullscreen, correct 3:2 shape, no pause when focus changes
install -d -o "$U" -g "$U" "$H/.config" "$H/.config/mgba"
# Xbox pad (SDL button numbers): A/B -> A/B, LB/RB -> L/R, View -> Select, Menu -> Start, D-pad + left stick -> directions
install -m644 -o "$U" -g "$U" /dev/stdin "$H/.config/mgba/config.ini" <<'EOF'
[ports.sdl]
fullscreen=1
lockAspectRatio=1
pauseOnFocusLost=0

[gba.input.SDLB]
keyA=0
keyB=1
keyL=6
keyR=7
keySelect=10
keyStart=11
keyUp=-1
keyDown=-1
keyLeft=-1
keyRight=-1
hat0Up=6
hat0Down=7
hat0Left=5
hat0Right=4
axisLeftAxis=-0
axisLeftValue=-12288
axisRightAxis=+0
axisRightValue=12288
axisUpAxis=-1
axisUpValue=-12288
axisDownAxis=+1
axisDownValue=12288
EOF
install -d -o "$U" -g "$U" "$H/roms"

# controller -> virtual mouse/keyboard needs uinput
echo uinput > /etc/modules-load.d/uinput.conf
echo 'KERNEL=="uinput", GROUP="input", MODE="0660"' > /etc/udev/rules.d/99-uinput.rules
usermod -aG input,video,audio,render,bluetooth "$U"

# autologin on tty1 straight into X
raspi-config nonint do_boot_behaviour B2
cat > "$H/.bash_profile" <<'EOF'
[ -z "$DISPLAY" ] && [ "$(tty)" = /dev/tty1 ] && exec startx /opt/funstation/xinitrc -- -nolisten tcp vt1 >/dev/null 2>&1
EOF
chown "$U:$U" "$H/.bash_profile"

# web mirror: noVNC on localhost:6080, exposed on the tailnet over HTTPS by tailscale serve
echo '<meta http-equiv="refresh" content="0;url=vnc.html?autoconnect=1&resize=scale&reconnect=1">' \
  > /usr/share/novnc/index.html
cat > /etc/systemd/system/funstation-web.service <<'EOF'
[Unit]
Description=funstation noVNC mirror
After=network.target
[Service]
ExecStart=/usr/bin/websockify --web /usr/share/novnc 127.0.0.1:6080 127.0.0.1:5900
Restart=always
User=nobody
[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/funstation-bt.service <<'EOF'
[Unit]
Description=funstation bluetooth speaker autoconnect
After=bluetooth.service
[Service]
ExecStart=/opt/funstation/bt-autoconnect.sh
Restart=always
[Install]
WantedBy=multi-user.target
EOF
# Xbox pads fail BLE pairing with "Authentication Failed" unless just-works re-pairing is allowed
sed -i 's/^#\?FastConnectable.*/FastConnectable = true/; s/^#\?JustWorksRepairing.*/JustWorksRepairing = always/; s/^#\?AutoEnable.*/AutoEnable = true/' /etc/bluetooth/main.conf
rfkill unblock bluetooth
systemctl daemon-reload
systemctl enable --now funstation-web funstation-bt

# fast boot: quiet kernel, no first-boot/cloud/update machinery
touch /etc/cloud/cloud-init.disabled
sed -i 's/console=serial0,115200 //; s/ quiet loglevel=3 logo.nologo//; s/$/ quiet loglevel=3 logo.nologo/' /boot/firmware/cmdline.txt
for u in NetworkManager-wait-online ModemManager e2scrub_reap apt-daily.timer apt-daily-upgrade.timer man-db.timer e2scrub_all.timer; do
  systemctl disable --now "$u" 2>/dev/null || true
done

if tailscale status >/dev/null 2>&1; then
  tailscale serve --bg --https=443 http://127.0.0.1:6080 >/dev/null
fi
pkill -f "^python3 /opt/funstation/funstation.py" || true  # xinitrc respawns it with the new code
echo "setup done"
