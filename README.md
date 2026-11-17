# Ulanzi Studio for Linux

A FastAPI web app that manages Ulanzi **D200 / D200H (U-Studio)** stream decks on Linux,
modelled on the official Ulanzi Studio app (Windows/macOS only).

- **Device preview**: the 13 LCD keys and the wide info window, with live press feedback from the deck.
- **Action library**: drag actions onto keys (or double-click an action to put it on the selected key).
  - *System*: Hotkey, Hotkey Switch, Open, Launch App, Website, Text, Run Command, Multimedia, Volume,
    Microphone Mute, Lock Screen, Screenshot
  - *Navigation*: Folders (with an automatic Back key), Next/Previous/Go-to Page, Switch Profile, Brightness
  - *Multi Action*: Multi Action, Multi Action Switch, Delay
  - *OBS Studio* (WebSocket v5): Scene, Record, Stream, Mute Source, Virtual Camera
- **Icons**: icon editor with 2,000+ Font Awesome icons, colours, your own images, or the icons of installed apps.
- **Titles**: per-key title with size, colour, position and bold. The deck firmware draws the titles.
- **Two-state keys** for toggle actions, plus an optional **long-press** action on any key.
- **Profiles and pages**: add, rename, reorder (drag), duplicate, import and export (`.ulanzi.json` with icons embedded).
- **Info window**: analog clock, digital clocks, CPU/RAM/GPU stats, or a 458×196 background image. Tapping it can run an action.
- **Device settings**: brightness, screen sleep, 12/24 h clock, long-press threshold.
- Right-click a key for Copy, Cut, Paste, Clear and Run. Drag keys to swap them, or Ctrl-drag to copy.
  Drop a key on a page tab to move it to that page.

## Requirements

- Linux with Python ≥ 3.12 and [uv](https://docs.astral.sh/uv/)
- `hidapi` (`libhidapi-hidraw0` on Debian/Ubuntu; installed with the Python wheel in most cases)
- Optional tools used by some actions: `xdg-open`, `gtk-launch`, `wpctl` or `pactl` (audio), `loginctl`,
  and `wl-copy`/`xclip` or KDE Klipper (for the *Text → paste* mode)
- A USB 2.0 hub, if your machine fails to detect the deck (see below)

## Installation

```bash
git clone <this repository> ulanzi-linux
cd ulanzi-linux
uv sync                      # add  --extra obs  to use the OBS Studio actions
```

### 1. udev rules (access to the deck and to the virtual keyboard)

```bash
uv run ulanzi-linux udev-rules | sudo tee /etc/udev/rules.d/70-ulanzi.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Then unplug and replug the deck. The file number must be **below 73**: systemd applies `uaccess`
ACLs in `73-seat-late.rules`, so a `99-` file adds the tag too late. The rules also give the
`plugdev` group access as a fallback. The `uinput` rule lets the app send hotkeys and text through a
virtual keyboard. That's the only method that works on **Wayland** (KDE, GNOME and others).
Without it, the app falls back to `xdotool`, which only reaches X11/XWayland windows.

Check it worked: `getfacl /dev/hidrawN` (the deck's node) should list your user with `rw-`.

### 2. If `lsusb` shows only `18d1:d002 Google Inc. Nexus 4 (debug)`

The deck contains an internal hub with two USB devices:

| VID:PID     | What it is                                         |
|-------------|----------------------------------------------------|
| `2207:0019` | the real HID device (deck protocol): **needed**    |
| `18d1:d002` | an unused, ADB-shaped decoy                        |

On many Linux xHCI controllers the HID half fails to enumerate when the deck is plugged in directly
(`dmesg`: `can't read configurations, error -22`). The known workaround is to **connect the deck
through a USB 2.0 hub** (a cheap unpowered one works). After that, `lsusb | grep 2207` should list it,
and the app connects automatically. The app shows a banner when it detects this situation.

## Running

```bash
uv run ulanzi-linux --open   # serves http://127.0.0.1:8765 and opens your browser
```

Options: `--host`, `--port`, `-v` (debug logging). The deck is detected automatically and
reconnected if it is unplugged; the layout is re-sent whenever it reconnects.

To start it automatically on login (the server keeps handling key presses with no browser open):

```bash
uv run ulanzi-linux install-service
systemctl --user daemon-reload && systemctl --user enable --now ulanzi-linux
journalctl --user -u ulanzi-linux -f      # logs
```

## Using the app

The window has four parts: the **top bar** (device status, profile, brightness, settings),
the **deck preview** in the centre with **page tabs** below it, the **action library** on the right,
and the **property inspector** at the bottom.

**Assign an action**: drag it from the library onto a key, or select a key and double-click the
action. The key gets a default icon, and the inspector shows the action's settings. Changes save
automatically and appear on the deck right away.

**Edit a key**
- *Title*: type in the Title field. The **A** button opens the style options (show/hide, size,
  colour, bold, top/middle/bottom).
- *Icon*: click the large key preview in the inspector. Pick from the icon library (with background
  and glyph colours and size), upload an image (PNG/JPG/GIF/WebP/SVG), or use an installed app's icon.
- *Two-state keys* (Hotkey Switch, Mic Mute, OBS Record…) have a State tab for each state, each with
  its own icon and title. The deck shows the current state.
- *Long press*: choose a second action that runs when the key is held (default 500 ms, adjustable in Settings).
- **▶ Test** (or double-click the key) runs the action from the browser.

**Organise keys**
- Drag a key onto another key to swap them; hold **Ctrl** while dragging to copy.
- Drop a key on a page tab to move it to that page.
- Right-click a key, or use **Ctrl+C / Ctrl+X / Ctrl+V / Delete**, to copy, cut, paste or clear it.

**Pages and folders**: **+ Page** adds a page. Double-click a tab to rename it, drag tabs to reorder
them, and use ✕ to delete one. Put *Next/Previous Page* keys on the deck to move between pages.
A *Create Folder* key opens a sub-page whose first key is **Back**. Use **Open folder →** in the
inspector to edit the folder's contents.

**Info window** (the wide display): click it to choose an analog clock, a digital clock, CPU/RAM/GPU
stats or a background image, and optionally an action to run when it's tapped.

**Profiles**: switch with the Profile menu. The **⋯** button creates, renames, duplicates, exports,
imports or deletes profiles. Exports are single `.ulanzi.json` files with the icons embedded, so you
can back them up or share them. A *Switch Profile* key changes profiles from the deck.

**Settings** (gear icon): screen sleep timeout (any key press wakes it), long-press duration, OBS
WebSocket connection (OBS → Tools → WebSocket Server Settings), keyboard backend status, and
**Re-send layout to device**.

**Hotkey field**: click it and press the shortcut. The **Aa** button switches to typing it as text,
e.g. `ctrl+alt+t`, `super+e`, `ctrl+shift+esc`, `f5`, `playpause`, `volup`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Banner: *only 18d1:d002* | Connect through a USB 2.0 hub (see above). |
| *could not open it (…)* | udev rule missing or named `99-…`; install `70-ulanzi.rules` and replug. |
| Hotkeys don't reach Wayland apps | `/dev/uinput` not accessible; install the rules and restart the app. |
| Deck shows its default layout | Settings → *Re-send layout to device*, or replug. |
| OBS actions fail | Run `uv sync --extra obs` and check the host, port and password in Settings. |

## How it works

```
browser UI ──REST/WebSocket──> FastAPI (web.py)
                                  │
                         Controller (controller.py) ── actions.py ── keyboard.py (uinput / xdotool)
                                  │
                         UlanziDevice thread (device.py) ── hidapi ── /dev/hidrawN
                                  │
                         protocol.py: 1024-byte reports, layout ZIP (manifest.json + Images/*.png)
```

- Configuration lives in `~/.config/ulanzi-linux/`: `settings.json`, `profiles/*.json`, and `icons/`
  (content-addressed PNGs). Set `ULANZI_CONFIG_DIR` to use a different location.
- Protocol notes come from community reverse-engineering:
  [redphx/strmdck](https://github.com/redphx/strmdck),
  [racerxdl/ulanzi-d200-linux](https://github.com/racerxdl/ulanzi-d200-linux) and
  [jcalado/companion-surface-d200](https://github.com/jcalado/companion-surface-d200).
  The firmware takes a ZIP with a `manifest.json` that maps `"col_row"` slots to icons and titles, sent
  in 1024-byte HID reports. Uploads are bracketed by `GETBASE` (0x0003) barriers. The ZIP is re-packed
  until no continuation chunk starts with `0x00` or `0x7c`, which works around a firmware bug.
- Adding an action means adding one decorated function in `actions.py`. The UI builds its property
  form from the declared `fields`.

Icons: [Font Awesome Free](https://fontawesome.com) (icons CC BY 4.0, fonts SIL OFL 1.1), bundled in
`static/vendor/fontawesome`.

## License

[MIT](LICENSE) © 2026 Flávio Codeço Coelho. The bundled Font Awesome Free files keep their own
licenses (icons CC BY 4.0, fonts SIL OFL 1.1); see `src/ulanzi_linux/static/vendor/fontawesome/LICENSE.txt`.
