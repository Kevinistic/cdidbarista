# cdidbarista
stupid fuck machine learnig projet

clearly wip, for when cdid releases the brand new barista job rework

## Installation
1. Click the green "<> Code" button, then Download ZIP
2. Unzip the project folder
3. Install Python 3.10 or newer from https://www.python.org/downloads/ if needed

## Usage
Double-click `run.bat`. The first launch creates `.venv` and installs the required packages; later launches reuse it.

Do not double-click `main.py`, because Windows may use a different Python installation instead of the project's virtual environment.

For manual setup, open a terminal in the project folder and run:

```text
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

F6 to toggle on/off, F7 to terminate. Keybinds changeable in config.py

Windows and Linux are supported.

## Linux (X11 / Sober)

Target: Linux Mint XFCE, X11 session, Roblox through [Sober](https://sober.vinegarhq.org/).
Native Wayland is not supported (compositors hide window geometry/focus and block input injection).

```text
sudo apt install python3-venv python3-tk
bash run.sh
```

* The game window is found by title/class `sober` (`GAME_WINDOW_MATCH` in config.py).
  Check what yours reports with `xprop WM_CLASS _NET_WM_NAME` and click the game.
* Run `.venv/bin/python selftest.py` first (add `--walk` to test key injection, `--hotkey` to test F6).
  It saves `selftest_window.png` / `selftest_hint.png` so you can see exactly what the bot captures.
* Keys/mouse go through the X server's XTEST extension, no root needed. If held movement keys
  stutter (X key auto-repeat), `pip install evdev`, set `LINUX_INPUT = "uinput"` in config.py and give
  your user write access to `/dev/uinput` (`sudo usermod -aG input $USER`, plus a udev rule).
* If the capture is black, turn off the compositor: Settings > Window Manager Tweaks > Compositor.
* `measurements.json` routes were recorded on Windows; re-record with `python calibrate.py`
  (walk timing differs per machine and per client).
* F6/F7 are grabbed globally on X11, so the game does not receive them.

Windows and Linux share all game logic; OS specifics live in `backend/` (`windows.py`, `x11.py`).