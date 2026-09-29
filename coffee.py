"""Making the drink: the customer's order, the recipe, the cup / flavour pickers and the
EKSTRAKSI shot minigame. The bot-facing functions take main.Bot as `bot` and use its
say / check / grab / click_client / set_order / debug_save / scr / enabled.
"""
import time

import cv2
import numpy as np

import config
from ocr import best_words, fuzzy_in, read_line, read_white_text, region_box, squash, white_boxes, whole_match


# ================================================================ order + recipe

def is_order_line(text):
    """'Hi! I'd like a ...' / 'Aku pesan ...': only this sentence names the order, not junk behind it."""
    return max(fuzzy_in(text, m) for m in config.ORDER_MARKS) >= 0.8

def parse_order(text):
    """Customer line -> (drink, syrup); either may be None."""
    if not is_order_line(text):
        return None, None
    drink = best_words(text, config.MENU)
    syrup = best_words(text, {s: [s] for s in config.SYRUPS})
    return drink, syrup

def needs_syrup(drink):
    return "Flavour" in config.RECIPES.get(drink, [])

def complete(drink, syrup):
    """Enough to make the drink: a drink, and its syrup if the recipe takes one."""
    return bool(drink) and (syrup is not None or not needs_syrup(drink))

def recipe_plan(drink, syrup):
    """'Espresso > Milk > Maple > Ice' for the overlay."""
    steps = []
    for s in config.RECIPES.get(drink, []):
        steps.append((syrup or "?") if s == "Flavour" else s)
    return " > ".join(steps)

RECIPE_ACTIONS = {"Espresso": ["Take Beans", "Load Beans", "Pull the Shot"], "Milk": ["Pour the Milk"],
                  "Flavour": ["Pick a Flavour"], "Ice": ["Add Ice"], "Foam": ["Add Foam"], "Water": ["Add Water"]}


def route(drink, syrup):
    """Every panel step of an order as (key, label); key matches main.Step: kind or action."""
    steps = [("ask", "Ask for order"), ("cup", f"Grab a cup{f' ({drink})' if drink else ''}")]
    if drink in config.RECIPES:
        for part in config.RECIPES[drink]:
            for action in RECIPE_ACTIONS[part]:
                label = f"{action} ({syrup or '?'})" if action == "Pick a Flavour" else action
                steps.append((action, f"{label}  @ {config.ACTION_STATION[action]}"))
    else:
        steps.append(("recipe", "Make the drink (order unknown)"))
    return steps + [("serve", "Hand it over")]


def dialogue_text(img):
    """The customer's current line ('Hi! I'd like a ...'); may be junk when no dialogue is up."""
    H, W = img.shape[:2]
    return read_white_text(img, region_box(config.DIALOGUE_REGION, W, H))

def is_dialogue(text):
    """Also true while the line is still typing out ('Hi!I'dlike a Iced Milk': OCR merges words)."""
    return squash(text).startswith(("hi", "halo")) or is_order_line(text) \
        or any(fuzzy_in(text, w) >= 0.8 for w in config.DIALOGUE_WORDS)

def continue_visible(img):
    """'click to continue' / 'klik untuk lanjut' under the dialogue."""
    H, W = img.shape[:2]
    text, _ = read_line(img, region_box(config.CONTINUE_REGION, W, H), 3.0)
    return max(whole_match(text, t) for t in config.CONTINUE_TEXTS) >= config.CONTINUE_CUTOFF

def dialogue_up(img):
    return continue_visible(img) or is_dialogue(dialogue_text(img))


def take_order(bot, timeout=15.0):
    """Let each line finish typing, read it, then click on. Drink and syrup are combined across
    lines. True only if the whole order was heard (a drink, and its syrup if it takes one)."""
    bot.say("listening to the order")
    H, W = bot.scr.rect[3], bot.scr.rect[2]
    x0, y0, x1, y1 = region_box(config.DIALOGUE_REGION, W, H)
    drink = syrup = None
    t0, blank, typing, since = time.time(), 0, None, 0.0
    while time.time() - t0 < timeout:
        bot.check(panel_every=0)
        img = bot.grab()
        text, done = dialogue_text(img), continue_visible(img)
        if not (done or is_dialogue(text)):
            blank += 1
            if blank >= 4 and (drink or time.time() - t0 > 4):
                break
            typing = None
            time.sleep(0.25)
            continue
        blank = 0
        if not done:            # "click to continue" shows once typed out; else wait for the text to hold still
            if squash(text) != typing:
                typing, since = squash(text), time.time()
            if time.time() - since < config.DIALOGUE_STILL:
                time.sleep(0.2)
                continue
        bot.say(f"heard: {text!r}")
        bot.status.heard = text
        d, s = parse_order(text)
        drink, syrup = d or drink, s or syrup
        typing = None
        bot.click_client((x0 + x1) // 2, (y0 + y1) // 2)     # "click to continue"
        time.sleep(0.5)
    if drink:
        bot.set_order(drink, syrup)
    ok = complete(drink, syrup)
    bot.say(f"order: {drink or '?'}{f' + {syrup}' if syrup else ''}{'' if ok else ' (incomplete)'}")
    return ok


# ================================================================ cup + flavour pickers

def find_card(img, aliases):
    """Card in the cup / flavour picker whose label best matches aliases -> (x, y, text)."""
    H, W = img.shape[:2]
    best, best_score = None, 0.75
    for x0, y0, x1, y1 in white_boxes(img, region_box(config.MODAL_REGION, W, H), (90, 190), (100, 190), 0.6):
        text, _ = read_line(img, (x0 + 2, y0 + 0.72 * (y1 - y0), x1 - 2, y1 - 2), 2.0)
        score = max(whole_match(text, a) for a in aliases)
        if score > best_score:
            best, best_score = ((x0 + x1) // 2, (y0 + y1) // 2, text), score
    return best


def pick_card(bot, aliases, what, timeout=4.0):
    """Click the matching card in the cup / flavour picker."""
    bot.say(f"picking {what}")
    t0 = time.time()
    while time.time() - t0 < timeout:
        bot.check(panel_every=0)
        img = bot.grab()
        card = find_card(img, aliases)
        if card:
            bot.debug_save(img, f"pick_{what}", [(card[0] - 40, card[1] - 10, card[0] + 40, card[1] + 10)])
            bot.click_client(card[0], card[1])
            time.sleep(0.6)
            return True
        time.sleep(0.2)
    return False


def grab_cup(bot, drink):
    """Cup picker: click the ordered drink (a wrong cup means an angry customer, XP -25)."""
    return pick_card(bot, config.MENU[drink], drink)


def add_flavour(bot, syrup):
    """Flavour picker: click the ordered syrup."""
    return pick_card(bot, [syrup], syrup)


# ================================================================ EKSTRAKSI shot minigame
# Hold SPACE to lift the needle (cup icon), keep it in the Perfect zone.

# The track is a wide tan -> dark-brown gradient bar in the bottom of the screen.
SEARCH_TOP = 0.6                        # fraction of client height to start looking
TRACK_LO, TRACK_HI = (5, 60, 20), (21, 255, 255)
TRACK_ASPECT = (5.0, 14.0)              # w / h, 400x42 at 1920x1172
TRACK_MIN_W = 0.1                       # fraction of client width
TRACK_FILL = 0.75
TRACK_CENTER_TOL = 0.1                  # |bar centre - screen centre| / width

# HSV ranges measured from the recordings.
ZONE_LO, ZONE_HI = (16, 135, 140), (21, 190, 205)   # flat fill of the Perfect zone
NEEDLE_S, NEEDLE_V = 200, 170                       # orange (in zone) or red (out of zone) cup

LOST_FRAMES = 40          # frames without a needle -> round over
LOOKAHEAD = 0.08          # seconds of velocity prediction
DEADBAND = 0.015          # fraction of track width


def find_track(img):
    """Locate the EKSTRAKSI track in a full client screenshot. Returns (x, y, w, h) or None."""
    H, W = img.shape[:2]
    top = int(SEARCH_TOP * H)
    hsv = cv2.cvtColor(img[top:], cv2.COLOR_BGR2HSV)
    mask = cv2.morphologyEx(cv2.inRange(hsv, TRACK_LO, TRACK_HI), cv2.MORPH_OPEN,
                            np.ones((3, 3), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    best = None
    for x, y, w, h, area in stats[1:]:
        if w < TRACK_MIN_W * W or not TRACK_ASPECT[0] <= w / max(h, 1) <= TRACK_ASPECT[1]:
            continue
        if area < TRACK_FILL * w * h or abs(x + w / 2 - W / 2) > TRACK_CENTER_TOL * W:
            continue                    # the bar is centred; the minimap's tint isn't
        v = hsv[y + h // 4:y + 3 * h // 4, :, 2]
        if np.median(v[:, x:x + w // 20]) < 150 or np.median(v[:, x + w - w // 20:x + w]) > 100:
            continue                    # not light-to-dark
        if best is None or w > best[2]:
            best = (int(x), int(y) + top, int(w), int(h))
    best = best or fixed_track(img)
    if config.DEBUG and best:
        print(f"[coffee] track at {best}")
    return best


def fixed_track(img):
    """The track where the panel always puts it, checked by its look: over a dark brown floor
    the see-through panel matches the track's colours and the blob search above merges them."""
    H, W = img.shape[:2]
    x0, y0, x1, y1 = region_box(config.TRACK_REGION, W, H)
    crop = img[y0:y1, x0:x1]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    h, w = hsv.shape[:2]
    band = hsv[int(.3 * h):int(.7 * h)]
    light = np.median(band[:, int(.08 * w):int(.15 * w), 2]) >= 150        # past the cup at 0%
    dark = np.median(band[:, int(.88 * w):int(.97 * w), 2]) <= 90
    brown = ((band[..., 0] >= 5) & (band[..., 0] <= 22) & (band[..., 1] >= 80)).mean() >= 0.8
    if not (light and dark and brown):
        return None
    needle, zone = locate(crop)                     # a light-to-dark counter top has neither
    return (x0, y0, x1 - x0, y1 - y0) if needle is not None and zone is not None else None


def locate(strip):
    """(needle_x, zone_center) in strip pixels; either may be None."""
    h, w = strip.shape[:2]
    hsv = cv2.cvtColor(strip, cv2.COLOR_BGR2HSV)
    sat = (hsv[..., 1] >= NEEDLE_S) & (hsv[..., 2] >= NEEDLE_V)
    red = (hsv[..., 0] <= 22) | (hsv[..., 0] >= 170)
    needle = (sat & red).astype(np.uint8)
    colsum = needle.sum(axis=0).astype(np.float32)
    needle_x = None
    if colsum.sum() >= 0.004 * h * w:
        needle_x = float((colsum * np.arange(w)).sum() / colsum.sum())

    band = hsv[int(.2 * h):int(.8 * h)]
    zone_cols = cv2.inRange(band, ZONE_LO, ZONE_HI).mean(axis=0) / 255 > 0.4
    zone_cols &= colsum == 0
    # bridge the gap the cup leaves when it sits inside the zone
    gap = int(0.2 * w)
    runs, start, last = [], None, -gap
    for i in np.flatnonzero(zone_cols):
        if start is None or i - last > gap:
            if start is not None:
                runs.append((start, last))
            start = i
        last = i
    if start is not None:
        runs.append((start, last))
    runs = [r for r in runs if 0.08 * w <= r[1] - r[0] <= 0.4 * w]
    zone_c = None
    if runs:
        a, b = max(runs, key=lambda r: r[1] - r[0])
        zone_c = (a + b) / 2
    return needle_x, zone_c


def play(grab, is_running, press, release, timeout=4.0):
    """Play one round. grab(rect) -> BGR of that client rect; grab(None) -> full client.
    press/release(key) send keys; is_running() -> False aborts. True if a round was played."""
    t0 = time.time()
    rect = None
    while rect is None:
        if time.time() - t0 > timeout or not is_running():
            return False
        rect = find_track(grab(None))
    x, y, w, h = rect

    held = False

    def set_key(want):
        nonlocal held
        if want != held:
            (press if want else release)("space")
            held = want

    lost = 0
    prev = None
    needle_v = zone_v = 0.0
    zone_c = None
    last_print = 0.0
    try:
        while is_running():
            strip = grab(rect)
            needle_x, new_zc = locate(strip)
            now = time.perf_counter()
            if needle_x is None:
                lost += 1
                if lost > LOST_FRAMES:
                    break
                continue
            lost = 0
            if new_zc is not None:
                if prev is not None and zone_c is not None:
                    zone_v = 0.6 * zone_v + 0.4 * (new_zc - zone_c) / max(now - prev[0], 1e-3)
                zone_c = new_zc
            if prev is not None:
                needle_v = 0.6 * needle_v + 0.4 * (needle_x - prev[1]) / max(now - prev[0], 1e-3)
            prev = (now, needle_x)

            target = zone_c if zone_c is not None else 0.5 * w
            err = (target + zone_v * LOOKAHEAD) - (needle_x + needle_v * LOOKAHEAD)
            if err > DEADBAND * w:
                set_key(True)          # needle left of the zone: lift it
            elif err < -DEADBAND * w:
                set_key(False)         # needle right of the zone: let it fall back

            if config.DEBUG and now - last_print > 0.5:
                last_print = now
                print(f"[coffee] needle={needle_x:6.1f} zone={zone_c} held={held}")
            time.sleep(0.004)
    finally:
        set_key(False)
    return True


def pull_shot(bot):
    bot.say("pulling the shot (EKSTRAKSI)")

    def grab(rect):
        if rect is None:
            return bot.grab()
        x, y, w, h = rect
        return bot.scr.grab((x, y, x + w, y + h))

    return play(grab, lambda: bot.enabled.is_set() and bot.scr.focused(), bot.hold, bot.release)
