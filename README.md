# funstation

A tiny family console on a Raspberry Pi Zero 2 W. Plug it into the projector's USB (power) and HDMI, and it boots straight into a couch launcher:

- **Jellyfin** (web client in a kiosk browser)
- **GBA / GB / GBC games** through mGBA, with box art and no emulator UI

You can drive it two ways:

- **An Xbox controller** over Bluetooth. The stick is a mouse, A clicks, B goes back and the Xbox button always brings you home (it quits the game if one is running).
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
| A | click |
| B | back |
| X | play / pause (Space) |
| Y | right click |
| Menu | Enter |
| View | Esc |
| D-pad | arrow keys |
| Xbox | home (quits game) |

In games, the controller goes directly to mGBA.
