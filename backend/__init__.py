"""OS abstraction layer.

The rest of the project talks to this package and never to win32 / Xlib directly. Each backend
(`windows.py`, `x11.py`) implements the same function set:

    set_dpi_aware()                  per-monitor DPI awareness (no-op on Linux)
    raise_timer_resolution()         1 ms sleep granularity (no-op on Linux)
    find_game_window() -> id | None  topmost visible Roblox/Sober window
    get_game_rect() -> dict | None   {hwnd, title, left, top, width, height}, client area in screen px
    foreground_window() -> id | None currently focused window
    focus_window(id, tries=5) -> bool
    key_down(name) / key_up(name)    "w", "a", "s", "d", "e", "space", "F6", ...
    cursor_pos() -> (x, y)
    mouse_move(x, y)                 absolute screen px
    mouse_button(x, y, down)         left button at (x, y)
    add_hotkey(name, fn)             global hotkey, fn runs on a background thread
    load_private_font(path)          make a font file usable by this process only (best effort)
    prepare_overlay(root)            make a Tk window never steal focus from the game
"""
import sys

if sys.platform == "win32":
    from . import windows as _impl
elif sys.platform.startswith("linux"):
    from . import x11 as _impl
else:
    raise ImportError(f"unsupported platform: {sys.platform}")

NAME = _impl.NAME

set_dpi_aware = _impl.set_dpi_aware
raise_timer_resolution = _impl.raise_timer_resolution
find_game_window = _impl.find_game_window
get_game_rect = _impl.get_game_rect
foreground_window = _impl.foreground_window
focus_window = _impl.focus_window
key_down = _impl.key_down
key_up = _impl.key_up
cursor_pos = _impl.cursor_pos
mouse_move = _impl.mouse_move
mouse_button = _impl.mouse_button
add_hotkey = _impl.add_hotkey
load_private_font = _impl.load_private_font
prepare_overlay = _impl.prepare_overlay
