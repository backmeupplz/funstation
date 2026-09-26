#!/bin/sh
# Paired devices that don't reconnect on their own (speakers) get poked until they're connected.
while sleep 15; do
  for mac in $(bluetoothctl devices Paired | cut -d' ' -f2); do
    info=$(bluetoothctl info "$mac")
    echo "$info" | grep -q 'Icon: audio' && echo "$info" | grep -q 'Connected: no' && timeout 10 bluetoothctl connect "$mac" >/dev/null
  done
done
