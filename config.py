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

# Barista panel + Hint label layout (Roblox UI values, pixels are pre-UI_SCALE).
# Hint is a descendant of Barista; its text reads "Next: <step> - at <station> (E)".
BARISTA_ANCHOR = (1.0, 0.5)
BARISTA_POS    = (0.998, 0.218)     # scale of the parent ScreenGui
BARISTA_SIZE   = (235, 170)         # px
HINT_ANCHOR    = (0.0, 0.0)
HINT_POS       = (10, 86)           # px, inside Barista
HINT_SIZE      = ((1.0, -20), (0.0, 34))   # ((scale_x, offset_x), (scale_y, offset_y))

# Mean pixel diff (0-255 scale) below which OCR is skipped on a static frame.
# Raise if OCR is being re-triggered on near-identical frames; lower if changes are missed.
FRAME_DIFF_THRESHOLD = 3.0

# --- runner (hardcoded route replay) ---
ROUTES_FILE = "measurements.json"      # routes recorded with calibrate.py live under "routes"
HINT_CHANGE_THRESHOLD = 2.0            # mean gray diff (64x16 thumbnail) that counts as "hint changed"
STEP_TIMEOUT = 20.0                    # seconds to wait for the hint to change after an E hold (pickers/minigames included)
HINT_SETTLE = 0.15                     # seconds to let the new hint text finish rendering

TAKE_E_HOLD = 0.4                      # E hold to ask the customer's order (prompt Hold is 0.35s; extra E could advance the dialog)
TAKE_RETRY = 4.0                       # seconds between attempts to take an order while waiting at the counter
SERVE_TAP_W = 0.3                      # W tap after serving: pushes into the top wall to re-anchor y (next order starts with `home` anyway)

# --- platform ---
# Case-insensitive substrings matched against the game window's title (and WM_CLASS on Linux).
# Sober's X11 window is titled "Sober" (Roblox's own client is "Roblox").
GAME_WINDOW_MATCH = {"win32": ("roblox",), "linux": ("sober",)}
# Linux key injection: "xtest" (X server extension, no setup) or "uinput" (kernel level, needs the evdev
# package and write access to /dev/uinput; avoids X key auto-repeat on held movement keys).
LINUX_INPUT = "xtest"
