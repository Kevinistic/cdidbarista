"""Recipe step lists, copied from the game's BaristaMenu script (no runtime dependency on it).

Keys match config.MENU (the Indonesian menu names the order OCR returns).
A "route" is the walk between two consecutive stops; stops are step names plus
"Home" (top-left corner after recalibration), "Counter" and "Trash".
Stops are physical places you press E, not game steps.
"""
# In the menu file espresso is Cup, Beans, LoadBeans, Brew, Pour. Physically LoadBeans/Brew/Pour
# are one stop: E at the Brewer starts the minigame, which runs all three. So they merge into "Brew".
_ESPRESSO = ["Cup", "Beans", "Brew"]

RECIPES = {
    "Kopi Hitam":   _ESPRESSO,
    "Cappuccino":   _ESPRESSO + ["Milk", "Flavour", "Foam"],
    "Es Kopi Susu": _ESPRESSO + ["Milk", "Flavour", "Ice"],
    "Americano":    _ESPRESSO + ["Water"],
    "Latte":        _ESPRESSO + ["Milk"],
    "Cokelat Panas": ["Cup", "Milk", "ChocolatePowder"],
    "Mocha":        _ESPRESSO + ["Milk", "ChocolatePowder"],
    "Macchiato":    _ESPRESSO + ["Foam"],
    "Frappuccino":  _ESPRESSO + ["Milk", "Cream", "Ice"],
    "Teh":          ["Cup", "Water", "TeaBag"],
    "Thai Tea":     ["Cup", "Water", "TeaBag", "Milk", "Ice"],
    "Matcha":       ["Cup", "Water", "MatchaPowder", "Milk"],
    "Bubble Tea":   ["Cup", "Water", "TeaBag", "Milk", "Boba", "Ice"],
    "Soda":         ["Cup", "Water", "Carbonate", "Ice"],
    "Lemonade":     ["Cup", "Water", "Carbonate", "LemonSlice", "Ice"],
}


def stops(menu):
    """Full stop list for a drink: Home, every step, then the Counter to serve."""
    return ["Home", *RECIPES[menu], "Counter"]


def route_key(a, b):
    return f"{a}>{b}"


def route_keys(menu):
    s = stops(menu)
    return [route_key(a, b) for a, b in zip(s, s[1:])]


def all_route_keys():
    """Every distinct route needed for all recipes, plus the recovery and startup routes."""
    keys = []
    for menu in RECIPES:
        for k in route_keys(menu):
            if k not in keys:
                keys.append(k)
    for extra in ("Home>Trash", "Home>Counter"):
        if extra not in keys:
            keys.append(extra)
    return keys
