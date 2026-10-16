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

Set `LANG = "id"` or `LANG = "en"` in config.py to match the language the Roblox game is set to (menu names, dialogue and picker titles are read from the screen in that language).

currently windows only

## Layout

* `main.py` overlay and process setup, `order.py` order/dialogue OCR, `cup.py` / `syrup.py` picker clicks (both built on `choice.py`), `coffee.py` brew minigame bot.
* Shared helpers: `vision.py` (screen capture + frame-change gate), `textmatch.py` (fuzzy OCR matching), `winutil.py` (game window + mouse input).