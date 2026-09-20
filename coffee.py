import ctypes
import time
import warnings
from collections import deque

import cv2
import keyboard
import mss
import numpy as np
import win32gui

from config import DEBUG

# per-monitor DPI awareness so screen pixels match win32 coordinates 1:1
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

# Set high-precision 1ms timer resolution on Windows for precise 5ms sleep & velocity tracking
try:
    ctypes.windll.winmm.timeBeginPeriod(1)
except Exception:
    pass

# ---------------- Studio layout (from your data) ----------------
FRAME_POS_Y = 0.9                      # frame: anchor (0.5, 1), position (0.5, 0.9)
FRAME_W, FRAME_H = 430, 128
TRACK_X, TRACK_Y, TRACK_W, TRACK_H = 14, 32, 320, 34
ZONE_W_RATIO = 0.2
NEEDLE_W = 30

# ---------------- Detection / tuning ----------------
UI_SCALE = 2.0        # your bar is ~643 px wide = 320 * 2
MINIGAME_SCALE = 2.0
MINIGAME_DY = 15
NEEDLE_CY = 0.0       # cup label center vs track center, in studio px (tune if the box sits high/low)
EDGE_MIN = 80         # min summed edge strength for a zone hit (tune with the edge= print)
ORANGE_LO = (0, 100, 220)              # BGR range of the cup's orange label
ORANGE_HI = (70, 170, 255)
MIN_ORANGE = 40                        # min orange px = MIN_ORANGE * scale^2
GRADIENT_MIN = 50                      # min (left - right) brightness of the track
LOST_FRAMES = 60                       # frames without needle -> round over (increased to allow out-of-zone recovery)
LOOKAHEAD = 0.08                       # seconds of velocity prediction
DEADBAND = 0.015                       # fraction of track width


def get_roblox_client_rect():
    def cb(hwnd, windows):
        if win32gui.IsWindowVisible(hwnd) and "roblox" in win32gui.GetWindowText(hwnd).lower():
            windows.append(hwnd)
    windows = []
    win32gui.EnumWindows(cb, windows)
    if not windows:
        return None
    hwnd = windows[0]
    _, _, w, h = win32gui.GetClientRect(hwnd)
    left, top = win32gui.ClientToScreen(hwnd, (0, 0))
    return {"left": left, "top": top, "width": w, "height": h}


def track_rect(W, H, s, dy=0):
    """Track rectangle in client coordinates (dy = vertical correction from detect)."""
    frame_bottom = FRAME_POS_Y * H
    frame_left = W / 2 - FRAME_W * s / 2
    x = frame_left + TRACK_X * s
    y = frame_bottom - FRAME_H * s + TRACK_Y * s + dy
    return int(x), int(y), int(TRACK_W * s), int(TRACK_H * s)

def grab(sct, region):
    return np.ascontiguousarray(np.array(sct.grab(region))[:, :, :3])


def orange_count(bgr):
    return cv2.countNonZero(cv2.inRange(bgr, ORANGE_LO, ORANGE_HI))


def detect(img, W, H):
    """Return the fixed layout only when the coffee needle is actually visible."""
    s, dy = MINIGAME_SCALE, MINIGAME_DY
    x, y, w, h = track_rect(W, H, s, dy)
    if x < 0 or y < 0 or x + w > W or y + h > H:
        return None

    # The orange cup/needle identifies an active minigame.  Without this
    # check, the worker locks on to choice dialogs and keeps handling Space.
    strip = img[y:y + h, x:x + w]
    if orange_count(strip) < MIN_ORANGE * s * s:
        return None
    return s, dy


def main(enabled_event=None):
    held = False

    def set_key(want):
        nonlocal held
        if want and not held:
            keyboard.press("space")
        elif not want and held:
            keyboard.release("space")
        held = want

    lock = None
    hist = deque(maxlen=90)
    bg = None
    lost = 0
    frame_i = 0
    prev = None                       # (t, needle_x, zone_c)
    needle_v = zone_v = 0.0
    zone_c = None
    last_zone_c = None
    last_print = 0.0

    if DEBUG:
        print("Coffee bot worker started.")
    with mss.mss() as sct:
        while True:
            win = get_roblox_client_rect()
            is_on = enabled_event.is_set() if enabled_event is not None else True
            if not is_on or win is None:
                set_key(False)
                time.sleep(0.2)
                continue
            W, H = win["width"], win["height"]

            # ---- idle: look for the minigame ----
            if lock is None:
                img = grab(sct, win)
                lock = detect(img, W, H)
                if lock:
                    hist.clear(); bg = None; lost = 0; prev = None; frame_i = 0
                    needle_v = zone_v = 0.0; zone_c = None; last_zone_c = None
                    if DEBUG:
                        print(f"Minigame found: scale={lock[0]:.2f}, dy={lock[1]}")
                else:
                    time.sleep(0.1)
                continue

            # ---- tracking ----
            s, dy = lock
            x, y, w, h = track_rect(W, H, s, dy)
            region = {"left": win["left"] + x, "top": win["top"] + y, "width": w, "height": h}
            strip = grab(sct, region)

            m = cv2.inRange(strip, ORANGE_LO, ORANGE_HI)
            if cv2.countNonZero(m) < MIN_ORANGE * s * s:
                # The control is no longer visible (for example, a Choice
                # dialog replaced it).  Stop sending Space immediately while
                # retaining the short loss window for a transient bad frame.
                set_key(False)
                lost += 1
                if lost > LOST_FRAMES:
                    set_key(False)
                    lock = None
                    if DEBUG:
                        print("Round over.")
                continue
            lost = 0
            colsum = m.sum(axis=0).astype(np.float32)
            cols = np.arange(w, dtype=np.float32)
            needle_x = float((colsum * cols).sum() / colsum.sum())

            # zone = two bright stroke edges exactly ZONE_W_RATIO * track width apart
            r0, r1 = int(.35 * h), int(.65 * h)
            band = strip[r0:r1].astype(np.float32).mean(axis=0)          # (w, 3)
            cover = np.abs(cols - needle_x) < 0.5 * NEEDLE_W * s         # cup hides the zone here
            k = max(2, int(2 * s))
            edge = np.zeros(w, np.float32)
            edge[k // 2:k // 2 + w - k] = np.linalg.norm(band[k:] - band[:-k], axis=1)
            edge = np.clip(edge - np.median(edge), 0, None)
            edge[cover] = 0
            edge[:int(4 * s)] = 0
            edge[-int(4 * s):] = 0                                       # bar end caps
            tol = int(3 * s)
            edge = cv2.dilate(edge.reshape(1, -1), np.ones((1, 2 * tol + 1), np.uint8)).ravel()

            now = time.perf_counter()
            zw = max(2, int(ZONE_W_RATIO * w))
            score = edge[:w - zw] + edge[zw:]
            i = int(np.argmax(score))
            if score[i] >= EDGE_MIN:
                new_zc = i + zw / 2
                if prev is not None and zone_c is not None:
                    dt = max(now - prev[0], 1e-3)
                    zone_v = 0.6 * zone_v + 0.4 * (new_zc - zone_c) / dt
                zone_c = new_zc
                last_zone_c = zone_c

            if prev is not None:
                dt = max(now - prev[0], 1e-3)
                needle_v = 0.6 * needle_v + 0.4 * (needle_x - prev[1]) / dt
            prev = (now, needle_x, zone_c)

            # Steering: guide needle back to zone even if needle is fully out of zone or zone_c is temporarily missing
            target_z = zone_c if zone_c is not None else (last_zone_c if last_zone_c is not None else 0.5 * w)
            err = (target_z + zone_v * LOOKAHEAD) - (needle_x + needle_v * LOOKAHEAD)
            db = DEADBAND * w
            if err > db:
                set_key(True)          # needle is left of target -> hold space to move right into zone
            elif err < -db:
                set_key(False)         # needle is right of target -> release space to move left into zone

            if DEBUG and now - last_print > 0.5:
                last_print = now
                print(f"needle={needle_x:6.1f}  zone={zone_c}  held={held}  edge={score.max():.0f}")
                cv2.imwrite("debug_coffee.png", strip)
            time.sleep(0.005)

    set_key(False)


if __name__ == "__main__":
    main()
