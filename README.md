# cdidbarista
stupid fuck machine learnig projet

Bot for the reworked barista job in CDID (Roblox). It reads the BARISTA quest panel and does what it says. Works with the game set to English or Indonesian.

## How it works
- **Panel.** OCR reads the instruction line above the panel's red CANCEL button, for example `Next: Take Beans - at Bean Hopper (E)`. The panel decides every step. If it ever says the drink is ruined, the bot drops whatever it was doing and bins the cup.
- **Stations.** The bot holds the right arrow until the target station's **gold label** is on screen, then walks at it (W, steering with the arrows) until the prompt chip under the label shows up. It then clicks that chip, or presses E if the chip is the E one. A chip is only used when its text matches the target station, because using the wrong station ruins the drink.
- **Customer and bin.** These aren't highlighted, so the bot turns until their chip text (`Ask for order`, `Hand over`, `Bin the Cup`) is on screen. If none is in reach, it walks toward a landmark label.
- **Order.** The bot reads the customer's line (`Hi! I'd like a Iced Milk Coffee with Maple syrup`) once and remembers it, because a customer can't be asked twice. It then clicks the matching card in the cup picker and, if the recipe needs one, the flavour picker. The overlay shows the recipe with the flavour filled in.
- **Shot minigame.** `coffee.py` finds the EKSTRAKSI bar and holds or releases SPACE to keep the cup needle in the Perfect zone.

The "ML" is EasyOCR's neural text recogniser plus OpenCV colour and shape detection; nothing is trained. `GAME_NOTES.md` (gitignored) records everything observed from the reference videos.

## Installation
1. Click the green "<> Code" button, then Download ZIP
2. Unzip the project folder
3. Install Python 3.10 or newer from https://www.python.org/downloads/ if needed

## Usage
1. Start the barista job in game and stand near the counter.
2. Double-click `run.bat`, then click back into Roblox. The bot only acts while Roblox is the focused window. The first launch creates `.venv` and installs the required packages; later launches reuse it.
3. F6 pauses and resumes, F7 quits. Keybinds and tuning are in `config.py`.

Do not double-click `main.py`, because Windows may use a different Python installation instead of the project's virtual environment.

For manual setup, open a terminal in the project folder and run:

```text
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

If a customer's order wasn't caught, the overlay says so. Pick the cup or flavour yourself and the bot carries on once the panel moves on.

Set `DEBUG = True` in `config.py` to print decisions and save annotated frames to `debug/`. To check the vision without playing, run it on screenshots:

```
.venv\Scripts\python main.py --test shot1.png shot2.png   # annotated copies go to debug/
```

Assumes Roblox's default controls: left/right arrows turn the camera and W walks. It was tuned from 1920x1172 recordings; different UI scales should mostly adapt.

currently windows only
