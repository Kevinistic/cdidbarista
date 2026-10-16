DEBUG = False

UI_SCALE = 2.0

TOGGLE_KEY = "F6"
FORCE_CLOSE_KEY = "F7"

# Key pressed to close the customer's dialogue once the order has been read.
DIALOGUE_KEY = "e"

# Case-insensitive substring matched against the game window's title.
GAME_WINDOW_TITLE = "roblox"

# Game language, "id" or "en"
LANG = "id"

_LOCALES = {
    "id": {
        "MENU": ["Kopi Hitam", "Cappuccino", "Es Kopi Susu", "Americano", "Latte",
                 "Cokelat Panas", "Mocha", "Macchiato", "Frappuccino",
                 "Teh", "Thai Tea", "Matcha", "Bubble Tea", "Soda", "Lemonade"],
        "PROMPT_TEXT": "klik untuk lanjut",         # dialog_hint
        "PICK_TITLE_MENU": "PESANANNYA APA?",       # pick_title_Menu
        "PICK_TITLE_FLAVOUR": "PILIH RASA",         # pick_title_Flavour
        # "Halo! Aku pesan [menu] ya." / "Halo! Aku pesan [menu], rasa [syrup] ya."
        "ORDER_PATTERN": r"pesan\s+(.+?)\s+ya\b",
        "OCR_LANGS": ["id", "en"],
    },
    "en": {
        "MENU": ["Black Coffee", "Cappuccino", "Iced Milk Coffee", "Americano", "Latte",
                 "Hot Chocolate", "Mocha", "Macchiato", "Frappuccino",
                 "Tea", "Thai Tea", "Matcha", "Bubble Tea", "Soda", "Lemonade"],
        "PROMPT_TEXT": "click to continue",
        "PICK_TITLE_MENU": "WHAT DID THEY ORDER?",
        "PICK_TITLE_FLAVOUR": "PICK A FLAVOUR",
        # "Hi! I'd like a [menu] please." / "Hi! I'd like a [menu] with [syrup] syrup."
        "ORDER_PATTERN": r"like\s+an?\s+(.+?)(?:\s+please\b|[.!]|$)",
        "OCR_LANGS": ["en"],
    },
}

if LANG not in _LOCALES:
    raise ValueError(f"config.LANG must be one of {sorted(_LOCALES)}, got {LANG!r}")

_L = _LOCALES[LANG]
MENU = _L["MENU"]
PROMPT_TEXT = _L["PROMPT_TEXT"]
PICK_TITLE_MENU = _L["PICK_TITLE_MENU"]
PICK_TITLE_FLAVOUR = _L["PICK_TITLE_FLAVOUR"]
ORDER_PATTERN = _L["ORDER_PATTERN"]
OCR_LANGS = _L["OCR_LANGS"]

SYRUPS =["Blueberry", "Caramel", "Grape", "Hazelnut", "Mango", "Maple",
          "Mint", "Orange", "Peach", "Raspberry", "Strawberry", "Vanilla"]

# written by order.py, read by cup.py (and later syrup/ml)
ORDER = {"menu": None, "syrup": None}

# Mean pixel diff (0-255 scale) below which OCR is skipped on a static frame.
# Raise if OCR is being re-triggered on near-identical frames; lower if changes are missed.
FRAME_DIFF_THRESHOLD = 3.0