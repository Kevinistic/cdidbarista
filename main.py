"""CDID barista bot.

Reads the BARISTA quest panel and does what it says: holds the right arrow until the gold
highlighted station label is on screen, walks at it until the prompt chip under it shows up,
then clicks that chip. The customer and the bin aren't highlighted, so those are found by OCR
on their chip text. Cup / flavour pickers are clicked by OCR, the shot minigame is coffee.py.

    python main.py                 run the bot (F6 pause/resume, F7 quit)
    python main.py --test a.png    run the vision on screenshots, annotated copies go to debug/
"""
import argparse
import ctypes
import difflib
import os
import random
import re
import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np

import coffee
import config

try:  # per-monitor DPI awareness so screen pixels match win32 coordinates 1:1
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

REF_H = 1172          # client height the pixel constants below were measured at
DEBUG_DIR = "debug"


# ================================================================ OCR + text matching

_reader = None
ALLOWLIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!?,.'#:-() "


def reader():
    """EasyOCR (CRAFT detector + CRNN recogniser), loaded once on first use."""
    global _reader
    if _reader is None:
        import easyocr
        import torch
        _reader = easyocr.Reader(["en"], gpu=torch.cuda.is_available(), verbose=False)
    return _reader


@dataclass
class Word:
    text: str
    box: tuple            # x0, y0, x1, y1 in client pixels

    @property
    def cx(self):
        return (self.box[0] + self.box[2]) / 2

    @property
    def cy(self):
        return (self.box[1] + self.box[3]) / 2

    @property
    def h(self):
        return self.box[3] - self.box[1]


def ocr(img, box=None, scale=1.0):
    """Words in img (or in the client-pixel box of it), with boxes in img coordinates."""
    x0, y0 = (box[0], box[1]) if box else (0, 0)
    crop = img[box[1]:box[3], box[0]:box[2]] if box else img
    if crop.size == 0:
        return []
    if scale != 1.0:
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    words = []
    for pts, text, conf in reader().readtext(crop, detail=1, paragraph=False,
                                             allowlist=ALLOWLIST, width_ths=0.3):
        if conf < config.OCR_MIN_CONF or not text.strip():
            continue
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        words.append(Word(text.strip(), (x0 + min(xs) / scale, y0 + min(ys) / scale,
                                         x0 + max(xs) / scale, y0 + max(ys) / scale)))
    return words


def lines(words):
    """Group words into text lines: same row and nearly touching, left to right."""
    out = []
    for w in sorted(words, key=lambda w: w.box[0]):
        for ln in out:
            last = ln[-1]
            h = max(w.h, last.h)
            if abs(w.cy - last.cy) < 0.5 * h and -0.5 * h < w.box[0] - last.box[2] < 1.0 * h:
                ln.append(w)
                break
        else:
            out.append([w])
    return sorted(out, key=lambda ln: (ln[0].cy, ln[0].box[0]))


def join(ln):
    """A line of words as one Word."""
    return Word(" ".join(w.text for w in ln),
                (min(w.box[0] for w in ln), min(w.box[1] for w in ln),
                 max(w.box[2] for w in ln), max(w.box[3] for w in ln)))


def squash(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def fuzzy_in(text, phrase):
    """How well phrase appears somewhere in text, 0..1. Short phrases must match a whole word."""
    t, p = squash(text), squash(phrase)
    if not t or not p:
        return 0.0
    if len(p) <= 4:                     # one OCR slip allowed ("Hil" for "Hi")
        r = max((difflib.SequenceMatcher(None, squash(w), p).ratio() for w in text.split()), default=0.0)
        return r if r >= 0.8 else 0.0
    if p in t:
        return 1.0
    best = 0.0
    for n in {len(p) - 1, len(p), len(p) + 1}:
        for i in range(max(1, len(t) - n + 1)):
            best = max(best, difflib.SequenceMatcher(None, t[i:i + n], p).ratio())
    return best


def best_alias(text, table, cutoff=None):
    """table {canonical: [aliases]} -> canonical whose alias best appears in text, or None."""
    cutoff = config.MATCH_CUTOFF if cutoff is None else cutoff
    best, best_score = None, cutoff
    for name, aliases in table.items():
        score = max(fuzzy_in(text, a) for a in aliases)
        if score > best_score or (score == best_score == 1.0 and best and len(name) > len(best)):
            best, best_score = name, score
    return best


def best_words(text, table, cutoff=0.75):
    """Like best_alias, but compares whole-word windows, so 'please' can't pass for 'Maple'."""
    words = [squash(w) for w in text.split()]
    words = [w for w in words if w]
    best, best_score, best_len = None, cutoff, 0
    for name, aliases in table.items():
        for alias in aliases:
            a = squash(alias)
            n = len(alias.split())
            score = 1.0 if a in "".join(words) and len(a) > 4 else 0.0
            for m in {max(1, n - 1), n, n + 1}:
                for i in range(len(words) - m + 1):
                    score = max(score, difflib.SequenceMatcher(None, "".join(words[i:i + m]), a).ratio())
            if score > best_score or (score == best_score and best and len(a) > best_len):
                best, best_score, best_len = name, score, len(a)
    return best


def whole_match(text, phrase):
    """Similarity of a whole line to a phrase (card labels, where substrings would lie)."""
    return difflib.SequenceMatcher(None, squash(text), squash(phrase)).ratio()


# ================================================================ panel / dialogue parsing

@dataclass
class Step:
    kind: str                 # ask, cup, station, serve, bin
    action: str = None
    station: str = None
    raw: str = ""

    def key(self):
        return self.kind, self.action, self.station

    def __str__(self):
        if self.kind == "station":
            return f"{self.action} @ {self.station}"
        return self.kind


def parse_panel(text):
    """BARISTA panel text -> Step, or None if nothing recognisable."""
    words = text.lower().split()
    # only station steps start with "Next:" (checked first: "Tuang" reads a lot like "Buang")
    starts = [i for i, w in enumerate(words[:4])
              if max(difflib.SequenceMatcher(None, squash(w), n).ratio() for n in config.NEXT_WORDS) >= 0.75]
    if not starts:
        low = " ".join(words)
        for kind, keys in config.PANEL_KEYWORDS:
            if any(fuzzy_in(low, k) >= 0.88 for k in keys):
                return Step(kind, station={"bin": "Bin", "cup": "Cup Rack"}.get(kind), raw=text)
        return None
    parts = re.split(r"\s(?:at|di)\s", " ".join(words[starts[0] + 1:]), maxsplit=1)
    action_txt, station_txt = parts[0], parts[1] if len(parts) > 1 else ""
    action_txt = action_txt.strip(" -:")
    station_txt = re.sub(r"\(?\s*e\s*\)?\s*$", "", station_txt).strip(" -:")
    action = best_alias(action_txt, config.ACTIONS)
    names = {k: v["names"] for k, v in config.STATIONS.items()}
    station = best_alias(station_txt, names) if station_txt else None
    station = station or config.ACTION_STATION.get(action) or station_txt.title() or None
    if not station:
        return None
    return Step("station", action or action_txt.title(), station, raw=text)


def parse_order(text):
    """Customer line -> (drink, syrup); either may be None."""
    drink = best_words(text, config.MENU)
    syrup = best_words(text, {s: [s] for s in config.SYRUPS})
    return drink, syrup


def recipe_plan(drink, syrup):
    """'Espresso > Milk > Maple > Ice' for the overlay."""
    steps = []
    for s in config.RECIPES.get(drink, []):
        steps.append((syrup or "?") if s == "Flavour" else s)
    return " > ".join(steps)


def station_info(name):
    info = config.STATIONS.get(name)
    return (info["names"], info["prompts"]) if info else ([name], [])


# ================================================================ vision on a client screenshot
# EasyOCR's text detector takes seconds per region on CPU, so text is located by pixels
# (white prompt chips, white picker cards, gold labels, the red CANCEL button) and only the
# recogniser runs, on single-line crops. The detector is kept for the landmark fallback.

def get_roblox_client_rect():
    """Find Roblox window and return its exact INNER client viewport coordinates."""
    import win32gui

    def enum_windows_callback(hwnd, windows):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if "roblox" in title.lower():
                windows.append((hwnd, title))

    windows = []
    win32gui.EnumWindows(enum_windows_callback, windows)
    if not windows:
        return None
    hwnd, title = windows[0]
    client_rect = win32gui.GetClientRect(hwnd)
    left, top = win32gui.ClientToScreen(hwnd, (0, 0))
    return {
        "hwnd": hwnd,
        "title": title,
        "left": left,
        "top": top,
        "width": client_rect[2] - client_rect[0],
        "height": client_rect[3] - client_rect[1],
    }


def _parent_rect(W, H):
    """Shared parent (the Roblox client viewport) in client pixels."""
    return (W * config.PARENT_POS_X, H * config.PARENT_POS_Y,
            W * config.PARENT_SIZE_X, H * config.PARENT_SIZE_Y)


def region_box(region, W, H):
    """Client-pixel box (x0, y0, x1, y1) of a Roblox-style ((anchor), (position), (size)) region."""
    (ax, ay), (px, py), (sx, sy) = region
    pl, pt, pw, ph = _parent_rect(W, H)
    bw, bh = pw * sx, ph * sy
    x0 = pl + pw * px - bw * ax
    y0 = pt + ph * py - bh * ay
    return int(x0), int(y0), int(x0 + bw), int(y0 + bh)


def screen_region(window, region):
    """The same region in absolute screen coordinates, as an mss grab dict."""
    x0, y0, x1, y1 = region_box(region, window["width"], window["height"])
    return {"left": window["left"] + x0, "top": window["top"] + y0, "width": x1 - x0, "height": y1 - y0}


def world_mask(W, H):
    """255 where world labels / chips can be: excludes the HUD panels."""
    m = np.zeros((H, W), np.uint8)
    x0, y0, x1, y1 = region_box(config.WORLD_REGION, W, H)
    m[y0:y1, x0:x1] = 255
    x0, y0, x1, y1 = region_box(config.HUD_BLOCK, W, H)
    m[y0:y1, x0:x1] = 0
    return m


def read_line(img, box, scale=2.0):
    """Recognise one line of text in box -> (text, confidence). No detector, so it's fast."""
    H, W = img.shape[:2]
    x0, y0, x1, y1 = max(0, int(box[0])), max(0, int(box[1])), min(W, int(box[2])), min(H, int(box[3]))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return "", 0.0
    crop = img[y0:y1, x0:x1]
    if crop.ndim == 3:
        crop = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    if scale != 1.0:
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    h, w = crop.shape
    res = reader().recognize(crop, horizontal_list=[[0, w, 0, h]], free_list=[],
                             detail=1, allowlist=ALLOWLIST)
    if not res:
        return "", 0.0
    return res[0][1].strip(), float(res[0][2])


def read_white_text(img, box):
    """World text is white with a dark outline: keep only the white fill, as black on white."""
    x0, y0, x1, y1 = (int(v) for v in box)
    crop = img[max(0, y0):y1, max(0, x0):x1]
    if crop.size == 0:
        return ""
    ink = 255 - cv2.inRange(crop, (245, 245, 245), (255, 255, 255))
    return read_line(ink, (0, 0, ink.shape[1], ink.shape[0]), 2.0)[0]


def white_boxes(img, region, w_range, h_range, min_fill):
    """Pure-white UI boxes (chips, cards) inside region; sizes in pixels at REF_H."""
    H, W = img.shape[:2]
    k = H / REF_H
    x0, y0, x1, y1 = region
    m = cv2.inRange(img[y0:y1, x0:x1], (250, 250, 250), (255, 255, 255))
    n, _, stats, _ = cv2.connectedComponentsWithStats(m)
    out = []
    for x, y, w, h, area in stats[1:]:
        if w_range[0] * k <= w <= w_range[1] * k and h_range[0] * k <= h <= h_range[1] * k \
                and area >= min_fill * w * h:
            out.append((int(x + x0), int(y + y0), int(x + x0 + w), int(y + y0 + h)))
    return out


def panel_text(img):
    """Instruction line(s) of the BARISTA panel, read just above its red CANCEL button."""
    H, W = img.shape[:2]
    x0, y0, x1, y1 = region_box(config.PANEL_REGION, W, H)
    hsv = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    red = cv2.inRange(hsv, (0, 150, 150), (6, 255, 255)) | cv2.inRange(hsv, (174, 150, 150), (180, 255, 255))
    n, _, stats, _ = cv2.connectedComponentsWithStats(red)
    buttons = [s for s in stats[1:]
               if s[2] > 0.08 * W and 4 <= s[2] / max(s[3], 1) <= 10 and s[4] > 0.7 * s[2] * s[3]]
    if not buttons:
        return ""
    bx, by, bw, bh, _ = max(buttons, key=lambda s: s[4])
    bx, by = bx + x0, by + y0
    u = bh / 40                          # the button is 40 px tall at 1920x1172
    texts = []
    for top, bottom in ((-62, -40), (-42, -18)):     # up to two instruction lines
        text, conf = read_line(img, (bx - 4 * u, by + top * u, bx + bw + 4 * u, by + bottom * u))
        if conf >= 0.2 and len(squash(text)) >= 3:
            texts.append(text)
    return " ".join(texts)


def dialogue_text(img):
    """The customer's current line ('Hi! I'd like a ...'); may be junk when no dialogue is up."""
    H, W = img.shape[:2]
    return read_white_text(img, region_box(config.DIALOGUE_REGION, W, H))


def is_dialogue(text):
    return any(fuzzy_in(text, w) >= 0.8 for w in config.DIALOGUE_WORDS) or parse_order(text)[0] is not None


def continue_visible(img):
    """'click to continue' / 'klik untuk lanjut' under the dialogue."""
    H, W = img.shape[:2]
    text, _ = read_line(img, region_box(config.CONTINUE_REGION, W, H), 3.0)
    return max(whole_match(text, t) for t in config.CONTINUE_TEXTS) >= config.CONTINUE_CUTOFF


def dialogue_up(img):
    return continue_visible(img) or is_dialogue(dialogue_text(img))


def highlight_mask(img):
    H, W = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    return cv2.inRange(hsv, config.HIGHLIGHT_LO, config.HIGHLIGHT_HI) & world_mask(W, H)


def find_labels(img, mask=None):
    """Candidate gold label boxes (x0, y0, x1, y1), widest first."""
    H, W = img.shape[:2]
    k = H / REF_H
    m = highlight_mask(img) if mask is None else mask
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, int(20 * k)), max(3, int(7 * k))))
    blob = cv2.morphologyEx(m, cv2.MORPH_CLOSE, kernel)
    n, _, stats, _ = cv2.connectedComponentsWithStats(blob)
    out = []
    for x, y, w, h, _ in stats[1:]:
        if w < 30 * k or not 12 * k <= h <= 80 * k or w < 1.3 * h:
            continue
        fill = cv2.countNonZero(m[y:y + h, x:x + w]) / (w * h)
        if 0.12 <= fill <= 0.85:
            out.append((int(x), int(y), int(x + w), int(y + h)))
    return sorted(out, key=lambda b: b[0] - b[2])


def read_label(img, box, mask=None):
    """OCR only the gold pixels of a label, so overlapping white labels don't pollute it."""
    m = highlight_mask(img) if mask is None else mask
    x0, y0, x1, y1 = box
    pad = 6
    crop = m[max(0, y0 - pad):y1 + pad, max(0, x0 - pad):x1 + pad]
    crop = 255 - cv2.dilate(crop, np.ones((2, 2), np.uint8))
    return read_line(crop, (0, 0, crop.shape[1], crop.shape[0]), 1.0)[0]


@dataclass
class Chip:
    x: int
    y: int                    # centre of the white chip (where to click)
    key: str                  # "e" -> press E, "click" -> click it
    text: str = ""            # the action text right of the chip
    box: tuple = ()           # chip + text


@dataclass
class Target:
    box: tuple                # x0, y0, x1, y1 of the thing to walk at
    text: str = ""
    chip: Chip = None         # set when the target itself is a prompt chip
    stale: bool = False       # re-used from an earlier (slow) look: don't steer on it

    @property
    def cx(self):
        return (self.box[0] + self.box[2]) / 2


def find_chips(img):
    """Every prompt chip on screen ('E' / 'Click' white box + action text to its right)."""
    H, W = img.shape[:2]
    wm = world_mask(W, H)
    boxes = [b for b in white_boxes(img, region_box(config.WORLD_REGION, W, H), (18, 110), (20, 42), 0.7)
             if wm[(b[1] + b[3]) // 2, (b[0] + b[2]) // 2]]
    chips = []
    for x0, y0, x1, y1 in boxes:
        inner = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
        h = y1 - y0
        edges = inner[[int(.15 * h), int(.85 * h)], int(.2 * (x1 - x0)):int(.8 * (x1 - x0)) + 1]
        if not 0.03 <= np.mean(inner < 100) <= 0.4 or np.mean(edges >= 245) < 0.75:
            continue                # a chip is a solid white box with dark key text in the middle
        # the action text runs right until the next chip on the same row, whose left edge is a
        # solid white column even when it touches this text and wasn't found as its own box
        end = min([b[0] for b in boxes if b[0] > x1 and abs(b[1] - y0) < h] + [x1 + 12 * h, W])
        strip = cv2.inRange(img[y0 + h // 4:y1 - h // 4, x1 + h // 2:int(end)], (250, 250, 250), (255, 255, 255))
        if strip.size:
            solid = (strip.min(axis=0) == 255).astype(np.uint8)
            run = max(3, h // 4)            # wider than any letter stroke
            hits = np.flatnonzero(np.convolve(solid, np.ones(run, np.uint8), "valid") == run)
            if len(hits):
                end = x1 + h // 2 + hits[0]
        text = read_white_text(img, (x1 + 2, y0 - 0.15 * h, end - 2, y1 + 0.15 * h))
        key = "e" if (x1 - x0) < 1.4 * h else "click"
        chips.append(Chip((x0 + x1) // 2, (y0 + y1) // 2, key, text, (x0, y0, int(end), y1)))
    return chips


def find_station_label(img, names, near=None):
    """Gold label of the target station. With near (a previous Target) it just tracks the
    candidate closest to it without OCR; otherwise each candidate is OCR-confirmed."""
    m = highlight_mask(img)
    boxes = find_labels(img, m)
    if not boxes:
        return None
    if near is not None:
        b = min(boxes, key=lambda b: abs((b[0] + b[2]) / 2 - near.cx) + abs(b[1] - near.box[1]))
        if abs((b[0] + b[2]) / 2 - near.cx) < 0.25 * img.shape[1]:
            return Target(b, near.text)
        return None
    for b in boxes[:4]:
        text = read_label(img, b, m)
        if max(fuzzy_in(text, n) for n in names) >= config.MATCH_CUTOFF:
            return Target(b, text)
        if config.DEBUG:
            print(f"[label] rejected {text!r} at {b}")
    return None


def other_phrases(phrases):
    """Every known prompt / action text that is not one of phrases."""
    known = [p for st in config.STATIONS.values() for p in st["prompts"]]
    known += [a for aliases in config.ACTIONS.values() for a in aliases]
    known += config.ASK_PROMPTS + config.SERVE_PROMPTS
    own = {squash(p) for p in phrases}
    return [p for p in known if squash(p) not in own]


def find_chip(img, label, phrases, chips=None):
    """The prompt chip under a label whose text matches one of phrases better than any other
    station's prompt. No text match, no chip: using the wrong station ruins the drink."""
    x0, y0, x1, y1 = label.box
    w, h = x1 - x0, y1 - y0
    others = other_phrases(phrases)
    best, best_score = None, 0.0
    for chip in find_chips(img) if chips is None else chips:
        if not y1 - 0.3 * h <= chip.box[1] <= y1 + 3 * h or not x0 - 0.5 * w <= chip.x <= x1:
            continue
        match = max((fuzzy_in(chip.text, p) for p in phrases), default=0.0)
        other = max((fuzzy_in(chip.text, p) for p in others), default=0.0)
        if match < config.MATCH_CUTOFF or match <= other:
            continue
        score = match - 0.2 * abs(chip.x - label.cx) / max(w, 1)
        if score > best_score:
            best, best_score = chip, score
    return best


def find_prompt(img, prompts):
    """A chip anywhere on screen whose text matches one of prompts -> Target."""
    best, best_score = None, config.MATCH_CUTOFF
    for chip in find_chips(img):
        score = max(fuzzy_in(chip.text, p) for p in prompts)
        if score >= best_score:
            best, best_score = Target(chip.box, chip.text, chip), score
    return best


def find_landmark(img, landmarks):
    """Slow path: detector OCR at half resolution for a big white station label."""
    H, W = img.shape[:2]
    view = img.copy()
    view[world_mask(W, H) == 0] = 0
    best, best_score = None, config.MATCH_CUTOFF
    for ln in lines(ocr(view, region_box(config.WORLD_REGION, W, H), 0.5)):
        whole = join(ln)
        score = max(fuzzy_in(whole.text, x) for x in landmarks)
        if score >= best_score:
            best, best_score = Target(tuple(int(v) for v in whole.box), whole.text), score
    return best


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


# ================================================================ screen + input

class Screen:
    """Roblox client-area capture. mss handles are per thread: create this in the thread using it."""

    def __init__(self):
        import mss
        self.sct = mss.mss()
        self.window = None                # get_roblox_client_rect() result
        self.hwnd = None
        self.rect = None                  # (left, top, width, height) of the client area

    def find(self):
        self.window = get_roblox_client_rect()
        if self.window is None or self.window["width"] <= 0 or self.window["height"] <= 0:
            self.hwnd = self.rect = None
            return None
        w = self.window
        self.hwnd = w["hwnd"]
        self.rect = (w["left"], w["top"], w["width"], w["height"])
        return self.rect

    def focused(self):
        import win32gui
        return self.hwnd is not None and win32gui.GetForegroundWindow() == self.hwnd

    def grab(self, box=None):
        """BGR of the client-pixel box (x0, y0, x1, y1), or the whole client area."""
        left, top, w, h = self.rect
        x0, y0, x1, y1 = box or (0, 0, w, h)
        shot = self.sct.grab({"left": left + x0, "top": top + y0, "width": x1 - x0, "height": y1 - y0})
        return np.ascontiguousarray(np.array(shot)[:, :, :3])

    def to_screen(self, x, y):
        return int(self.rect[0] + x), int(self.rect[1] + y)


# Native SendInput: Roblox only registers hover/click from hardware-style events.
INPUT_MOUSE, MOVE, LEFTDOWN, LEFTUP, ABSOLUTE = 0, 0x0001, 0x0002, 0x0004, 0x8000
ULONG_PTR = ctypes.c_ulong if ctypes.sizeof(ctypes.c_void_p) == 4 else ctypes.c_ulonglong


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong), ("dwExtraInfo", ULONG_PTR)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]
    _fields_ = [("type", ctypes.c_ulong), ("u", _U)]


def _mouse(flags, x, y):
    u32 = ctypes.windll.user32
    ax = int(x * 65535 / max(1, u32.GetSystemMetrics(0) - 1))
    ay = int(y * 65535 / max(1, u32.GetSystemMetrics(1) - 1))
    inp = INPUT(INPUT_MOUSE)
    inp.u.mi = MOUSEINPUT(ax, ay, 0, flags | ABSOLUTE, 0, u32.GetMessageExtraInfo())
    u32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def click(x, y):
    """Glide the cursor to screen (x, y) so Roblox registers the hover, then left click."""
    import win32api
    sx, sy = win32api.GetCursorPos()
    steps = max(3, min(int(((x - sx) ** 2 + (y - sy) ** 2) ** 0.5) // 30, 12))
    for i in range(1, steps + 1):
        cx, cy = int(sx + (x - sx) * i / steps), int(sy + (y - sy) * i / steps)
        win32api.SetCursorPos((cx, cy))
        _mouse(MOVE, cx, cy)
        time.sleep(0.003)
    win32api.SetCursorPos((x, y))
    _mouse(MOVE, x, y)
    time.sleep(0.02)
    _mouse(LEFTDOWN, x, y)
    time.sleep(0.02)
    _mouse(LEFTUP, x, y)


# ================================================================ the bot

class Abort(Exception):
    """Paused, lost focus, or the panel moved on: drop the current step and re-plan."""


@dataclass
class Status:
    state: str = "starting"
    step: str = "-"
    order: str = "-"
    plan: str = ""
    paused: bool = False


class Bot:
    def __init__(self, enabled):
        self.enabled = enabled            # threading.Event, F6 toggles it
        self.status = Status()
        self.held = set()
        self.order = None                 # (drink, syrup) of the current customer
        self.step = None                  # Step being worked on
        self.fails = {}                   # step key -> attempts without progress
        self._last_check = 0.0
        self.scr = None

    # ---------------------------------------------------------- plumbing
    def say(self, state):
        self.status.state = state
        if config.DEBUG:
            print(f"[bot] {state}")

    def ready(self):
        ok = self.enabled.is_set() and self.scr.find() is not None and self.scr.focused()
        if not ok:
            self.release_all()
        self.status.paused = not ok
        return ok

    def check(self, panel_every=2.5):
        """Raise Abort when paused/unfocused, or (every few s) when the panel changed step."""
        if not self.ready():
            raise Abort("paused")
        now = time.time()
        if self.step is not None and panel_every and now - self._last_check > panel_every:
            self._last_check = now
            step = parse_panel(panel_text(self.scr.grab()))
            if step is not None and step.key() != self.step.key():
                raise Abort(f"panel now says {step}")

    def hold(self, key):
        import keyboard
        if key not in self.held:
            keyboard.press(key)
            self.held.add(key)

    def release(self, key):
        import keyboard
        if key in self.held:
            keyboard.release(key)
            self.held.discard(key)

    def release_all(self):
        for key in list(self.held):
            self.release(key)

    def tap(self, key, seconds=0.05):
        self.hold(key)
        time.sleep(seconds)
        self.release(key)

    def grab(self):
        return self.scr.grab()

    def click_client(self, x, y):
        click(*self.scr.to_screen(x, y))

    def debug_save(self, img, name, boxes=()):
        if not config.DEBUG:
            return
        os.makedirs(DEBUG_DIR, exist_ok=True)
        out = img.copy()
        for b in boxes:
            cv2.rectangle(out, (int(b[0]), int(b[1])), (int(b[2]), int(b[3])), (0, 255, 0), 2)
        cv2.imwrite(os.path.join(DEBUG_DIR, f"{time.strftime('%H%M%S')}_{name}.png"), out)

    # ---------------------------------------------------------- reading state
    def read_step(self):
        """Panel step, read twice so one bad OCR pass can't send us to the wrong station."""
        a = parse_panel(panel_text(self.grab()))
        if a is None:
            return None
        time.sleep(0.15)
        b = parse_panel(panel_text(self.grab()))
        return a if b is not None and a.key() == b.key() else None

    def set_order(self, drink, syrup):
        self.order = (drink, syrup)
        self.status.order = " + ".join(p for p in (drink, syrup) if p) or "-"
        self.status.plan = recipe_plan(drink, syrup)

    # ---------------------------------------------------------- navigation
    def scan(self, locate, slow):
        """Lazy search: turn right until locate(img) sees the target."""
        self.say("scanning (holding right arrow)")
        t0 = time.time()
        try:
            while time.time() - t0 < config.SEEK_TIMEOUT:
                self.check()
                if slow:            # OCR is slow: turn in steps so frames aren't smeared
                    self.tap(config.TURN_RIGHT, config.SCAN_STEP)
                    time.sleep(0.08)
                else:
                    self.hold(config.TURN_RIGHT)
                target = locate(self.grab(), None)
                if target is not None:
                    return target
        finally:
            self.release(config.TURN_RIGHT)
        return None

    def approach(self, target, locate, chip_near, slow):
        """Walk at the target, keeping it centred, until the chip under it shows up."""
        self.say(f"walking to {target.text or 'target'}")
        W = self.scr.rect[2]
        t0 = last_seen = time.time()
        thumbs = []                       # (t, tiny grey frame) to notice we're stuck
        try:
            while time.time() - t0 < config.APPROACH_TIMEOUT:
                self.check()
                img = self.grab()
                found = locate(img, target)
                if found is None:
                    self.release(config.FORWARD)
                    if time.time() - last_seen > config.LOST_TIMEOUT:
                        return None
                    continue
                target, last_seen = found, time.time()
                chip = target.chip or chip_near(img, target)
                if chip is not None:
                    self.debug_save(img, "chip", [target.box, (chip.x - 5, chip.y - 5, chip.x + 5, chip.y + 5)])
                    return chip

                err = 0.0 if target.stale else (target.cx - W / 2) / W
                turn = config.TURN_RIGHT if err > 0 else config.TURN_LEFT
                if abs(err) > config.CENTER_TOL:
                    self.release(config.FORWARD)
                    self.tap(turn, min(0.3, abs(err) * config.STEER_GAIN))
                    continue
                if slow:
                    self.tap(config.FORWARD, config.WALK_BURST)
                else:
                    self.hold(config.FORWARD)
                if abs(err) > config.STEER_TOL:
                    self.tap(turn, abs(err) * config.STEER_GAIN)

                thumb = cv2.cvtColor(cv2.resize(img, (64, 40), interpolation=cv2.INTER_AREA),
                                     cv2.COLOR_BGR2GRAY).astype(np.float32)
                thumbs = [(t, g) for t, g in thumbs if time.time() - t < config.STUCK_TIMEOUT] + [(time.time(), thumb)]
                if time.time() - thumbs[0][0] > 0.8 * config.STUCK_TIMEOUT \
                        and np.mean(np.abs(thumbs[0][1] - thumb)) < 2.0:
                    self.unstick()
                    thumbs = []
        finally:
            self.release(config.FORWARD)
        return None

    def unstick(self):
        self.say("stuck, backing off")
        self.release_all()
        self.tap(config.BACK, 0.4)
        self.tap(random.choice([config.STRAFE_LEFT, config.STRAFE_RIGHT]), 0.5)
        self.tap(config.JUMP, 0.1)

    def wander(self):
        """Nothing found after a full turn: walk somewhere else and look again."""
        self.say("nothing in view, wandering")
        self.tap(config.TURN_RIGHT, random.uniform(0.2, 0.6))
        self.tap(config.FORWARD, random.uniform(0.8, 1.5))

    def goto_station(self, name, phrases):
        names, prompts = station_info(name)
        phrases = list(phrases) + prompts

        def locate(img, near):
            return find_station_label(img, names, near)

        def chip_near(img, target):
            return find_chip(img, target, phrases)

        return self.navigate(locate, chip_near, slow=False)

    def goto_text(self, prompts, landmarks):
        """Customer / bin: nothing is highlighted, so turn looking for their chip text. If no
        chip is in reach, walk at a landmark label (slow OCR, refreshed every few bursts)."""
        looks = [0]

        def locate(img, near):
            hit = find_prompt(img, prompts)
            if hit is not None:
                return hit
            looks[0] += 1
            if near is None or looks[0] % 3 == 0:
                return find_landmark(img, landmarks)
            return Target(near.box, near.text, stale=True)

        target = self.scan(lambda img, near: find_prompt(img, prompts), slow=False)
        if target is not None:
            return target.chip
        self.say(f"no prompt in reach, looking for {landmarks[0]} (slow OCR)")
        for _ in range(8):                  # roughly a full turn in coarse steps
            self.check()
            target = find_landmark(self.grab(), landmarks)
            if target is not None:
                return target.chip or self.approach(target, locate, lambda img, t: None, slow=True)
            self.tap(config.TURN_RIGHT, 3 * config.SCAN_STEP)
            time.sleep(0.1)
        self.wander()
        return None

    def navigate(self, locate, chip_near, slow):
        target = self.scan(locate, slow)
        if target is None:
            self.wander()
            return None
        if target.chip is not None:
            return target.chip
        return self.approach(target, locate, chip_near, slow)

    def use(self, chip):
        self.say(f"using {chip.text!r} ({chip.key})")
        if chip.key == "e":
            self.tap(config.INTERACT, 0.08)     # this E chip is under our target, so E is ours
        else:
            self.click_client(chip.x, chip.y)

    # ---------------------------------------------------------- popups / minigame
    def read_order(self, timeout=10.0):
        """Click through the customer's lines and remember the order (it can't be asked again)."""
        self.say("listening to the order")
        H, W = self.scr.rect[3], self.scr.rect[2]
        x0, y0, x1, y1 = region_box(config.DIALOGUE_REGION, W, H)
        t0, blank, heard = time.time(), 0, False
        while time.time() - t0 < timeout:
            self.check(panel_every=0)
            img = self.grab()
            text = dialogue_text(img)
            if not (is_dialogue(text) or continue_visible(img)):
                blank += 1
                if blank >= 4 and (heard or time.time() - t0 > 4):
                    break
                time.sleep(0.25)
                continue
            blank = 0
            drink, syrup = parse_order(text)
            if drink:
                self.set_order(drink, syrup)
                heard = True
            self.click_client((x0 + x1) // 2, (y0 + y1) // 2)     # "click to continue"
            time.sleep(0.5)
        return heard

    def pick(self, aliases, what, timeout=4.0):
        """Click the matching card in the cup / flavour picker."""
        self.say(f"picking {what}")
        t0 = time.time()
        while time.time() - t0 < timeout:
            self.check(panel_every=0)
            img = self.grab()
            card = find_card(img, aliases)
            if card:
                self.debug_save(img, f"pick_{what}", [(card[0] - 40, card[1] - 10, card[0] + 40, card[1] + 10)])
                self.click_client(card[0], card[1])
                time.sleep(0.6)
                return True
            time.sleep(0.2)
        return False

    def play_shot(self):
        self.say("pulling the shot (EKSTRAKSI)")

        def grab(rect):
            if rect is None:
                return self.grab()
            x, y, w, h = rect
            return self.scr.grab((x, y, x + w, y + h))

        return coffee.play(grab, lambda: self.enabled.is_set() and self.scr.focused())

    # ---------------------------------------------------------- one step
    def wait_for_change(self, step, timeout=config.ACTION_WAIT):
        t0 = time.time()
        while time.time() - t0 < timeout:
            self.check(panel_every=0)
            if coffee.find_track(self.grab()) is not None:     # a minigame we didn't expect
                self.play_shot()
            new = parse_panel(panel_text(self.grab()))
            if new is not None and new.key() != step.key():
                return True
            time.sleep(0.3)
        return False

    def need_order(self, what):
        """No order known: let the human pick, the panel moving on resumes the bot."""
        self.say(f"order unknown: pick the {what} yourself")
        self.release_all()

    def do(self, step):
        self.step, self._last_check = step, time.time()
        self.status.step = str(step)
        if dialogue_up(self.grab()):                     # the customer is still talking
            self.read_order()
        drink, syrup = self.order or (None, None)

        # a picker may still be open from a previous attempt
        want = config.MENU[drink] if step.kind == "cup" and drink else \
            [syrup] if step.action == "Pick a Flavour" and syrup else None
        if want and find_card(self.grab(), want):
            self.pick(want, want[0])
            self.wait_for_change(step)
            return

        if step.kind == "ask":
            chip = self.goto_text(config.ASK_PROMPTS, config.COUNTER_LANDMARKS)
            if chip:
                self.use(chip)
                time.sleep(0.6)
                if not self.read_order():
                    self.say("didn't catch the order")
        elif step.kind == "serve":
            chip = self.goto_text(config.SERVE_PROMPTS, config.COUNTER_LANDMARKS)
            if chip:
                self.use(chip)
        elif step.kind == "bin":
            names, prompts = station_info("Bin")
            chip = self.goto_text(prompts, names)
            if chip:
                self.use(chip)
        elif step.kind == "cup":
            if not drink:
                return self.need_order("cup")
            chip = self.goto_station("Cup Rack", [])
            if chip:
                self.use(chip)
                time.sleep(0.5)
                self.pick(config.MENU[drink], drink)
        elif step.kind == "station":
            if step.action == "Pick a Flavour" and not (syrup or config.DEFAULT_SYRUP):
                return self.need_order("flavour")
            chip = self.goto_station(step.station, config.ACTIONS.get(step.action, [step.action]))
            if chip:
                self.use(chip)
                time.sleep(0.4)
                if step.action == "Pick a Flavour":
                    s = syrup or config.DEFAULT_SYRUP
                    self.pick([s], s)
                elif step.action == "Pull the Shot":
                    self.play_shot()
        if not self.wait_for_change(step):
            n = self.fails[step.key()] = self.fails.get(step.key(), 0) + 1
            if n % 3 == 0:
                self.unstick()
            return
        self.fails.pop(step.key(), None)
        if step.kind == "serve":            # next customer; an order can't be re-asked, so only now
            self.order = None
            self.status.order, self.status.plan = "-", ""

    # ---------------------------------------------------------- main loop
    def run(self):
        self.scr = Screen()
        idle_since = time.time()
        while True:
            try:
                if not self.ready():
                    self.say("paused / Roblox not focused")
                    time.sleep(0.3)
                    continue
                step = self.read_step()
                if step is None:
                    self.step = None
                    self.status.step = "-"
                    # a dialogue left open (e.g. we asked but missed the reply)?
                    if dialogue_up(self.grab()):
                        self.read_order()
                    elif time.time() - idle_since > 5:
                        self.say("can't read the BARISTA panel (is the job started?)")
                    time.sleep(0.4)
                    continue
                idle_since = time.time()
                self.do(step)
            except Abort as e:
                self.release_all()
                self.say(f"re-planning: {e}")
            except Exception as e:                 # keep the bot alive, show what broke
                self.release_all()
                self.say(f"error: {e!r}")
                if config.DEBUG:
                    raise
                time.sleep(1.0)


# ================================================================ overlay + entry points

def run_overlay():
    import keyboard
    import tkinter as tk

    enabled = threading.Event()
    enabled.set()
    bot = Bot(enabled)

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.geometry("340x150+20+200")
    label = tk.Label(root, text="Loading OCR...", bg="#222222", fg="white", font=("Segoe UI", 10, "bold"),
                     justify="left", anchor="nw", wraplength=324, padx=8, pady=4)
    label.pack(fill="both", expand=True)

    drag = {"x": 0, "y": 0}
    label.bind("<Button-1>", lambda e: drag.update(x=e.x_root - root.winfo_x(), y=e.y_root - root.winfo_y()))
    label.bind("<B1-Motion>", lambda e: root.geometry(f"+{e.x_root - drag['x']}+{e.y_root - drag['y']}"))
    root.update()

    # keep the overlay from stealing focus from Roblox
    u32 = ctypes.windll.user32
    hwnd = u32.GetParent(root.winfo_id())
    u32.SetWindowLongW(hwnd, -20, u32.GetWindowLongW(hwnd, -20) | 0x08000000 | 0x80)

    reader().readtext(np.zeros((64, 256, 3), np.uint8))       # first inference finishes init

    def toggle():
        (enabled.clear if enabled.is_set() else enabled.set)()
        print(f"[{config.TOGGLE_KEY}] {'resumed' if enabled.is_set() else 'paused'}")

    def force_close():
        print(f"[{config.FORCE_CLOSE_KEY}] quitting")
        bot.release_all()
        for key in ("space", config.FORWARD, config.TURN_LEFT, config.TURN_RIGHT):
            try:
                keyboard.release(key)
            except Exception:
                pass
        os._exit(0)

    keyboard.add_hotkey(config.TOGGLE_KEY, toggle)
    keyboard.add_hotkey(config.FORCE_CLOSE_KEY, force_close)
    threading.Thread(target=bot.run, daemon=True).start()
    print(f"CDID barista bot running. {config.TOGGLE_KEY} = pause/resume, {config.FORCE_CLOSE_KEY} = quit.")

    def refresh():
        s = bot.status
        label.config(
            text=(f"{config.TOGGLE_KEY} pause  {config.FORCE_CLOSE_KEY} quit\n"
                  f"Step: {s.step}\nOrder: {s.order}\n{s.plan}\n{s.state}"),
            bg="#552222" if s.paused else "#225522")
        root.after(150, refresh)

    refresh()
    try:
        root.mainloop()
    finally:
        force_close()


def selftest(paths, slow=False):
    """Run every detector on screenshots and write annotated copies to debug/."""
    os.makedirs(DEBUG_DIR, exist_ok=True)
    for path in paths:
        img = cv2.imread(path)
        if img is None:
            print(f"{path}: unreadable")
            continue
        out = img.copy()
        t0 = time.time()
        ptext = panel_text(img)
        step = parse_panel(ptext)
        print(f"\n== {path}\n panel: {ptext!r}\n step: {step}")

        m = highlight_mask(img)
        for b in find_labels(img, m)[:4]:
            print(f" gold label {b}: {read_label(img, b, m)!r}")
            cv2.rectangle(out, b[:2], b[2:], (0, 215, 255), 2)
        if step and step.kind in ("station", "cup"):
            names, prompts = station_info(step.station)
            target = find_station_label(img, names)
            print(f" target label: {target}")
            if target:
                chip = find_chip(img, target, config.ACTIONS.get(step.action, []) + prompts)
                print(f" chip: {chip}")
                if chip:
                    cv2.circle(out, (chip.x, chip.y), 8, (0, 0, 255), 3)
        for chip in find_chips(img):
            print(f" chip on screen: {chip.key} {chip.text!r} at ({chip.x}, {chip.y})")
            cv2.rectangle(out, chip.box[:2], chip.box[2:], (255, 0, 255), 2)
        prompt = find_prompt(img, config.ASK_PROMPTS + config.SERVE_PROMPTS + station_info("Bin")[1])
        print(f" customer/bin prompt: {prompt}")
        if slow:
            t1 = time.time()
            print(f" landmark: {find_landmark(img, config.BIN_LANDMARKS + config.COUNTER_LANDMARKS)}"
                  f" ({time.time() - t1:.1f}s)")
        dtext = dialogue_text(img)
        if is_dialogue(dtext):
            print(f" dialogue: {dtext!r} -> {parse_order(dtext)}")
        cards = [(a[0], find_card(img, a)) for a in list(config.MENU.values()) + [[s] for s in config.SYRUPS]]
        if any(c for _, c in cards):
            print(" cards: " + ", ".join(f"{n}@{c[:2]}" for n, c in cards if c))
        track = coffee.find_track(img)
        if track:
            x, y, w, h = track
            needle, zone = coffee.locate(img[y:y + h, x:x + w])
            print(f" minigame track {track}: needle={needle} zone={zone}")
            cv2.rectangle(out, (x, y), (x + w, y + h), (255, 0, 0), 2)
        print(f" ({time.time() - t0:.1f}s)")
        cv2.imwrite(os.path.join(DEBUG_DIR, "test_" + os.path.basename(path)), out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--test", nargs="+", metavar="IMG", help="run the vision on screenshots instead")
    ap.add_argument("--slow", action="store_true", help="with --test: also run the slow landmark OCR")
    args = ap.parse_args()
    if args.test:
        selftest(args.test, args.slow)
    else:
        run_overlay()
