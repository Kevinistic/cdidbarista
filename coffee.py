"""EKSTRAKSI shot minigame: hold SPACE to lift the needle (cup icon), keep it in the Perfect zone."""
import time

import cv2
import keyboard
import numpy as np

from config import DEBUG

# The track is a wide tan -> dark-brown gradient bar in the bottom of the screen.
SEARCH_TOP = 0.6                        # fraction of client height to start looking
TRACK_LO, TRACK_HI = (5, 60, 20), (21, 255, 255)
TRACK_ASPECT = (5.0, 14.0)              # w / h, 400x42 at 1920x1172
TRACK_MIN_W = 0.1                       # fraction of client width
TRACK_FILL = 0.75

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
        if area < TRACK_FILL * w * h:
            continue
        v = hsv[y + h // 4:y + 3 * h // 4, :, 2]
        if np.median(v[:, x:x + w // 20]) < 150 or np.median(v[:, x + w - w // 20:x + w]) > 100:
            continue                    # not light-to-dark
        if best is None or w > best[2]:
            best = (int(x), int(y) + top, int(w), int(h))
    if DEBUG and best:
        print(f"[coffee] track at {best}")
    return best


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


def play(grab, is_running, timeout=4.0):
    """Play one round. grab(rect) -> BGR of that client rect; grab(None) -> full client.
    is_running() -> False aborts. Returns True if a round was played."""
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
            (keyboard.press if want else keyboard.release)("space")
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

            if DEBUG and now - last_print > 0.5:
                last_print = now
                print(f"[coffee] needle={needle_x:6.1f} zone={zone_c} held={held}")
            time.sleep(0.004)
    finally:
        set_key(False)
    return True
