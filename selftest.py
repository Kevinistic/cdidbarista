"""Platform check: does the bot see the game window, capture it, and find the hint?

    python selftest.py            # read-only: window lookup, capture, hint crop, OCR
    python selftest.py --walk     # also taps W for 0.3s in the game (tests key injection)
    python selftest.py --hotkey   # also waits 15s for the toggle key (tests global hotkeys)

Run it with the game window visible and the Barista panel open. Writes selftest_window.png and
selftest_hint.png so you can look at exactly what the bot sees.
"""
import sys
import time

import cv2

import backend
import config
import vision


def step(label, value):
    print(f"{label:<26} {value}")


def main():
    step("backend", backend.NAME)
    backend.set_dpi_aware()

    hwnd = backend.find_game_window()
    step("game window id", hwnd)
    if hwnd is None:
        print(f"\nNo window matched {config.GAME_WINDOW_MATCH}. Edit GAME_WINDOW_MATCH in config.py "
              f"(on Linux compare with:  xprop WM_CLASS _NET_WM_NAME  and click the game).")
        return 1

    win = backend.get_game_rect()
    step("title", win["title"])
    step("client rect", f"{win['left']},{win['top']}  {win['width']}x{win['height']}")
    step("focused", backend.foreground_window() == win["hwnd"])
    step("cursor", backend.cursor_pos())

    shot = vision.grab({"left": win["left"], "top": win["top"], "width": win["width"], "height": win["height"]})
    cv2.imwrite("selftest_window.png", vision.to_bgr(shot))
    step("window capture", f"{shot.shape[1]}x{shot.shape[0]}  mean={shot[:, :, :3].mean():.0f}  (selftest_window.png)")
    if shot[:, :, :3].mean() < 2:
        print("  !! capture is black: turn off the compositor (Settings > Window Manager Tweaks > Compositor)")

    import hint  # loads EasyOCR
    region = hint.hint_region(win)
    step("hint region", region)
    frame = hint.grab_hint(win)
    cv2.imwrite("selftest_hint.png", frame)
    text = hint.read_hint_text(frame)
    step("hint OCR", repr(text))
    step("hint state", hint.classify(text).value)

    if "--walk" in sys.argv:
        print("\nfocusing the game and tapping W for 0.3s...")
        ok = backend.focus_window(win["hwnd"])
        step("focused", ok)
        time.sleep(0.3)
        backend.key_down("w")
        time.sleep(0.3)
        backend.key_up("w")
        print("  (the character should have taken a step)")

    if "--hotkey" in sys.argv:
        fired = []
        backend.add_hotkey(config.TOGGLE_KEY, lambda: fired.append(time.time()))
        print(f"\npress {config.TOGGLE_KEY} within 15s...")
        deadline = time.time() + 15
        while time.time() < deadline and not fired:
            time.sleep(0.1)
        step("hotkey", "received" if fired else "NOT received")
    return 0


if __name__ == "__main__":
    sys.exit(main())
