"""CDID barista bot.

Reads the BARISTA quest panel and does what it says: turns in steps until the target station
is on screen (its gold chevron first, OCR of the label text when there's none), walks at it in
bursts until the prompt chip under it shows up, then holds E / holds a click on that chip. The
customer isn't highlighted, so it's found by OCR on its chip text. Cup / flavour pickers are
clicked by OCR, the shot minigame is coffee.py.

    python main.py                 run the bot (F6 pause/resume, F7 quit)
    python main.py --test a.png    run the vision on screenshots, annotated copies go to debug/
"""
import argparse
import ctypes
import difflib
import math
import os
import random
import re
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

import coffee
import config
from ocr import REF_H, best_alias, fuzzy_in, read_line, read_white_text, region_box, squash, white_boxes

try:  # per-monitor DPI awareness so screen pixels match win32 coordinates 1:1
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

DEBUG_DIR = "debug"


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


def screen_region(window, region):
    """The same region in absolute screen coordinates, as an mss grab dict."""
    x0, y0, x1, y1 = region_box(region, window["width"], window["height"])
    return {"left": window["left"] + x0, "top": window["top"] + y0, "width": x1 - x0, "height": y1 - y0}


def world_mask(W, H):
    """255 where world labels / chips can be: excludes the HUD panels."""
    m = np.zeros((H, W), np.uint8)
    x0, y0, x1, y1 = region_box(config.WORLD_REGION, W, H)
    m[y0:y1, x0:x1] = 255
    for block in (config.HUD_BLOCK, config.TOPBAR_BLOCK):
        x0, y0, x1, y1 = region_box(block, W, H)
        m[y0:y1, x0:x1] = 0
    return m


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

    def read(src):
        texts = []
        for top, bottom in ((-62, -40), (-42, -18)):     # up to two instruction lines
            text, conf = read_line(src, (bx - 4 * u, by + top * u, bx + bw + 4 * u, by + bottom * u))
            if conf >= 0.2 and len(squash(text)) >= 3:
                texts.append(text)
        return " ".join(texts)

    text = read(img)
    if parse_panel(text) is None:
        # the panel is see-through: a red sign behind it wrecks the plain read, so keep only
        # the light unsaturated text as black on white
        mask = cv2.inRange(cv2.cvtColor(img, cv2.COLOR_BGR2HSV), (0, 0, 170), (180, 70, 255))
        text = read(cv2.cvtColor(255 - mask, cv2.COLOR_GRAY2BGR)) or text
    return text


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


def find_chevrons(img, mask=None):
    """Gold chevrons (the marker over the target's label) as (x0, y0, x1, y1): a fixed-size V,
    wide at the top and narrow at the bottom."""
    H = img.shape[0]
    k = H / REF_H
    m = highlight_mask(img) if mask is None else mask
    n, labels, stats, _ = cv2.connectedComponentsWithStats(m)
    out = []
    for i, (x, y, w, h, area) in enumerate(stats[1:], 1):
        if not (35 * k <= w <= 58 * k and 17 * k <= h <= 32 * k and 0.25 <= area / (w * h) <= 0.65):
            continue
        blob = labels[y:y + h, x:x + w] == i
        mid = slice(w // 2 - 2, w // 2 + 3)
        hollow_top = blob[:h // 3, mid].mean() < 0.1 and blob[:h // 3, :w // 4].any() and blob[:h // 3, -(w // 4):].any()
        solid_point = blob[int(.6 * h):int(.9 * h), mid].mean() > 0.7
        cols = np.flatnonzero(blob[int(.8 * h):].any(axis=0))
        if hollow_top and solid_point and len(cols) and cols[-1] - cols[0] < 0.6 * w:
            out.append((int(x), int(y), int(x + w), int(y + h)))
    return out


def chevron_target(img, chevron, mask):
    """Target for the label under a chevron: the gold pixels just below it (partly hidden
    under other labels is fine), or a box where the label should be."""
    H = img.shape[0]
    k = H / REF_H
    cx0, cy0, cx1, cy1 = chevron
    cx = (cx0 + cx1) // 2
    x0, y0, x1, y1 = int(cx - 220 * k), int(cy1 + 15 * k), int(cx + 220 * k), int(cy1 + 75 * k)
    ys, xs = np.nonzero(mask[max(0, y0):y1, max(0, x0):x1])
    if len(xs) > 30:
        box = (max(0, x0) + int(xs.min()), max(0, y0) + int(ys.min()),
               max(0, x0) + int(xs.max()), max(0, y0) + int(ys.max()))
    else:
        box = (int(cx - 80 * k), int(cy1 + 35 * k), int(cx + 80 * k), int(cy1 + 60 * k))
    return Target(box)


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
        h, w = y1 - y0, x1 - x0
        edges = inner[[int(.15 * h), int(.85 * h)], int(.2 * w):int(.8 * w) + 1]
        sides = np.concatenate([inner[int(.15 * h):int(.85 * h), int(.05 * w):int(.12 * w) + 1],
                                inner[int(.15 * h):int(.85 * h), int(.88 * w):int(.95 * w) + 1]], axis=1)
        if not 0.03 <= np.mean(inner < 100) <= 0.4 or np.mean(edges >= 245) < 0.75                 or np.mean(sides >= 245) < 0.85:
            continue                # a chip is a white box with its dark key text centred (a "B" isn't)
        # the action text runs right until the next chip on the same row, whose left edge is a
        # solid white column even when it touches this text and wasn't found as its own box
        end = min([b[0] for b in boxes if b[0] > x1 and abs(b[1] - y0) < h] + [x1 + 12 * h, W])
        if end - x1 < h:
            continue                # at the screen edge: no text to check it against
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


def white_mask(img):
    """White label fill (the cup rack, bin and customers' stations aren't highlighted)."""
    H, W = img.shape[:2]
    return cv2.inRange(img, (245, 245, 245), (255, 255, 255)) & world_mask(W, H)


def find_station_label(img, names, near=None, chevron=True):
    """Label of the target station. 1) The gold chevron (only the target has one, and OCR
    misreads labels drawn over each other). 2) No chevron: OCR the gold, then the white labels.
    With near (a previous Target) only the candidates close to it are read, but still read:
    tracking by position alone drifted onto the Boba Pot next to the Cup Rack."""
    W = img.shape[1]
    gold = highlight_mask(img)
    if chevron:
        chevrons = find_chevrons(img, gold)
        if near is not None and chevrons:
            chevrons = [min(chevrons, key=lambda c: abs((c[0] + c[2]) / 2 - near.cx))]
        if len(chevrons) == 1:
            return Target(chevron_target(img, chevrons[0], gold).box, names[0])
    others = [n for st in config.STATIONS.values() for n in st["names"] if n not in names]
    for m, limit in ((gold, 4), (white_mask(img), 12)):
        boxes = find_labels(img, m)
        if near is not None:
            boxes = sorted((b for b in boxes if abs((b[0] + b[2]) / 2 - near.cx) < 0.25 * W),
                           key=lambda b: abs((b[0] + b[2]) / 2 - near.cx) + abs(b[1] - near.box[1]))
            limit = 2
        for b in boxes[:limit]:
            text = read_label(img, b, m)
            match = max(fuzzy_in(text, n) for n in names)
            if match >= config.MATCH_CUTOFF and match > max((fuzzy_in(text, n) for n in others), default=0):
                return Target(b, text)
            if config.DEBUG:
                print(f"[label] rejected {text!r} at {b}")
    return None


def other_phrases(phrases):
    """Every known prompt / action text that is not one of phrases."""
    known = [p for st in config.STATIONS.values() for p in st["prompts"]]
    known += [a for aliases in config.ACTIONS.values() for a in aliases]
    known += config.ASK_PROMPTS + config.SERVE_PROMPTS
    known += [n for st in config.STATIONS.values() for n in st["names"]]
    own = {squash(p) for p in phrases}
    # also drop parts of our own phrases: the name "Milk" would out-match "Pour the Milk"
    return [p for p in known if not any(squash(p) in o for o in own)]


def find_chip(img, label, phrases, chips=None):
    """The prompt chip under a label whose text matches one of phrases better than any other
    station's prompt. No text match, no chip: using the wrong station ruins the drink."""
    x0, y0, x1, y1 = label.box
    w, h = x1 - x0, y1 - y0
    others = other_phrases(phrases)
    best, best_score = None, 0.0
    for chip in find_chips(img) if chips is None else chips:
        # the key box sits left of centre under the label: ~35 px past a short one like "Milk"
        if not y1 - 0.3 * h <= chip.box[1] <= y1 + 3 * h or not x0 - max(0.5 * w, 3 * h) <= chip.x <= x1:
            continue
        match = max((fuzzy_in(chip.text, p) for p in phrases), default=0.0)
        other = max((fuzzy_in(chip.text, p) for p in others), default=0.0)
        if match < config.MATCH_CUTOFF or match <= other:
            if config.DEBUG:
                print(f"[chip] under {label.text!r}: rejected {chip.key} {chip.text!r} ({match:.2f} vs {other:.2f})")
            continue
        score = match - 0.2 * abs(chip.x - label.cx) / max(w, 1)
        if score > best_score:
            best, best_score = chip, score
    return best


def find_prompt(img, prompts):
    """A chip anywhere on screen whose text matches one of prompts -> Target."""
    others = other_phrases(prompts)
    best, best_score = None, config.MATCH_CUTOFF
    for chip in find_chips(img):
        score = max(fuzzy_in(chip.text, p) for p in prompts)
        if score >= best_score and score > max(fuzzy_in(chip.text, p) for p in others):
            best, best_score = Target(chip.box, chip.text, chip), score
    return best


def customer_name(img, chip):
    """Display name of the customer a chip belongs to: the small white name tag at the fixed
    offset from the chip where the asked customer's tag sat (other players stand close by)."""
    k = img.shape[0] / REF_H
    ex, ey = chip.box[0] + config.NAME_TAG_OFFSET[0] * k, chip.box[1] - config.NAME_TAG_OFFSET[1] * k
    m = white_mask(img)
    tags = [b for b in name_tags(img, m) if abs((b[0] + b[2]) / 2 - ex) + abs(b[3] - ey) < 80 * k]
    if not tags:
        return None
    b = min(tags, key=lambda b: abs((b[0] + b[2]) / 2 - ex) + abs(b[3] - ey))
    name = read_label(img, b, m).strip()
    stations = [n for st in config.STATIONS.values() for n in st["names"]]
    if len(squash(name)) >= 2 and max(fuzzy_in(name, s) for s in stations) < config.MATCH_CUTOFF:
        return name
    return None


def name_tags(img, m=None):
    """Small white player name tags (display names), widest first."""
    k = img.shape[0] / REF_H
    m = white_mask(img) if m is None else m
    return [b for b in find_labels(img, m) if b[3] - b[1] <= 22 * k and b[2] - b[0] <= 220 * k]


def find_landmark(img, landmarks, customer=None):
    """What to walk toward when no prompt is in reach: the remembered customer's name tag,
    else a big white station label."""
    if customer:
        m = white_mask(img)
        for b in name_tags(img, m)[:10]:
            if fuzzy_in(read_label(img, b, m), customer) >= config.MATCH_CUTOFF:
                return Target(b, customer)
    return find_station_label(img, landmarks, chevron=False)     # a chevron there marks something else


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
INPUT_MOUSE, MOVE, LEFTDOWN, LEFTUP, VIRTUALDESK, ABSOLUTE = 0, 0x0001, 0x0002, 0x0004, 0x4000, 0x8000
ULONG_PTR = ctypes.c_ulong if ctypes.sizeof(ctypes.c_void_p) == 4 else ctypes.c_ulonglong


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ULONG_PTR)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]
    _fields_ = [("type", ctypes.c_ulong), ("u", _U)]


# Keys go out as scancodes; arrows need the extended flag or Roblox reads them as numpad keys.
SCANCODES = {"w": 0x11, "a": 0x1E, "s": 0x1F, "d": 0x20, "e": 0x12, "space": 0x39,
             "up": (0x48, True), "down": (0x50, True), "left": (0x4B, True), "right": (0x4D, True)}


def send_key(key, down):
    code = SCANCODES[key]
    scan, extended = code if isinstance(code, tuple) else (code, False)
    inp = INPUT(1)
    inp.u.ki = KEYBDINPUT(0, scan, 0x0008 | (0 if down else 0x0002) | (0x0001 if extended else 0), 0, 0)
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def _mouse(flags, x, y):
    u32 = ctypes.windll.user32
    # normalise over the whole virtual desktop, so Roblox on a second monitor works
    vx, vy, vw, vh = (u32.GetSystemMetrics(i) for i in (76, 77, 78, 79))
    ax = int((x - vx) * 65535 / max(1, vw - 1))
    ay = int((y - vy) * 65535 / max(1, vh - 1))
    inp = INPUT(INPUT_MOUSE)
    inp.u.mi = MOUSEINPUT(ax, ay, 0, flags | ABSOLUTE | VIRTUALDESK, 0, u32.GetMessageExtraInfo())
    u32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def click(x, y, hold=0.02):
    """Glide the cursor to screen (x, y) so Roblox registers the hover, then left click
    (prompt chips need the button held, like E)."""
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
    time.sleep(hold)
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
    route: list = field(default_factory=lambda: coffee.route(None, None))    # (key, label) per panel step
    customer: str = None
    current: str = None       # key of the step being worked on ("bin" while binning a ruined drink)
    served: int = 0
    goal: str = None          # map target being walked to
    since: float = field(default_factory=time.time)     # when `current` started
    took: dict = field(default_factory=dict)            # step key -> seconds it took this order
    order_times: list = field(default_factory=list)     # seconds per served order
    started: float = field(default_factory=time.time)
    focused: bool = True

    def enter(self, key):
        """Mark the step now being worked on; closes the timer of the previous one."""
        if key != self.current:
            if self.current is not None:
                self.took[self.current] = time.time() - self.since
            self.current, self.since = key, time.time()


class Bot:
    def __init__(self, enabled):
        self.enabled = enabled            # threading.Event, F6 toggles it
        self.status = Status()
        self.held = set()
        self.order = None                 # (drink, syrup) of the current customer
        self.customer = None              # their name tag, remembered to find them again for serving
        self.step = None                  # Step being worked on
        self.fails = {}                   # step key -> attempts without progress
        self.stuck_count = 0              # unstick() attempts, grows the sideways slide
        self._last_check = 0.0
        self.scr = None
        self.nav = None                   # nav.Navigator once a map.json exists (tools/mapper.py)
        self.used_at = None               # where we stood when using the last chip
        try:
            import nav
            cafe = nav.Map.load()
            self.nav = nav.Navigator(cafe) if cafe else None
        except Exception as e:            # a broken map must never stop the bot: vision still works
            print(f"[nav] map not used: {e!r}")

    # ---------------------------------------------------------- plumbing
    def say(self, state):
        self.status.state = state
        if config.DEBUG:
            print(f"[bot] {state}")

    def ready(self):
        self.status.focused = self.scr.find() is not None and self.scr.focused()
        ok = self.enabled.is_set() and self.status.focused
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
        if key not in self.held:
            send_key(key, True)
            self.held.add(key)

    def release(self, key):
        if key in self.held:
            send_key(key, False)
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

    def click_client(self, x, y, hold=0.02):
        click(*self.scr.to_screen(x, y), hold)

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
        self.status.plan = coffee.recipe_plan(drink, syrup)
        self.status.route = coffee.route(drink, syrup)

    # ---------------------------------------------------------- navigation
    def scan(self, locate, slow):
        """Lazy search: turn right until locate(img) sees the target."""
        self.say("scanning (holding right arrow)")
        t0 = time.time()
        try:
            while time.time() - t0 < config.SEEK_TIMEOUT:
                self.check()
                # turn in steps and look while still: holding the key kept turning past the target
                self.tap(config.TURN_RIGHT, config.SCAN_STEP if slow else config.FAST_SCAN_STEP)
                time.sleep(0.12)
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
                if abs(err) > config.CENTER_TOL:
                    self.steer(err)
                    continue
                # walk in bursts and look while stopped: holding W walked past the chip
                self.tap(config.FORWARD, config.WALK_BURST)
                time.sleep(0.1)
                if abs(err) > config.STEER_TOL:
                    self.steer(err)

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

    def steer(self, err):
        """Fine turn pulses toward an offset (fraction of width): longer taps accelerate and overshoot."""
        turn = config.TURN_RIGHT if err > 0 else config.TURN_LEFT
        for _ in range(max(1, min(config.MAX_PULSES, round(abs(err) / config.TURN_PULSE)))):
            self.tap(turn, config.TURN_TAP)
            time.sleep(0.08)
        time.sleep(0.1)

    def unstick(self):
        """Walking into a wall (labels show through walls): back off and slide sideways, a
        little further and on the other side each time, to find the way around."""
        if self.nav:
            self.nav.stuck()
        self.stuck_count += 1
        side = config.STRAFE_LEFT if self.stuck_count % 2 else config.STRAFE_RIGHT
        self.say(f"stuck, backing off and strafing {side}")
        self.release_all()
        self.tap(config.BACK, 0.5)
        self.tap(side, min(0.6 + 0.4 * self.stuck_count, 2.5))     # no jump: it lands on the counter

    def wander(self):
        """Nothing found after a full turn: with a map, go back to the middle of the walked floor;
        without, back up for a wider view (walking forward at random led into side rooms)."""
        if self.nav and self.nav.nodes and self.nav.update(self.grab()):
            hub = max(range(len(self.nav.nodes)), key=lambda i: sum(i in e for e in self.nav.edges))
            self.say("nothing in view, back to the middle of the kitchen")
            if self.walk_to(self.nav.nodes[hub]):
                return
        self.say("nothing in view, backing up")
        self.tap(config.BACK, random.uniform(0.5, 1.0))
        self.tap(config.TURN_RIGHT, random.uniform(0.2, 0.5))

    # ---------------------------------------------------------- map navigation
    def localize(self, tries=4):
        """Pose from the labels in view; turn a quarter at a time if too few are visible."""
        for _ in range(tries):
            self.check()
            if self.nav.update(self.grab()):
                return True
            self.tap(config.TURN_RIGHT, config.FAST_SCAN_STEP)       # show other labels
            time.sleep(0.15)
        return False

    def face(self, xy):
        """Turn to look at map point xy: pulses of ~4.4 deg, dead-reckoned, with a fresh fix
        once close (localising costs ~0.5 s, turning without looking doesn't)."""
        for _ in range(12):
            err = self.nav.heading_error(xy)                 # + = left
            if abs(err) < config.FACE_TOL:
                if self.nav.update(self.grab()) and abs(self.nav.heading_error(xy)) < config.FACE_TOL:
                    return
                continue
            pulses = min(config.MAX_PULSES, max(1, round(abs(err) / config.TURN_PULSE_RAD)))
            self.steer(-math.copysign(pulses * config.TURN_PULSE, err))
            self.nav.turned(math.copysign(pulses * config.TURN_PULSE_RAD, err))

    def walk_to(self, xy, timeout=12.0):
        """Walk to map point xy in bursts, re-localising between them. False if stuck or lost."""
        t0, last, still = time.time(), None, 0
        while time.time() - t0 < timeout:
            self.check()
            pose = self.nav.pose
            if pose is None:
                return False
            if np.linalg.norm(pose[0] - xy) < config.ARRIVE * self.nav.map.D:
                return True
            self.face(xy)
            self.tap(config.FORWARD, config.WALK_BURST)
            time.sleep(0.1)
            if not self.nav.update(self.grab()):
                continue
            moved = 0 if last is None else np.linalg.norm(self.nav.pose[0] - last)
            still = still + 1 if last is not None and moved < 0.05 * self.nav.map.D else 0
            last = self.nav.pose[0]
            if still >= 3:
                self.unstick()
                return False
        return False

    def goto_map(self, target):
        """Walk along known floor to where target was used before (or the floor nearest its label),
        then face its label. The vision step after this finds and checks the chip as always."""
        if not self.nav or not self.localize():
            return False
        goal = self.nav.goal(target)
        if goal is None:
            return False
        self.status.goal = target
        self.say(f"map: heading to {target}")
        route = self.nav.route(goal)
        for i, wp in enumerate(route):
            nodes = [j for j, n in enumerate(self.nav.nodes) if np.allclose(n, wp)]
            here = self.nav.nearest(self.nav.pose[0])
            self.nav._target_edge = tuple(sorted((here, nodes[0]))) if nodes and here is not None else None
            if not self.walk_to(wp):
                self.say(f"map: lost the way to {target}, using vision")
                return False
        label = self.nav.map.labels.get(target)
        if label is not None:
            self.face(label)
        return True

    def learn_spot(self, target):
        """The last chip worked: remember where we stood for target."""
        if self.nav and self.used_at is not None:
            self.nav.learn(target, self.used_at)
        self.used_at = None

    def goto_station(self, name, phrases, slow=False):
        """slow: usually found by text, not a chevron (cup rack, bin), so scan in OCR-sized steps."""
        names, prompts = station_info(name)
        phrases = list(phrases) + prompts
        self.goto_map(name)

        def locate(img, near):
            return find_station_label(img, names, near)

        def chip_near(img, target):
            return find_chip(img, target, phrases)

        def recheck(img):
            label = find_station_label(img, names)
            return find_chip(img, label, phrases) if label else None

        return self.settled(self.navigate(locate, chip_near, slow=slow), recheck)

    def goto_text(self, prompts, landmarks, customer=None):
        def recheck(img):
            target = find_prompt(img, prompts)
            return target.chip if target else None

        return self.settled(self._goto_text(prompts, landmarks, customer), recheck)

    def settled(self, chip, recheck):
        """The camera kept moving while we looked: stop, let it settle, find the chip again."""
        if chip is None:
            return None
        self.release_all()
        time.sleep(config.SETTLE)
        chip = recheck(self.grab())
        if chip is None:
            self.say("chip moved away after settling")
        return chip

    def _goto_text(self, prompts, landmarks, customer=None):
        """Customer / bin: nothing is highlighted, so turn looking for their chip text. If no
        chip is in reach, walk at a landmark label (slow OCR, refreshed every few bursts)."""
        looks = [0]

        def locate(img, near):
            hit = find_prompt(img, prompts)
            if hit is not None:
                return hit
            looks[0] += 1
            if near is None or looks[0] % 3 == 0:
                return find_landmark(img, landmarks, customer)
            return Target(near.box, near.text, stale=True)

        target = self.scan(lambda img, near: find_prompt(img, prompts), slow=False)
        if target is not None:
            return target.chip
        self.say(f"no prompt in reach, looking for {customer or landmarks[0]} (slow OCR)")
        for _ in range(8):                  # roughly a full turn in coarse steps
            self.check()
            target = find_landmark(self.grab(), landmarks, customer)
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
        if self.nav:
            self.used_at = self.nav.pose[0] if self.nav.update(self.grab()) else None
        self.say(f"using {chip.text!r} ({chip.key})")
        if config.PRESS_E and chip.key == "e":
            self.tap(config.INTERACT, config.INTERACT_HOLD)     # the E chip under our target is ours
        else:
            self.click_client(chip.x, chip.y, config.INTERACT_HOLD)     # "Click" chips aren't the nearest prompt, E would miss them

    # ---------------------------------------------------------- one step
    def wait_for_change(self, step, timeout=config.ACTION_WAIT):
        t0 = time.time()
        while time.time() - t0 < timeout:
            self.check(panel_every=0)
            if coffee.find_track(self.grab()) is not None:     # a minigame we didn't expect
                coffee.pull_shot(self)
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
        self.status.enter(step.action if step.kind == "station" else step.kind)
        if coffee.dialogue_up(self.grab()):                     # the customer is still talking
            coffee.take_order(self)
        drink, syrup = self.order or (None, None)

        # a picker may still be open from a previous attempt
        want = config.MENU[drink] if step.kind == "cup" and drink else \
            [syrup] if step.action == "Pick a Flavour" and syrup else None
        if want and coffee.find_card(self.grab(), want):
            coffee.pick_card(self, want, want[0])
            self.wait_for_change(step)
            return

        if step.kind == "ask":
            self.goto_map("register")
            chip = self.goto_text(config.ASK_PROMPTS, config.COUNTER_LANDMARKS)
            if chip:
                self.customer = self.status.customer = customer_name(self.grab(), chip)
                self.say(f"customer: {self.customer or '?'}")
                self.use(chip)
                time.sleep(0.6)
                if not coffee.take_order(self):
                    self.say("didn't catch the order")
        elif step.kind == "serve":
            self.goto_map("register")
            chip = self.goto_text(config.SERVE_PROMPTS, config.COUNTER_LANDMARKS, self.customer)
            if chip:
                self.use(chip)
        elif step.kind == "bin":
            chip = self.goto_station("Bin", [], slow=True)
            if chip:
                self.use(chip)
        elif step.kind == "cup":
            if not drink:
                return self.need_order("cup")
            chip = self.goto_station("Cup Rack", [], slow=True)
            if chip:
                self.use(chip)
                time.sleep(0.5)
                coffee.grab_cup(self, drink)
        elif step.kind == "station":
            if step.action == "Pick a Flavour" and not (syrup or config.DEFAULT_SYRUP):
                return self.need_order("flavour")
            chip = self.goto_station(step.station, config.ACTIONS.get(step.action, [step.action]))
            if chip:
                self.use(chip)
                time.sleep(0.4)
                if step.action == "Pick a Flavour":
                    s = syrup or config.DEFAULT_SYRUP
                    coffee.add_flavour(self, s)
                elif step.action == "Pull the Shot":
                    coffee.pull_shot(self)
        if not self.wait_for_change(step):
            n = self.fails[step.key()] = self.fails.get(step.key(), 0) + 1
            if n % 3 == 0:
                self.unstick()
            return
        self.fails.pop(step.key(), None)
        self.learn_spot({"ask": "register", "serve": "register", "bin": "Bin", "cup": "Cup Rack"}.get(
            step.kind, step.station))
        self.stuck_count = 0
        if step.kind == "serve":            # next customer; an order can't be re-asked, so only now
            self.order = self.customer = self.status.customer = None
            self.status.order, self.status.plan = "-", ""
            s = self.status
            s.took[s.current] = time.time() - s.since
            s.order_times.append(sum(s.took.values()))
            s.route, s.took, s.served = coffee.route(None, None), {}, s.served + 1
            s.current, s.since = "ask", time.time()

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
                    if coffee.dialogue_up(self.grab()):
                        coffee.take_order(self)
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


# ================================================================ entry points

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
        dtext = coffee.dialogue_text(img)
        if coffee.is_dialogue(dtext):
            print(f" dialogue: {dtext!r} -> {coffee.parse_order(dtext)}")
        cards = [(a[0], coffee.find_card(img, a)) for a in list(config.MENU.values()) + [[s] for s in config.SYRUPS]]
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
        import overlay
        overlay.run()
