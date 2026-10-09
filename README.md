# obs-snooker

Snooker scoreboard for OBS Studio: a browser overlay for the stream, a scoreboard window for a second monitor,
a touch-friendly control panel, an admin page and a "foul & miss" screenshot comparison tool.

| What | Where |
|------|-------|
| Broadcast overlay (OBS Browser Source) | `http://127.0.0.1:8710/overlay` |
| Control panel (scoring buttons, phone/tablet friendly) | `http://127.0.0.1:8710/control` |
| Admin (players, match setup, score correction, foul & miss control) | `http://127.0.0.1:8710/admin` |

The port (default `8710`) can be changed in the script's properties in OBS.

## Installation on Windows 10 / 11

1. **OBS Studio** 28 or newer (64-bit), from <https://obsproject.com>.
2. **Python 3.11, 64-bit**, from <https://www.python.org/downloads/windows/>.
   During installation tick **"tcl/tk and IDLE"** (needed for the scoreboard window; it is on by default)
   and optionally "Add python.exe to PATH". Use the *same bitness* as OBS (64-bit).
3. Copy this project folder somewhere permanent, e.g. `C:\obs-snooker` (avoid folders that sync or move).
4. Install the dependencies. In *Command Prompt* or *PowerShell*:
   ```
   cd C:\obs-snooker
   py -3.11 -m pip install -r requirements.txt
   ```
   (`pynput` for the keyboard listener, `Pillow` for the semi-transparent screenshot blend in the scoreboard window.)
5. In OBS: **Tools -> Scripts -> Python Settings** -> *Python Install Path* = the folder with `python.exe`,
   e.g. `C:\Users\<you>\AppData\Local\Programs\Python\Python311`
   (find it with `py -3.11 -c "import sys; print(sys.exec_prefix)"`). Restart OBS if it asks.
   The tab should then show "Loaded Python Version: 3.11".
6. **Tools -> Scripts -> "+"** -> select `C:\obs-snooker\obs-snooker.py`.
7. The script log (button "Script Log") should print the overlay, control and admin URLs, and a window
   "Snooker Scoreboard" opens.

### If the scoreboard window does not open

The window is a separate process started with your Python. If auto-detection fails, set
**"Python interpreter (for scoreboard window)"** in the script properties to the full path, e.g.
`C:\Users\<you>\AppData\Local\Programs\Python\Python311\python.exe`, then press "Show scoreboard window".

## Setting up the scene

1. **Overlay:** add a *Browser* source, URL `http://127.0.0.1:8710/overlay`, size `1920x1080`
   (a transparent background is used, leave the custom CSS empty). Place it above the camera.
2. **Scoreboard window:** drag it to the second monitor and press **F11** for fullscreen (Esc to leave).
3. **Control panel:** open `http://127.0.0.1:8710/control` in a browser, or from a phone/tablet on the same Wi-Fi
   using the LAN address printed in the script log (e.g. `http://192.168.1.20:8710/control`).
4. **Windows Firewall:** the first start shows a prompt for OBS / Python - allow **Private networks**,
   otherwise phones cannot connect. The control and admin pages have no password; use them only on a trusted network.

## Using it

- **Control panel:** only the player at the table can score. Balls add to the *break*; the break is added to the
  frame score on "Switch turn". "Wins frame" and "Reset frame score" discard the pending break.
- **Admin:** choose players from the roster (or a custom name), match title, race length; correct scores; manage
  the player roster (name + 3-letter country code, e.g. `CZE`, `ENG`).
- **Hotkeys:** OBS *Settings -> Hotkeys*, search for "Snooker".
- **Foul & miss control** (admin page; shown only when running inside OBS):
  1. Select the *camera source* and opacity, press **Save settings**.
  2. **1. Take reference screenshot** - only stores the camera picture before the disturbance (nothing is shown yet).
  3. **2. Compare** - the scoreboard window shows a fresh screenshot semi-transparently over the reference; press again for a new current picture.
  4. **3. Hide** - back to the scoreboard.
  Screenshots are taken via OBS and read from OBS's *recording* output folder (Settings -> Output -> Recording Path),
  so that folder must exist and be writable.

## Windows 7 / 8

Not officially supported. OBS 28+ requires Windows 10 or newer. The last OBS for Windows 7/8 is 27.2.4 with
Python 3.8 as the newest usable interpreter (last Python for Win 7). The project uses only Python 3.7+ syntax
(`from __future__ import annotations`), so it *may* work there, but it has not been tested, and the screenshot
API used by the foul & miss control may be missing in old OBS versions (the other features don't need it).
Recommended: update to Windows 10/11.

## Troubleshooting

- **"ModuleNotFoundError" in the script log:** OBS's *Python Install Path* is wrong or points to another Python than
  the one where you ran `pip install`.
- **Overlay is blank:** open the URL in a normal browser first; check the port and that the script loaded.
- **Scoreboard window has no blend / only one image in foul control:** Pillow is missing in the Python used for the window.
- **After editing files:** *Tools -> Scripts -> Reload Scripts* is enough (no OBS restart needed).
- **Players are stored in** `players.json` next to `obs-snooker.py`.

## Development

```
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest
python -m obs_snooker.overlay_server        # preview server with demo data
python -m obs_snooker.scoreboard_window --url http://127.0.0.1:8710/api/score
```
