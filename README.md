# cdidbarista
stupid fuck machine learnig projet

clearly wip, for when cdid releases the brand new barista job rework

features: auto read order, auto select cup/syrup, auto coffee minigame

## Installation
1. Install Python 3.11 (MUST BE 3.11!!)
    1. Open Windows Store, install 'Python Install Manager'
    2. Open Commmand Prompt, type `py install 3.11`
2. Click the green "<> Code" button above, then Download ZIP
3. Unzip the project folder
4. Open config.py on Notepad, set `LANG = "id"` or `LANG = "en"` depending on your in-game language.

## Usage
Double-click `run.bat`, Do NOT double-click `main.py`.

F6 to toggle on/off, F7 to terminate. Keybinds changeable in config.py

For manual setup, open a terminal in the project folder and run:

```text
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

currently windows only

## Layout

* `main.py` overlay and process setup, `order.py` order/dialogue OCR, `cup.py` / `syrup.py` picker clicks (both built on `choice.py`), `coffee.py` brew minigame bot.
* Shared helpers: `vision.py` (screen capture + frame-change gate), `textmatch.py` (fuzzy OCR matching), `winutil.py` (game window + mouse input).