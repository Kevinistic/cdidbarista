import re
import time

import cv2
import easyocr
import keyboard
import numpy as np

import textmatch
import vision
import winutil
from config import DEBUG, MENU, SYRUPS, ORDER, FRAME_DIFF_THRESHOLD, DIALOGUE_KEY

winutil.set_dpi_aware()  # screen pixels match win32 coordinates 1:1

# Overlay status tags (second argument of the on_status callback). None = running normally.
STATUS_PAUSED = "PAUSED"
STATUS_UNFOCUSED = "UNFOCUSED"
STATUS_NOT_FOUND = "NOT FOUND"
STATUS_ERROR = "ERROR"

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

# Reader construction loads the model files, but EasyOCR still performs some
# lazy setup on its first inference (notably CUDA initialization when enabled).
# Keep this state here so a warm-up is safe to call more than once.
_ocr_warmed_up = False

OCR_SCALE = 1 # upscale factor for ocr, originally 3
ALLOWLIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz!?,.' " # char whitelist
MIN_CONFIDENCE = 0.2 # ignore detections below this confidence (0..1)


def ocr_recognize_strip(gray):
    """Text of a grayscale image that is a single line of text (skips the CRAFT detector)."""
    h, w = gray.shape
    parts = reader.recognize(gray, horizontal_list=[[0, w, 0, h]], free_list=[],
                             detail=0, allowlist=ALLOWLIST)
    return " ".join(parts)


def warm_up_ocr():
    """Run both EasyOCR stages once before the bot starts polling.

    A blank, in-memory image is intentional: this primes the models without
    needing Roblox to be open or relying on any screen content.
    """
    global _ocr_warmed_up
    if _ocr_warmed_up:
        return

    # readtext exercises the CRAFT detector and recognition model together.
    blank = np.zeros((64, 512, 3), dtype=np.uint8)
    reader.readtext(blank, detail=0, allowlist=ALLOWLIST)
    _ocr_warmed_up = True


get_roblox_client_rect = winutil.get_game_rect

# Boxes are placed inside the Roblox client viewport (Roblox UI "scale" values):
# anchor, position and size are all fractions of the window.
# dialogue text box
DLG_ANCHOR, DLG_POS, DLG_SIZE = (0.5, 1.0), (0.5, 0.862), (0.54, 0.16)
# "Klik untuk lanjut" prompt strip
PROMPT_ANCHOR, PROMPT_POS, PROMPT_SIZE = (0.5, 0.0), (0.5, 0.876), (0.2, 0.04)
PROMPT_TEXT = "klik untuk lanjut"
PROMPT_OCR_SCALE = 3
PROMPT_DEBUG = DEBUG
PROMPT_CUTOFF = 0.47
POLL_MS = 50

# Per dialogue (while the "Klik untuk lanjut" prompt stays on screen):
READ_TRIES = 3             # OCR attempts to read the order (a miss is retried; the text may still be typing)
READ_GAP = 0.25            # seconds between those attempts
CLOSE_TRIES = 3            # key presses to close the dialogue; a re-press happens only if the prompt is still there
CLOSE_GAP = 0.4            # seconds to wait for the dialogue to close before pressing again
DIALOGUE_KEY_HOLD = 0.1    # seconds the key is held; a same-frame tap can be missed by Roblox


def _box(window, anchor, pos, size):
    """Screen-space region of a UI box given in fractions of the client viewport."""
    pw, ph = window["width"], window["height"]
    bw, bh = int(pw * size[0]), int(ph * size[1])
    return {
        "left": int(window["left"] + pw * pos[0] - bw * anchor[0]),
        "top": int(window["top"] + ph * pos[1] - bh * anchor[1]),
        "width": bw,
        "height": bh,
    }


def get_order_crop_region(window):
    """Dialogue box in absolute screen coordinates."""
    return _box(window, DLG_ANCHOR, DLG_POS, DLG_SIZE)


def get_prompt_region(window):
    return _box(window, PROMPT_ANCHOR, PROMPT_POS, PROMPT_SIZE)


def extract_order(text):
    """'Halo! Aku pesan [order] ya.' -> '[order]' (falls back to raw text)."""
    m = re.search(r"pesan\s+(.+?)\s+ya\b", text, re.IGNORECASE)
    return m.group(1).strip() if m else text


def snap_order(text):
    """Snap noisy OCR text to (menu, syrup); either may be None."""
    return textmatch.best_match(text, MENU), textmatch.best_match(text, SYRUPS)


def prepare_for_ocr(frame, scale=OCR_SCALE):
    """EasyOCR (CRAFT + CRNN) works best on natural, colored images, so we only
    upscale. No binarization: the dark outline around the letters is a useful
    cue for the detector, and thresholding tends to destroy it."""
    if scale == 1:
        return frame
    return cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)


def read_customer_order(debug=False, window=None):
    if window is None:
        window = get_roblox_client_rect()
    if window is None:
        raise RuntimeError("Could not find a visible Roblox window.")

    crop_region = get_order_crop_region(window)

    frame = vision.to_bgr(vision.grab(crop_region))
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


def _prompt_strip_score(shot):
    """Similarity (0..1) of the prompt strip's text to PROMPT_TEXT."""
    img = prepare_for_ocr(vision.to_bgr(shot), scale=PROMPT_OCR_SCALE)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return textmatch.ratio(ocr_recognize_strip(gray), PROMPT_TEXT)


# Frame-diff state for prompt_visible: skip OCR when the strip hasn't changed.
_prompt_gate = vision.FrameGate((32, 8), FRAME_DIFF_THRESHOLD)
_prompt_cached_result = False


def prompt_visible(window):
    global _prompt_cached_result
    shot = vision.grab(get_prompt_region(window))
    # If the strip is pixel-stable we can reuse the last OCR decision for free.
    if _prompt_gate.unchanged(shot):
        return _prompt_cached_result

    score = _prompt_strip_score(shot)
    if PROMPT_DEBUG:
        print(f"[prompt score] {score:.2f}")
    _prompt_cached_result = score >= PROMPT_CUTOFF
    return _prompt_cached_result


class _Poller:
    """Polls Roblox every POLL_MS and handles each customer dialogue.

    While the prompt is on screen: read the order (retrying a miss), then press DIALOGUE_KEY to close
    the dialogue, and press again only if the prompt is still there after CLOSE_GAP. The prompt
    staying visible is the verification, so nothing is pressed once the dialogue is gone.
    """

    def __init__(self, on_tick, enabled_event, on_status, schedule):
        self.on_tick = on_tick
        self.enabled_event = enabled_event
        self.on_status = on_status
        self.schedule = schedule
        self.was_visible = False
        self.last_shown = "-"
        self._reset_dialogue()

    def _reset_dialogue(self):
        self.read_tries = 0
        self.order_found = False
        self.close_presses = 0
        self.next_action = 0.0

    def _status(self, text, tag=None):
        if self.on_status:
            self.on_status(text, tag)

    def _read_order(self, window):
        """One OCR pass; publishes the result to ORDER. True if a drink or syrup was recognised."""
        raw, menu, syrup = read_customer_order(window=window)
        ORDER.update(menu=menu, syrup=syrup)
        shown = " ".join(p for p in (menu, syrup) if p) or raw
        self.last_shown = shown
        self._status(f"Order: {shown}")
        if DEBUG:
            print(shown)
        return bool(menu or syrup)

    @staticmethod
    def _press_dialogue_key():
        try:
            keyboard.press(DIALOGUE_KEY)
            time.sleep(DIALOGUE_KEY_HOLD)
        except Exception:
            pass
        finally:
            try:
                keyboard.release(DIALOGUE_KEY)
            except Exception:
                pass

    def _drive_dialogue(self, window):
        """Called every poll while the prompt is visible."""
        if time.perf_counter() < self.next_action:
            return
        if not self.order_found:
            if self.read_tries >= READ_TRIES:
                return
            self.read_tries += 1
            self.order_found = self._read_order(window)
            if not self.order_found:
                self.next_action = time.perf_counter() + READ_GAP
                return
        elif self.close_presses >= CLOSE_TRIES:
            return
        self._press_dialogue_key()
        self.close_presses += 1
        self.next_action = time.perf_counter() + CLOSE_GAP

    def _poll_once(self):
        if self.enabled_event is not None and not self.enabled_event.is_set():
            self._status(f"Order: {self.last_shown}", STATUS_PAUSED)
            self.was_visible = False
            return
        window = get_roblox_client_rect()
        if window is None:
            self._status(f"Order: {self.last_shown}", STATUS_NOT_FOUND)
            self.was_visible = False
        elif winutil.foreground_window() != window["hwnd"]:
            self._status(f"Order: {self.last_shown}", STATUS_UNFOCUSED)
            self.was_visible = False
        else:
            self._status(f"Order: {self.last_shown}")
            visible = prompt_visible(window)
            if visible:
                if not self.was_visible:  # rising edge: a new dialogue
                    self._reset_dialogue()
                self._drive_dialogue(window)
            self.was_visible = visible
            if self.on_tick:
                self.on_tick(window)

    def poll(self):
        t0 = time.perf_counter()
        try:
            self._poll_once()
        except Exception as e:
            self._status(f"error: {e}", STATUS_ERROR)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        if self.schedule:
            self.schedule(max(1, int(POLL_MS - elapsed_ms)), self.poll)


def main(on_tick=None, enabled_event=None, on_status=None, schedule=None):
    """Start polling. `on_status(text, tag)` gets one of the STATUS_* tags (None while running)."""
    poller = _Poller(on_tick, enabled_event, on_status, schedule)
    if DEBUG:
        print("OCR ready.")
    poller.poll()


if __name__ == "__main__":
    main()
