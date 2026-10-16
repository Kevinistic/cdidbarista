import time

import cv2
import keyboard
import numpy as np

import vision
import winutil
from config import DEBUG, UI_SCALE

winutil.set_dpi_aware()  # screen pixels match win32 coordinates 1:1
winutil.raise_timer_resolution()  # precise 5ms sleep & velocity tracking

# ---------------- Studio layout (from your data) ----------------
FRAME_POS_Y = 0.9                      # frame: anchor (0.5, 1), position (0.5, 0.9)
FRAME_W, FRAME_H = 430, 128
TRACK_X, TRACK_Y, TRACK_W, TRACK_H = 14, 32, 320, 34
ZONE_W_RATIO = 0.2
NEEDLE_W = 30

# ---------------- Detection / tuning ----------------
MINIGAME_SCALE = UI_SCALE              # your bar is ~643 px wide = 320 * 2
MINIGAME_DY = 15
EDGE_MIN = 80         # min summed edge strength for a zone hit (tune with the edge= print)
ORANGE_LO = (0, 100, 220)              # BGR range of the cup's orange label
ORANGE_HI = (70, 170, 255)
MIN_ORANGE = 40                        # min orange px = MIN_ORANGE * scale^2
LOST_TIMEOUT = 0.3                     # seconds without needle -> round over (long enough for out-of-zone recovery)
IDLE_SLEEP = 0.02                      # seconds between checks while no minigame is on screen
LOOKAHEAD = 0.08                       # seconds of velocity prediction
DEADBAND = 0.015                       # fraction of track width
ARM_FRAMES = 8                         # consecutive frames with a detected zone before space may be pressed


def track_rect(W, H, s, dy=0):
    """Track rectangle in client coordinates (dy = vertical correction)."""
    frame_bottom = FRAME_POS_Y * H
    frame_left = W / 2 - FRAME_W * s / 2
    x = frame_left + TRACK_X * s
    y = frame_bottom - FRAME_H * s + TRACK_Y * s + dy
    return int(x), int(y), int(TRACK_W * s), int(TRACK_H * s)


def _worker(enabled_event):
    """Steer the minigame forever. Raises on unexpected errors; main() restarts it.

    Space is never left held, whatever happens in the loop.
    """
    held = False

    def set_key(want):
        nonlocal held
        if want and not held:
            keyboard.press("space")
        elif not want and held:
            keyboard.release("space")
        held = want

    s, dy = MINIGAME_SCALE, MINIGAME_DY  # the minigame's layout is fixed
    last_seen = None                  # perf_counter of the last frame with a needle; None = no round running
    prev = None                       # (t, needle_x, zone_c)
    needle_v = zone_v = 0.0
    zone_c = None
    last_zone_c = None
    last_print = 0.0
    armed = 0                         # consecutive zone detections; >= ARM_FRAMES means a real minigame

    if DEBUG:
        print("Coffee bot worker started.")
    try:
        while True:
            win = winutil.get_game_rect()
            is_on = enabled_event.is_set() if enabled_event is not None else True
            is_focused = win is not None and winutil.foreground_window() == win["hwnd"]

            if not is_on or win is None or not is_focused:
                set_key(False)
                last_seen = None
                time.sleep(0.2)
                continue
            W, H = win["width"], win["height"]

            # ---- look for / track the cup's orange label in the track strip ----
            x, y, w, h = track_rect(W, H, s, dy)
            region = {"left": win["left"] + x, "top": win["top"] + y, "width": w, "height": h}
            strip = vision.to_bgr(vision.grab(region))

            m = cv2.inRange(strip, ORANGE_LO, ORANGE_HI)
            now = time.perf_counter()
            if cv2.countNonZero(m) < MIN_ORANGE * s * s:
                if last_seen is not None and now - last_seen > LOST_TIMEOUT:
                    set_key(False)
                    last_seen = None
                    if DEBUG:
                        print("Round over.")
                time.sleep(IDLE_SLEEP if last_seen is None else 0.005)
                continue
            if last_seen is None:         # a new round starts: forget the previous one
                prev = None
                needle_v = zone_v = 0.0
                zone_c = last_zone_c = None
                armed = 0
                if DEBUG:
                    print(f"Minigame found: scale={s:.2f}, dy={dy}")
            last_seen = now
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

            zw = max(2, int(ZONE_W_RATIO * w))
            score = edge[:w - zw] + edge[zw:]
            i = int(np.argmax(score))
            if score[i] >= EDGE_MIN:
                if armed < ARM_FRAMES:
                    armed += 1
                new_zc = i + zw / 2
                if prev is not None and zone_c is not None:
                    dt = max(now - prev[0], 1e-3)
                    zone_v = 0.6 * zone_v + 0.4 * (new_zc - zone_c) / dt
                zone_c = new_zc
                last_zone_c = zone_c
            elif armed < ARM_FRAMES:
                armed = 0                  # not a real minigame (yet): needs consecutive zone hits

            if prev is not None:
                dt = max(now - prev[0], 1e-3)
                needle_v = 0.6 * needle_v + 0.4 * (needle_x - prev[1]) / dt
            prev = (now, needle_x, zone_c)

            if armed < ARM_FRAMES:
                # orange pixels alone (NPC dialog, UI, character) are not the minigame; never press space for them
                set_key(False)
                time.sleep(0.005)
                continue

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
    finally:
        try:
            keyboard.release("space")  # unconditional: `held` may be stale if press/release itself failed
        except Exception:
            pass


def main(enabled_event=None):
    while True:
        try:
            _worker(enabled_event)
        except Exception as exc:  # e.g. the screen grab failing while Roblox is being resized
            print(f"Coffee bot error: {exc!r}; restarting")
            time.sleep(0.5)


if __name__ == "__main__":
    main()
