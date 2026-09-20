import time
import difflib

import cv2
import mss
import numpy as np
import win32api
import win32con
import ctypes

from config import DEBUG, SYRUPS, ORDER, UI_SCALE, FRAME_DIFF_THRESHOLD
from order import reader, prepare_for_ocr, ALLOWLIST, _squash  # importing order also loads the OCR model once

# Studio layout (Choice is centered, anchor 0.5/0.5)
CHOICE_W, CHOICE_H = 580, 480
GRID_X, GRID_Y = 14, 40
CELL_W, CELL_H, PAD = 104, 124, 8
COLS = (CHOICE_W - 28 + PAD) // (CELL_W + PAD)      # grid is 552 px wide -> 5
TITLE_X, TITLE_Y, TITLE_H = 14, 32, 26
TITLE_W = CHOICE_W - 28
TITLE_TEXT = "PILIH RASA"
TITLE_CUTOFF = 0.5          # tune with the debug print
TITLE_OCR_SCALE = 3


def _origin(win, s=UI_SCALE):
    """Top-left of Choice in client coordinates."""
    return win["width"] / 2 - CHOICE_W * s / 2, win["height"] / 2 - CHOICE_H * s / 2


_last_debug_save = 0.0
# Persistent mss screen-capture context — opened once, reused on every tick.
_sct = mss.mss()
# Frame diff state for choice_visible — skip OCR when the title region hasn't changed.
_prev_title_gray = None
_title_cached_result = False


def choice_visible(win, s=UI_SCALE):
    global _last_debug_save, _prev_title_gray, _title_cached_result
    ox, oy = _origin(win, s)
    rx, ry = int(ox + TITLE_X * s), int(oy + TITLE_Y * s)
    rw, rh = int(TITLE_W * s), int(TITLE_H * s)

    region = {
        "left": int(win["left"] + rx),
        "top": int(win["top"] + ry),
        "width": rw,
        "height": rh,
    }
    shot = np.array(_sct.grab(region))

    # Frame diff guard: compare a tiny grayscale thumbnail against the previous frame.
    # On a static screen this skips all OCR work at the cost of one cheap resize+diff.
    small = cv2.resize(shot[:, :, :3], (48, 8), interpolation=cv2.INTER_AREA)
    gray_small = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    if _prev_title_gray is not None:
        diff = float(np.mean(np.abs(gray_small.astype(np.float32) - _prev_title_gray.astype(np.float32))))
        if diff < FRAME_DIFF_THRESHOLD:
            return _title_cached_result
    _prev_title_gray = gray_small

    now = time.time()
    should_save = DEBUG and (now - _last_debug_save >= 1.0)
    if should_save:
        _last_debug_save = now
        full_shot = np.array(_sct.grab({"left": win["left"], "top": win["top"], "width": win["width"], "height": win["height"]}))
        full_bgr = cv2.cvtColor(full_shot, cv2.COLOR_BGRA2BGR)
        cv2.rectangle(full_bgr, (rx, ry), (rx + rw, ry + rh), (0, 255, 0), 2)
        cv2.imwrite("debug_syrup_full.png", full_bgr)

    img = prepare_for_ocr(cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR), scale=TITLE_OCR_SCALE)
    if should_save:
        cv2.imwrite("debug_syrup_title.png", img)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    parts = reader.recognize(gray, horizontal_list=[[0, w, 0, h]], free_list=[],
                             detail=0, allowlist=ALLOWLIST)
    raw_text = " ".join(parts)
    sq_raw = _squash(raw_text)
    sq_title = _squash(TITLE_TEXT)
    score = difflib.SequenceMatcher(None, sq_raw, sq_title).ratio()
    if DEBUG and should_save:
        print(f"[title score] {score:.2f} | text: {raw_text!r}")
    _title_cached_result = sq_title in sq_raw or score >= TITLE_CUTOFF
    return _title_cached_result


# Native Windows SendInput API (Roblox processes SendInput hardware events)
INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000

ULONG_PTR = ctypes.c_ulong if ctypes.sizeof(ctypes.c_void_p) == 4 else ctypes.c_ulonglong


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ULONG_PTR),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("u", INPUT_UNION),
    ]


def _send_mouse_input(flags, x=0, y=0):
    screen_w = ctypes.windll.user32.GetSystemMetrics(0)
    screen_h = ctypes.windll.user32.GetSystemMetrics(1)
    abs_x = int(x * 65535 / max(1, screen_w - 1))
    abs_y = int(y * 65535 / max(1, screen_h - 1))
    extra = ctypes.windll.user32.GetMessageExtraInfo()
    ii = INPUT_UNION()
    ii.mi = MOUSEINPUT(abs_x, abs_y, 0, flags, 0, extra)
    inp = INPUT(INPUT_MOUSE, ii)
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def cell_center(win, index, s=UI_SCALE):
    """Absolute screen position of the center of grid cell `index`."""
    ox, oy = _origin(win, s)
    row, col = divmod(index, COLS)          # StartCorner TopLeft, Horizontal fill
    x = ox + (GRID_X + col * (CELL_W + PAD) + CELL_W / 2) * s
    y = oy + (GRID_Y + row * (CELL_H + PAD) + CELL_H / 2) * s
    return int(win["left"] + x), int(win["top"] + y)


def click(x, y):
    """Smoothly moves cursor to (x, y) via SendInput hardware events so Roblox UI registers hover & click."""
    start_x, start_y = win32api.GetCursorPos()
    dist = int(((x - start_x) ** 2 + (y - start_y) ** 2) ** 0.5)
    steps = max(3, min(dist // 30, 12))

    for step in range(1, steps + 1):
        cx = int(start_x + (x - start_x) * step / steps)
        cy = int(start_y + (y - start_y) * step / steps)
        win32api.SetCursorPos((cx, cy))
        _send_mouse_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, cx, cy)
        time.sleep(0.003)

    win32api.SetCursorPos((x, y))
    _send_mouse_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, x, y)
    time.sleep(0.01)  # 10ms frame sync for Roblox engine
    _send_mouse_input(MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE, x, y)
    time.sleep(0.01)
    _send_mouse_input(MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE, x, y)


_was_visible = False


def tick(win):
    """Called by order.py's poll loop; clicks the ordered syrup on the Choice screen's rising edge."""
    global _was_visible
    if not ORDER["syrup"]:
        _was_visible = False
        return
    visible = choice_visible(win)
    if visible and not _was_visible:
        time.sleep(0.05)                     # let the grid finish any open tween
        if ORDER["syrup"] in SYRUPS:
            x, y = cell_center(win, SYRUPS.index(ORDER["syrup"]))
            click(x, y)
            if DEBUG:
                print(f"Selected syrup: {ORDER['syrup']}")
    _was_visible = visible
