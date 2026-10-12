"""Reading the Barista panel's hint label: where it is, what it says, whether it changed.

    python hint.py     # Roblox open, Barista panel visible: saves hint_debug.png and prints what OCR reads
"""
import enum

import cv2
import numpy as np

import backend
import textmatch
import vision
from config import (BARISTA_ANCHOR, BARISTA_POS, BARISTA_SIZE, HINT_ANCHOR, HINT_CHANGE_THRESHOLD,
                    HINT_POS, HINT_SIZE, UI_SCALE)
from order import ALLOWLIST, MIN_CONFIDENCE, ocr_readtext, prepare_for_ocr

CANCEL_BELOW_HINT = 48  # ui px from the hint's top edge down to the cancel button's top edge (134 - 86, from a screenshot)
CANCEL_BUTTON_UI_H = 32  # the red BATALKAN/CANCEL button is 215 x 32 ui px

RUINED_TEXTS = ("Rusak. Buang di Tong Sampah (E), lalu ambil gelas baru",
                "Ruined. Bin it (E), then grab a fresh cup")
RUINED_CUTOFF = 0.6
# While the brew minigame runs the hint reads "Berikutnya: Seduh - di Mesin Kopi (E)" (step_Brew).
# Only when it stops saying "Seduh" is the minigame over.
BREW_STEP_TEXTS = ("Seduh", "Pull the Shot")
# Positive evidence the brew is over: the serve hint, the ruined hint, or a hint naming any other station.
# (Not-"Seduh" is not enough: the minigame may cover the hint, which OCRs as garbage.)
NEXT_HINT_TEXTS = ("Berikutnya", "Next")  # prefix of hint_next: a cup with a recipe in progress is in hand
SERVE_TEXTS = ("Antar ke pelanggan di kasir", "Take it to the customer at the counter")
NEXT_STATION_TEXTS = ("Foam Maker", "Susu", "Milk", "Mesin Es", "Ice Machine", "Botol Sirup", "Syrup Bottle",
                      "Keran Air", "Water Tap", "Toples Cokelat", "Chocolate Jar", "Toples Matcha", "Matcha Jar",
                      "Kotak Teh", "Tea Box", "Panci Boba", "Boba Pot", "Dispenser Krim", "Cream Dispenser",
                      "Karbonator", "Carbonator", "Talenan Lemon", "Lemon Board")
CUP_TEXTS = ("Ambil gelas di rak", "Grab a cup from the rack")
TAKE_TEXTS = ("Hampiri pelanggan di kasir", "Go to the customer at the counter")


# ------------------------------------------------------------------ where the hint is
def _computed_hint_region(window, s=UI_SCALE):
    """Hint label from the Roblox UI values. Can be off vertically when the ScreenGui respects the topbar inset."""
    bw, bh = BARISTA_SIZE[0] * s, BARISTA_SIZE[1] * s
    bl = window["left"] + window["width"] * BARISTA_POS[0] - bw * BARISTA_ANCHOR[0]
    bt = window["top"] + window["height"] * BARISTA_POS[1] - bh * BARISTA_ANCHOR[1]
    (sx, ox), (sy, oy) = HINT_SIZE
    hw = bw * sx + ox * s
    hh = bh * sy + oy * s
    return {
        "left": int(bl + HINT_POS[0] * s - hw * HINT_ANCHOR[0]),
        "top": int(bt + HINT_POS[1] * s - hh * HINT_ANCHOR[1]),
        "width": int(hw),
        "height": int(hh),
    }


def _find_cancel_button(window, s):
    """Locate the red BATALKAN/CANCEL button (same left edge and width as the hint) on screen."""
    area = {"left": window["left"] + window["width"] // 2, "top": window["top"],
            "width": window["width"] - window["width"] // 2, "height": window["height"]}
    img = vision.to_bgr(vision.grab(area))
    mask = cv2.inRange(img, (0, 0, 201), (109, 109, 255))  # BGR: r > 200, g < 110, b < 110
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    exp_w, exp_h = BARISTA_SIZE[0] + HINT_SIZE[0][1], CANCEL_BUTTON_UI_H  # 215 x 32 ui px
    best = None
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if 0.8 * exp_w * s <= w <= 1.2 * exp_w * s and 0.6 * exp_h * s <= h <= 1.4 * exp_h * s:
            if best is None or w * h > best[2] * best[3]:
                best = (x, y, w, h)
    if best is None:
        return None
    return area["left"] + best[0], area["top"] + best[1]


_region_cache = (None, None)  # (window geometry, hint region) of the last successful cancel-button lookup


def hint_region(window, s=UI_SCALE):
    """Hint label in absolute screen coordinates.

    The red cancel button sits a fixed distance below the hint with the same left edge,
    so it is used as the anchor (robust to the topbar inset / window layout). Falls back
    to the computed region if the button is not found. Result cached per window geometry.
    """
    global _region_cache
    geo = (window["left"], window["top"], window["width"], window["height"])
    if _region_cache[0] == geo:
        return _region_cache[1]
    base = _computed_hint_region(window, s)
    try:
        found = _find_cancel_button(window, s)
    except Exception:
        found = None
    if found is None:
        return base  # not cached: retry on the next call
    cx, cy = found
    region = dict(base, left=cx, top=int(cy - CANCEL_BELOW_HINT * s))
    _region_cache = (geo, region)
    return region


def grab_hint(window):
    return vision.to_bgr(vision.grab(hint_region(window)))


# ------------------------------------------------------------------ did it change
def thumb(frame):
    """64x16 grayscale thumbnail used to compare hint frames."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, (64, 16), interpolation=cv2.INTER_AREA).astype(np.float32)


def changed_from(frame, baseline_thumb):
    """Has `frame` moved away from the frame that produced `baseline_thumb`?"""
    return float(np.mean(np.abs(thumb(frame) - baseline_thumb))) >= HINT_CHANGE_THRESHOLD


# ------------------------------------------------------------------ what does it say
def read_hint_text(frame):
    results = ocr_readtext(prepare_for_ocr(frame), detail=1, paragraph=False, allowlist=ALLOWLIST,
                           text_threshold=0.6, low_text=0.3, width_ths=0.8, mag_ratio=1.0)
    results.sort(key=lambda r: (round(r[0][0][1] / 10), r[0][0][0]))  # reading order: lines, then left edge
    return " ".join(t for _, t, c in results if c >= MIN_CONFIDENCE)


def is_ruined(text):
    return max(textmatch.ratio(text, t) for t in RUINED_TEXTS) >= RUINED_CUTOFF


def is_brewing(text):
    return any(textmatch.contains(text, p) for p in BREW_STEP_TEXTS)


def is_cup(text):
    """Order taken but no cup yet ("Ambil gelas di rak (E) untuk mulai meracik")."""
    return any(textmatch.contains(text, p, 0.7) for p in CUP_TEXTS)


def is_take(text):
    return any(textmatch.contains(text, p, 0.7) for p in TAKE_TEXTS)


def is_serve(text):
    return any(textmatch.contains(text, p) for p in SERVE_TEXTS)


def is_next_step(text):
    return any(textmatch.contains(text, p, 0.8) for p in NEXT_HINT_TEXTS)


def after_brew(text):
    """True only if the hint positively shows what comes after the brew minigame."""
    if is_brewing(text):
        return False
    return (is_ruined(text)
            or is_serve(text)
            or any(textmatch.contains(text, p, 0.8) for p in NEXT_STATION_TEXTS))


class HintState(enum.Enum):
    RUINED = "ruined"      # cup ruined: bin it
    BUSY = "busy"          # cup in hand: next-step or serve hint
    TAKE = "take"          # no order open
    CUP = "cup"            # order taken, ready to start a drink
    UNKNOWN = "unknown"    # unreadable


def classify(text):
    if is_ruined(text):
        return HintState.RUINED
    if is_next_step(text) or is_serve(text):
        return HintState.BUSY
    if is_take(text):
        return HintState.TAKE
    if is_cup(text):
        return HintState.CUP
    return HintState.UNKNOWN


if __name__ == "__main__":
    win = backend.get_game_rect()
    if win is None:
        raise SystemExit("Roblox window not found")
    print("computed region:", _computed_hint_region(win))
    print("used region:    ", hint_region(win))
    frame = grab_hint(win)
    cv2.imwrite("hint_debug.png", frame)
    print("saved hint_debug.png; OCR reads:", repr(read_hint_text(frame)))
