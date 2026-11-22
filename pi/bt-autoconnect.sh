#!/bin/sh
# Keeps the Pi on its best links, every 15 s:
# - paired devices that don't reconnect on their own (speakers) get poked until they're connected;
# - Wi-Fi goes back to the highest-priority network (5 GHz) once it's in range. NetworkManager never leaves a
#   working network for a better one, so after a 5 GHz dropout (seen: 04:00, an hour after the nightly reboot) the
#   Pi stayed on the 2.4 GHz fallback all day, where Wi-Fi and Bluetooth share the airwaves: buffering video and
#   laggy, doubled controller presses.
while sleep 15; do
  for mac in $(bluetoothctl devices Paired | cut -d' ' -f2); do
    info=$(bluetoothctl info "$mac")
    echo "$info" | grep -q 'Icon: audio' && echo "$info" | grep -q 'Connected: no' && timeout 10 bluetoothctl connect "$mac" >/dev/null
  done
  best=$(nmcli -t -f AUTOCONNECT-PRIORITY,TYPE,NAME con show | grep ':802-11-wireless:' | sort -t: -k1,1nr | head -1 | cut -d: -f3-)
  active=$(nmcli -t -f TYPE,NAME con show --active | grep '^802-11-wireless:' | cut -d: -f2-)
  if [ -n "$active" ] && [ "$active" != "$best" ] &&
      nmcli -g SSID dev wifi list | grep -qxF "$(nmcli -g 802-11-wireless.ssid con show "$best")"; then
    timeout 60 nmcli con up "$best" >/dev/null
  fi
done
