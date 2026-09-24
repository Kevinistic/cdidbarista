DEBUG = False              # saves annotated frames to debug/ and prints decisions

TOGGLE_KEY = "F6"
FORCE_CLOSE_KEY = "F7"

# Roblox default controls: left/right arrows turn the camera, W walks where the camera faces.
TURN_LEFT, TURN_RIGHT = "left", "right"
FORWARD, BACK = "w", "s"
STRAFE_LEFT, STRAFE_RIGHT = "a", "d"
JUMP = "space"
INTERACT = "e"

# ---------------------------------------------------------------- menu / recipes
# canonical name -> every spelling seen in-game (English UI, Indonesian UI)
MENU = {
    "Black Coffee": ["Black Coffee", "Kopi Hitam"],
    "Cappuccino": ["Cappuccino", "Cappucino"],
    "Iced Milk Coffee": ["Iced Milk Coffee", "Es Kopi Susu"],
    "Americano": ["Americano"],             # unlocks at Lv5
    "Latte": ["Latte"],                     # unlocks at Lv5
    # higher-level drinks: Indonesian names only, recipes not seen yet
    "Cokelat Panas": ["Cokelat Panas", "Hot Chocolate"],
    "Mocha": ["Mocha"],
    "Macchiato": ["Macchiato"],
    "Frappuccino": ["Frappuccino"],
    "Teh": ["Teh", "Tea"],
    "Thai Tea": ["Thai Tea"],
    "Matcha": ["Matcha"],
    "Bubble Tea": ["Bubble Tea"],
    "Soda": ["Soda"],
    "Lemonade": ["Lemonade"],
}
SYRUPS = ["Blueberry", "Caramel", "Grape", "Hazelnut", "Mango", "Maple",
          "Mint", "Orange", "Peach", "Raspberry", "Strawberry", "Vanilla"]
DEFAULT_SYRUP = None       # e.g. "Caramel"; None pauses instead of guessing (wrong drink = XP -25)

# From the in-game RECIPE GUIDE. "Flavour" gets substituted with the ordered syrup.
RECIPES = {
    "Black Coffee": ["Espresso"],
    "Cappuccino": ["Espresso", "Milk", "Flavour", "Foam"],
    "Iced Milk Coffee": ["Espresso", "Milk", "Flavour", "Ice"],
    "Americano": ["Espresso", "Water"],
    "Latte": ["Espresso", "Milk"],
}

# Panel line "Next: <action> - at <station> (E)" / "Berikutnya: <action> - di <station> (E)".
ACTIONS = {
    "Take Beans": ["Take Beans", "Ambil Biji"],
    "Load Beans": ["Load Beans", "Taruh Biji"],
    "Pull the Shot": ["Pull the Shot", "Seduh"],
    "Pour the Milk": ["Pour the Milk", "Tuang Susu"],
    "Pick a Flavour": ["Pick a Flavour", "Pilih Rasa"],
    "Add Ice": ["Add Ice", "Tambah Es"],
    "Add Foam": ["Add Foam", "Make Foam", "Buat Foam"],
    "Add Water": ["Add Water", "Pour Water", "Tambah Air", "Tuang Air"],   # guessed, never seen
}
ACTION_STATION = {
    "Take Beans": "Bean Hopper", "Load Beans": "Coffee Maker", "Pull the Shot": "Coffee Maker",
    "Pour the Milk": "Milk", "Pick a Flavour": "Syrup Bottle", "Add Ice": "Ice Machine",
    "Add Foam": "Foam Maker", "Add Water": "Water Tap",
}
# names: the floating label. prompts: text on the chip that appears under it when close.
STATIONS = {
    "Cup Rack": {"names": ["Cup Rack", "Rak Gelas"], "prompts": ["Take a Cup", "Ambil Gelas"]},
    "Bean Hopper": {"names": ["Bean Hopper", "Wadah Biji"], "prompts": ["Take Beans", "Ambil Biji"]},
    "Coffee Maker": {"names": ["Coffee Maker", "Mesin Kopi"],
                     "prompts": ["Use the Machine", "Load Beans", "Pull the Shot",
                                 "Pakai Mesin", "Taruh Biji", "Seduh"]},
    "Milk": {"names": ["Milk", "Susu"],
             "prompts": ["Pour the Milk", "Take the Milk", "Tuang Susu", "Ambil Susu"]},
    "Syrup Bottle": {"names": ["Syrup Bottle", "Botol Sirup"], "prompts": ["Pick a Flavour", "Pilih Rasa"]},
    "Ice Machine": {"names": ["Ice Machine", "Mesin Es"],
                    "prompts": ["Use the Ice Machine", "Add Ice", "Tambah Es"]},
    "Foam Maker": {"names": ["Foam Maker"],
                   "prompts": ["Use the Foam Maker", "Add Foam", "Buat Foam", "Pakai Foam Maker"]},
    "Water Tap": {"names": ["Water Tap", "Keran Air"],
                  "prompts": ["Add Water", "Pour Water", "Tambah Air", "Tuang Air"]},
    "Bin": {"names": ["Bin", "Tong Sampah"], "prompts": ["Bin the Cup", "Buang Gelas"]},
}

# Panel lines without a station, matched by keyword in this order (bin wins: "grab a fresh cup").
PANEL_KEYWORDS = [
    ("bin", ["ruined", "bin it", "rusak", "buang"]),
    ("serve", ["take it to the customer", "antar ke pelanggan"]),
    ("ask", ["go to the customer", "hampiri pelanggan", "tanya pesanan"]),
    ("cup", ["grab a cup", "ambil gelas di rak", "mulai meracik"]),
]
NEXT_WORDS = ["next", "berikutnya"]

# The customer and the bin are never highlighted: found by OCR on their chip text, walking
# toward a landmark label until it shows up.
ASK_PROMPTS = ["Ask for order", "Tanya Pesanan"]
SERVE_PROMPTS = ["Hand over", "Serahkan"]
COUNTER_LANDMARKS = ["Bean Hopper", "Coffee Maker", "Wadah Biji", "Mesin Kopi"]
BIN_LANDMARKS = ["Bin", "Tong Sampah"]
DIALOGUE_WORDS = ["Hi", "Halo", "like", "please", "pesan"]

# ---------------------------------------------------------------- vision
# Screen regions as fractions of the Roblox client area (measured at 1920x1172).
# shared parent (Roblox client viewport)
PARENT_POS_X, PARENT_POS_Y = 0.0, 0.0
PARENT_SIZE_X, PARENT_SIZE_Y = 1.0, 1.0

# Regions laid out like Roblox GUI objects, scale-based and relative to the parent:
# ((anchor_x, anchor_y), (pos_x, pos_y), (size_x, size_y))
PANEL_REGION = ((1.0, 0.0), (1.0, 0.15), (0.20, 0.25))        # BARISTA panel (never click its CANCEL)
DIALOGUE_REGION = ((0.5, 0.0), (0.5, 0.825), (0.50, 0.055))   # "Hi! I'd like a ... please." (one line)
CONTINUE_REGION = ((0.5, 0.0), (0.5, 0.876), (0.20, 0.04))    # "click to continue" under it
MODAL_REGION = ((0.5, 0.5), (0.5, 0.525), (0.50, 0.65))       # cup picker / flavour picker
WORLD_REGION = ((0.0, 0.0), (0.19, 0.08), (0.81, 0.82))       # where world labels can show up
HUD_BLOCK = ((1.0, 0.0), (1.0, 0.0), (0.17, 0.37))            # panel, masked out of WORLD_REGION

CONTINUE_TEXTS = ["click to continue", "klik untuk lanjut"]
CONTINUE_CUTOFF = 0.47

# Target station label highlight (OpenCV HSV). Measured gold ~H20, S100-170, V>240.
HIGHLIGHT_LO = (14, 80, 215)
HIGHLIGHT_HI = (32, 255, 255)

OCR_MIN_CONF = 0.25
MATCH_CUTOFF = 0.6         # fuzzy ratio for station/prompt/menu text

# ---------------------------------------------------------------- navigation tuning
SEEK_TIMEOUT = 10.0        # s of holding TURN_RIGHT before wandering somewhere else
SCAN_STEP = 0.18           # s per turn tap when scanning with (slow) OCR
APPROACH_TIMEOUT = 15.0
CENTER_TOL = 0.12          # |offset| / width before we stop walking and turn first
STEER_TOL = 0.04           # |offset| / width before a steering tap while walking
STEER_GAIN = 0.5           # s of turn per unit offset
LOST_TIMEOUT = 1.2         # s the target may vanish during approach
STUCK_TIMEOUT = 3.0        # s without the label growing before trying to unstick
WALK_BURST = 0.35          # s of walking between OCR checks in text mode
ACTION_WAIT = 3.0          # s to wait for an interaction to change something
