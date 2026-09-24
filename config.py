DEBUG = False

UI_SCALE = 2.0

TOGGLE_KEY = "F6"
FORCE_CLOSE_KEY = "F7"

MENU = ["Kopi Hitam", "Cappuccino", "Es Kopi Susu", "Americano", "Latte", 
            "Cokelat Panas", "Mocha", "Macchiato", "Frappuccino",
            "Teh", "Thai Tea", "Matcha", "Bubble Tea", "Soda", "Lemonade"]
SYRUPS = ["Blueberry", "Caramel", "Grape", "Hazelnut", "Mango", "Maple",
          "Mint", "Orange", "Peach", "Raspberry", "Strawberry", "Vanilla"]

# written by order.py, read by cup.py (and later syrup/ml)
ORDER = {"menu": None, "syrup": None}

# Mean pixel diff (0-255 scale) below which OCR is skipped on a static frame.
# Raise if OCR is being re-triggered on near-identical frames; lower if changes are missed.
FRAME_DIFF_THRESHOLD = 3.0