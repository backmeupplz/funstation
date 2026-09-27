# funstation

A tiny family console on a Raspberry Pi 4/400. Plug it into the projector's USB (power) and HDMI, and it boots straight into a couch launcher:

- **Jellyfin** (web client in a kiosk browser)
- **Music** from Navidrome (its web player)
- **GBA / GB / GBC games** through mGBA, with box art and no emulator UI

You can drive it two ways:

- **An Xbox controller** over Bluetooth. The stick is a mouse and the D-pad navigates. The Xbox button sends the current app to the background (paused) and brings you home; press it again to jump back in. Y closes a running app from the home screen.
- **A browser**: `https://funstation.<your-tailnet>.ts.net` mirrors the screen, with full mouse and keyboard.

A paired Bluetooth speaker connects automatically and takes over the audio.

## Setup

```sh
cp config.example.env config.env   # fill in
./install.sh                       # re-run after any change
```

See [AGENTS.md](AGENTS.md) for the full from-scratch procedure: flashing, Tailscale and Bluetooth pairing.

## Controller

| Button | Action |
|---|---|
| Left stick | mouse |
| Right stick | scroll |
| A | select (D-pad highlight) or click (stick pointer) |
| B | back |
| X | play / pause (Space) |
| Y | close app (home screen) / right click |
| Menu | Enter |
| View | Esc |
| D-pad | arrow keys |
| LB / RB | volume down / up |
| Xbox (tap) | home, app keeps running paused; again to resume |
| Xbox (hold) + D-pad up/down | volume, works in games too |

In games, the controller goes directly to mGBA: A/B, LB/RB → L/R, View → Select, Menu → Start, and the D-pad or left stick for directions.
