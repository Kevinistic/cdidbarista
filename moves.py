"""Low-level input for walking/holding keys in Roblox. Shared by calibrate.py and runner.py.

A route is a list of [keys, seconds] items, e.g. [["d", 0.6], ["s", 0.3], ["e", 0.5]].
"w/a/s/d" items walk, an "e" item holds E (the interaction for that route's destination).
"""
import time

import backend

HOME_HOLD = 2.5      # seconds W then A held to land in the top-left corner (origin)
FOCUS_DELAY = 0.35   # let Roblox take focus before keys are sent
ROUTE_GAP = 0.2      # idle gap after every item; same value is used when recording and replaying


def release_keys(*keys):
    """Key-up for `keys` (default: the walk keys and E). Never raises, so it is safe in `finally` blocks."""
    for k in keys or "wasde":
        try:
            backend.key_up(k)
        except Exception:
            pass


def focus(hwnd, tries=5):
    """Bring hwnd to the foreground and confirm it. Returns True on success."""
    return backend.focus_window(hwnd, tries)


def press_for(keys, seconds):
    """Hold `keys` together for exactly `seconds` (timer starts at the press)."""
    t0 = time.perf_counter()
    try:
        for k in keys:
            backend.key_down(k)
        while time.perf_counter() - t0 < seconds:
            time.sleep(0.001)
    finally:
        release_keys(*keys)  # also covers a press that failed halfway through `keys`


def run_steps(steps, after_e=None, force_focus=True, return_to=None, should_continue=None, before_e=None):
    """Run [(keys, seconds), ...] in one Roblox focus session.

    before_e(): called right before each E hold.
    after_e(): called after each E hold; returning False aborts the run.
    force_focus: True = take focus if needed (calibration); False = abort if Roblox
                 is not already the foreground window (unattended runs).
    return_to: window handle to hand focus back to afterwards.
    Returns True if every step ran.
    """
    rb = backend.find_game_window()
    if rb is None:
        print("  !! Roblox window not found")
        return False
    if backend.foreground_window() != rb:
        if not force_focus:
            print("  !! Roblox not focused, nothing sent")
            return False
        if not focus(rb):
            print("  !! could not focus Roblox, nothing sent")
            return False
        time.sleep(FOCUS_DELAY)
    try:
        for keys, seconds in steps:
            if backend.foreground_window() != rb:
                print("  !! Roblox lost focus mid-sequence, aborting")
                return False
            if should_continue is not None and not should_continue():
                return False
            if keys == "e" and before_e is not None:
                before_e()
            press_for(keys, seconds)
            time.sleep(ROUTE_GAP)  # idle gap between items, nothing held
            if keys == "e" and after_e is not None and not after_e():
                return False
    finally:
        release_keys()
        if return_to:
            focus(return_to)
    return True


def home(**kwargs):
    """Walk W then A into the top-left corner (origin)."""
    return run_steps([("w", HOME_HOLD), ("a", HOME_HOLD)], **kwargs)
