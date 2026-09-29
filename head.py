"""The player's own head on screen: its width in pixels is inversely proportional to the camera's
distance, which no label bearing can tell (the camera zooms, and walls pull it in).

The third-person camera looks at the character, so the head sits on the screen's centre column.
Skin is a pale, low-saturation pink (HSV H 0-15, S 45-115, V >= 150); the wood and chairs are
the same hue but far more saturated.
"""
import cv2
import numpy as np

SKIN_LO, SKIN_HI = (0, 45, 150), (15, 115, 255)
CENTRE_TOL = 0.12          # |head centre x - W/2| / W
ASPECT = (0.6, 1.7)        # Roblox heads are about square


def head_box(img):
    """(x0, y0, x1, y1) of the player's head, or None."""
    H, W = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, SKIN_LO, SKIN_HI)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    n, _, stats, cents = cv2.connectedComponentsWithStats(m)
    best, best_d = None, None
    for (x, y, w, h, area), (cx, cy) in zip(stats[1:], cents[1:]):
        if w < 0.012 * W or not ASPECT[0] <= w / max(h, 1) <= ASPECT[1] or area < 0.55 * w * h:
            continue
        if abs(cx - W / 2) > CENTRE_TOL * W or not 0.15 * H < cy < 0.85 * H:
            continue
        d = abs(cx - W / 2) + 0.3 * abs(cy - H / 2)
        if best is None or d < best_d:
            best, best_d = (int(x), int(y), int(x + w), int(y + h)), d
    return best


def head_width(img):
    """Head width as a fraction of the screen width, or None."""
    b = head_box(img)
    return None if b is None else (b[2] - b[0]) / img.shape[1]
