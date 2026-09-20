import cv2
import numpy as np
import mss
import win32gui
import ctypes
import easyocr
import re
import difflib
import time
import keyboard

from config import DEBUG, MENU, SYRUPS, ORDER

# dpi awareness, screen pix match win32 coordinates 1:1
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

# ---------------------------------------------------------------------------
# EasyOCR reader: create ONCE (loading the models takes a few seconds).
# gpu=True silently falls back to CPU if CUDA isn't available.
# ---------------------------------------------------------------------------
try:
    import torch
    USE_GPU = torch.cuda.is_available()
except Exception:
    USE_GPU = False

reader = easyocr.Reader(["id", "en"], gpu=USE_GPU)

OCR_SCALE = 1 # upscale factor for ocr, originally 3
ALLOWLIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz!?,.' " # char whitelist
MIN_CONFIDENCE = 0.2 # ignore detections below this confidence (0..1)


def get_roblox_client_rect():
    """Find Roblox window and return its exact INNER client viewport coordinates."""
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
    width = client_rect[2] - client_rect[0]
    height = client_rect[3] - client_rect[1]

    left, top = win32gui.ClientToScreen(hwnd, (0, 0))

    return {
        "hwnd": hwnd,
        "title": title,
        "left": left,
        "top": top,
        "width": width,
        "height": height,
    }


# shared parent (Roblox client viewport)
PARENT_POS_X, PARENT_POS_Y   = 0.0, 0.0
PARENT_SIZE_X, PARENT_SIZE_Y = 1.0, 1.0

# dialogue text box (scale based)
DLG_ANCHOR_X, DLG_ANCHOR_Y = 0.5, 1.0
DLG_POS_X, DLG_POS_Y       = 0.5, 0.862
DLG_SIZE_X, DLG_SIZE_Y     = 0.54, 0.16

def _parent_rect(window):
    pw = window["width"] * PARENT_SIZE_X
    ph = window["height"] * PARENT_SIZE_Y
    pl = window["left"] + window["width"] * PARENT_POS_X
    pt = window["top"] + window["height"] * PARENT_POS_Y
    return pl, pt, pw, ph


def get_order_crop_region(window):
    """Dialogue box in absolute screen coordinates."""
    pl, pt, pw, ph = _parent_rect(window)
    bw, bh = int(pw * DLG_SIZE_X), int(ph * DLG_SIZE_Y)
    return {
        "left": int(pl + pw * DLG_POS_X - bw * DLG_ANCHOR_X),
        "top": int(pt + ph * DLG_POS_Y - bh * DLG_ANCHOR_Y),
        "width": bw,
        "height": bh,
    }

# "Klik untuk lanjut" prompt region (scale based)
PROMPT_ANCHOR_X, PROMPT_ANCHOR_Y = 0.5, 0.0
PROMPT_POS_X, PROMPT_POS_Y       = 0.5, 0.876
PROMPT_SIZE_X, PROMPT_SIZE_Y     = 0.2, 0.04
PROMPT_TEXT = "klik untuk lanjut"
PROMPT_OCR_SCALE = 3
PROMPT_DEBUG = DEBUG
PROMPT_CUTOFF = 0.47
POLL_MS = 50

def _squash(s):
    return "".join(s.lower().split())


def get_prompt_region(window):
    pl, pt, pw, ph = _parent_rect(window)
    bw, bh = int(pw * PROMPT_SIZE_X), int(ph * PROMPT_SIZE_Y)
    return {
        "left": int(pl + pw * PROMPT_POS_X - bw * PROMPT_ANCHOR_X),
        "top": int(pt + ph * PROMPT_POS_Y - bh * PROMPT_ANCHOR_Y),
        "width": bw,
        "height": bh,
    }

def extract_order(text):
    """'Halo! Aku pesan [order] ya.' -> '[order]' (falls back to raw text)."""
    m = re.search(r"pesan\s+(.+?)\s+ya\b", text, re.IGNORECASE)
    return m.group(1).strip() if m else text


def _best_match(text, candidates, cutoff=0.7):
    """Best fuzzy match of any candidate against same-length word windows of text."""
    words = text.split()
    best, best_score = None, cutoff
    for cand in candidates:
        n = len(cand.split())
        for i in range(len(words) - n + 1):
            chunk = " ".join(words[i:i + n])
            score = difflib.SequenceMatcher(None, chunk.lower(), cand.lower()).ratio()
            if score > best_score:
                best, best_score = cand, score
    return best


def snap_order(text):
    """Snap noisy OCR text to (menu, syrup); either may be None."""
    return _best_match(text, MENU), _best_match(text, SYRUPS)

def prepare_for_ocr(frame, scale=OCR_SCALE):
    """EasyOCR (CRAFT + CRNN) works best on natural, colored images, so we only
    upscale. No binarization: the dark outline around the letters is a useful
    cue for the detector, and thresholding tends to destroy it."""
    return cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)


def read_customer_order(debug=False):
    roblox_window = get_roblox_client_rect()
    if roblox_window is None:
        raise RuntimeError("Could not find a visible Roblox window.")

    crop_region = get_order_crop_region(roblox_window)

    with mss.mss() as sct:
        screenshot = np.array(sct.grab(crop_region))
    frame = cv2.cvtColor(screenshot, cv2.COLOR_BGRA2BGR)
    img = prepare_for_ocr(frame)

    if debug:
        cv2.imwrite("debug_crop.png", frame)
        cv2.imwrite("debug_scaled.png", img)

    # keep only the text boxes with confidence >= MIN_CONFIDENCE, and sort them by their left edge
    results = reader.readtext(
        img,
        detail=1,
        paragraph=False,
        allowlist=ALLOWLIST,
        text_threshold=0.6,
        low_text=0.3,
        width_ths=0.8,      # merge words that are close horizontally into one box
        mag_ratio=1.0,      # we already upscaled ourselves
    )

    kept = [(box, text, conf) for box, text, conf in results if conf >= MIN_CONFIDENCE]
    # sort by the left edge (x of top-left corner) of each box
    kept.sort(key=lambda r: r[0][0][0])

    order_text = " ".join(text.strip() for _, text, _ in kept).strip()

    if debug:
        for box, text, conf in kept:
            print(f"  {conf:.2f}  {text!r}")

    raw = extract_order(order_text)
    return raw, *snap_order(raw)

def prompt_visible(window):
    with mss.mss() as sct:
        shot = np.array(sct.grab(get_prompt_region(window)))
    img = prepare_for_ocr(cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR), scale=PROMPT_OCR_SCALE)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    # skip the CRAFT detector: treat the whole strip as one text line
    parts = reader.recognize(gray, horizontal_list=[[0, w, 0, h]], free_list=[],
                             detail=0, allowlist=ALLOWLIST)
    text = " ".join(parts)
    score = difflib.SequenceMatcher(None, _squash(text), _squash(PROMPT_TEXT)).ratio()
    if PROMPT_DEBUG:
        print(f"[prompt score] {score:.2f}")
    return score >= PROMPT_CUTOFF


def main(on_tick=None, enabled_event=None, on_status=None, schedule=None):
    was_visible = [False]
    last_shown = ["-"]

    def set_status(text, background):
        if on_status:
            on_status(text, background)

    def poll():
        t0 = time.perf_counter()
        try:
            is_enabled = enabled_event.is_set() if enabled_event is not None else True
            if not is_enabled:
                set_status(f"Order: {last_shown[0]} [PAUSED]", "#552222")
                was_visible[0] = False
            else:
                set_status(f"Order: {last_shown[0]}", "#225522")
                window = get_roblox_client_rect()
                visible = window is not None and prompt_visible(window)
                if visible and not was_visible[0]:  # rising edge only
                    raw, menu, syrup = read_customer_order()
                    if menu or syrup:
                        try:
                            keyboard.send("space")
                        except Exception:
                            pass
                    ORDER.update(menu=menu, syrup=syrup)
                    shown = " ".join(p for p in (menu, syrup) if p) or raw
                    last_shown[0] = shown
                    set_status(f"Order: {shown}", "#225522")
                    if DEBUG:
                        print(shown)
                was_visible[0] = visible
                if window is not None and on_tick:
                    on_tick(window)
        except Exception as e:
            set_status(f"error: {e}", "#552222")
        elapsed_ms = (time.perf_counter() - t0) * 1000
        if schedule:
            schedule(max(1, int(POLL_MS - elapsed_ms)), poll)

    if DEBUG:
        print("OCR ready.")
    poll()


if __name__ == "__main__":
    main()