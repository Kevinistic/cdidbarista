"""Clicks the ordered option on Roblox's ChoicePicker screen (cup.py = drink, syrup.py = flavour).

Both pickers use the same centered "Choice" frame and differ only in the title text and which
ORDER entry they follow, so the layout, title OCR and mouse code live here once.
"""
import time

import cv2

import order
import textmatch
import vision
import winutil
from config import DEBUG, ORDER, UI_SCALE, FRAME_DIFF_THRESHOLD

# Studio layout (Choice is centered, anchor 0.5/0.5)
CHOICE_W, CHOICE_H = 580, 480
GRID_X, GRID_Y = 14, 40
CELL_W, CELL_H, PAD = 104, 124, 8
COLS = (CHOICE_W - 28 + PAD) // (CELL_W + PAD)      # grid is 552 px wide -> 5
TITLE_X, TITLE_Y, TITLE_H = 14, 32, 26
TITLE_W = CHOICE_W - 500
TITLE_CUTOFF = 0.5          # tune with the debug print
TITLE_OCR_SCALE = 3
CLICK_TRIES = 3             # clicks per opening of a picker (the first plus re-clicks while its title stays up)
CLICK_GAP = 0.6             # seconds to wait for the picker to close before clicking again


def _origin(win, s=UI_SCALE):
    """Top-left of Choice in client coordinates."""
    return win["width"] / 2 - CHOICE_W * s / 2, win["height"] / 2 - CHOICE_H * s / 2


class _TitleReader:
    """OCR of the picker's title strip.

    The cup and syrup pickers watch the very same strip, so one read serves both: the frame gate
    makes the second picker's call in a tick return the text the first one just read.
    """

    def __init__(self):
        self._gate = vision.FrameGate((48, 8), FRAME_DIFF_THRESHOLD)  # skip OCR while the strip is static
        self._text = ""
        self._last_debug_save = 0.0

    def read(self, win, s=UI_SCALE):
        ox, oy = _origin(win, s)
        rx, ry = int(ox + TITLE_X * s), int(oy + TITLE_Y * s)
        rw, rh = int(TITLE_W * s), int(TITLE_H * s)
        shot = vision.grab({"left": int(win["left"] + rx), "top": int(win["top"] + ry),
                            "width": rw, "height": rh})
        if self._gate.unchanged(shot):
            return self._text

        now = time.time()
        should_save = DEBUG and (now - self._last_debug_save >= 1.0)
        if should_save:
            self._last_debug_save = now
            full_bgr = vision.to_bgr(vision.grab({"left": win["left"], "top": win["top"],
                                                  "width": win["width"], "height": win["height"]}))
            cv2.rectangle(full_bgr, (rx, ry), (rx + rw, ry + rh), (0, 255, 0), 2)
            cv2.imwrite("debug_choice_full.png", full_bgr)

        img = order.prepare_for_ocr(vision.to_bgr(shot), scale=TITLE_OCR_SCALE)
        if should_save:
            cv2.imwrite("debug_choice_title.png", img)

        self._text = order.ocr_recognize_strip(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
        if should_save:
            print(f"[choice title] text: {self._text!r}")
        return self._text


_titles = _TitleReader()


def cell_center(win, index, s=UI_SCALE):
    """Absolute screen position of the center of grid cell `index`."""
    ox, oy = _origin(win, s)
    row, col = divmod(index, COLS)          # StartCorner TopLeft, Horizontal fill
    x = ox + (GRID_X + col * (CELL_W + PAD) + CELL_W / 2) * s
    y = oy + (GRID_Y + row * (CELL_H + PAD) + CELL_H / 2) * s
    return int(win["left"] + x), int(win["top"] + y)


def click(x, y):
    """Smoothly moves cursor to (x, y), then left-clicks, so Roblox UI registers hover & click."""
    start_x, start_y = winutil.cursor_pos()
    dist = int(((x - start_x) ** 2 + (y - start_y) ** 2) ** 0.5)
    steps = max(3, min(dist // 30, 12))

    for step in range(1, steps + 1):
        winutil.mouse_move(int(start_x + (x - start_x) * step / steps),
                           int(start_y + (y - start_y) * step / steps))
        time.sleep(0.003)

    winutil.mouse_move(x, y)
    time.sleep(0.01)  # 10ms frame sync for Roblox engine
    winutil.mouse_button(x, y, True)
    time.sleep(0.01)
    winutil.mouse_button(x, y, False)


class ChoicePicker:
    """Clicks ORDER[order_key]'s cell in `options` when the picker titled `title` appears.

    The picker's title still being on screen CLICK_GAP after a click means the click did not register,
    so the cell is clicked again (at most CLICK_TRIES clicks per opening of the picker).
    """

    def __init__(self, title, order_key, options):
        self.title = title
        self.order_key = order_key
        self.options = options
        self._clicks = 0              # clicks made during the current opening of the picker
        self._last_click = 0.0

    def visible(self, win, s=UI_SCALE):
        raw_text = _titles.read(win, s)
        sq_title = textmatch.squash(self.title)
        return sq_title in textmatch.squash(raw_text) or textmatch.ratio(raw_text, self.title) >= TITLE_CUTOFF

    def _click_choice(self, win):
        choice = ORDER[self.order_key]
        if choice not in self.options:
            self._clicks = CLICK_TRIES    # nothing to click for this order: don't try again
            return
        x, y = cell_center(win, self.options.index(choice))
        click(x, y)
        self._clicks += 1
        self._last_click = time.perf_counter()
        if DEBUG:
            retry = f" (retry {self._clicks - 1})" if self._clicks > 1 else ""
            print(f"Selected {self.order_key}: {choice}{retry}")

    def tick(self, win):
        """Called by order.py's poll loop; clicks the ordered option when the picker appears, and again if it stays."""
        if not ORDER[self.order_key]:
            self._clicks = 0
            return
        if not self.visible(win):
            self._clicks = 0              # closed (or never opened): the next opening starts fresh
        elif self._clicks == 0:
            time.sleep(0.05)              # let the grid finish any open tween
            self._click_choice(win)
        elif self._clicks < CLICK_TRIES and time.perf_counter() - self._last_click >= CLICK_GAP:
            self._click_choice(win)
