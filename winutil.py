"""Windows helpers shared by the macro: game window lookup, DPI/timer setup and mouse input."""
import ctypes

import win32api
import win32gui

from config import GAME_WINDOW_TITLE

_MATCH = GAME_WINDOW_TITLE.lower()

_dpi_set = False


def set_dpi_aware():
    """Per-monitor DPI awareness so screen pixels match win32 coordinates 1:1. Safe to call repeatedly."""
    global _dpi_set
    if _dpi_set:
        return
    _dpi_set = True
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        ctypes.windll.user32.SetProcessDPIAware()


def raise_timer_resolution():
    """1 ms timer resolution for precise short sleeps (process wide)."""
    try:
        ctypes.windll.winmm.timeBeginPeriod(1)
    except Exception:
        pass


# ------------------------------------------------------------------ game window
def _is_game(hwnd):
    return bool(win32gui.IsWindowVisible(hwnd)) and _MATCH in win32gui.GetWindowText(hwnd).lower()


def foreground_window():
    return win32gui.GetForegroundWindow() or None


def find_game_window():
    """Handle of the topmost visible window whose title matches, or None.

    The foreground window is the topmost one, so checking it first returns the same window as a
    full EnumWindows pass without walking every top-level window on every call.
    """
    fg = win32gui.GetForegroundWindow()
    if fg and _is_game(fg):
        return fg
    found = []

    def cb(hwnd, _):
        if _is_game(hwnd):
            found.append(hwnd)

    win32gui.EnumWindows(cb, None)
    return found[0] if found else None


def get_game_rect():
    """The game's INNER client viewport in screen coordinates, or None if no matching window is visible."""
    hwnd = find_game_window()
    if hwnd is None:
        return None
    client_rect = win32gui.GetClientRect(hwnd)
    left, top = win32gui.ClientToScreen(hwnd, (0, 0))
    return {
        "hwnd": hwnd,
        "title": win32gui.GetWindowText(hwnd),
        "left": left,
        "top": top,
        "width": client_rect[2] - client_rect[0],
        "height": client_rect[3] - client_rect[1],
    }


# ------------------------------------------------------------------ mouse
# Native Windows SendInput API (Roblox processes SendInput hardware events)
_INPUT_MOUSE = 0
_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_ABSOLUTE = 0x8000

_ULONG_PTR = ctypes.c_ulong if ctypes.sizeof(ctypes.c_void_p) == 4 else ctypes.c_ulonglong


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("u", _INPUT_UNION),
    ]


def _send_mouse_input(flags, x=0, y=0):
    screen_w = ctypes.windll.user32.GetSystemMetrics(0)
    screen_h = ctypes.windll.user32.GetSystemMetrics(1)
    abs_x = int(x * 65535 / max(1, screen_w - 1))
    abs_y = int(y * 65535 / max(1, screen_h - 1))
    extra = ctypes.windll.user32.GetMessageExtraInfo()
    ii = _INPUT_UNION()
    ii.mi = _MOUSEINPUT(abs_x, abs_y, 0, flags, 0, extra)
    inp = _INPUT(_INPUT_MOUSE, ii)
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def cursor_pos():
    return win32api.GetCursorPos()


def mouse_move(x, y):
    win32api.SetCursorPos((x, y))
    _send_mouse_input(_MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE, x, y)


def mouse_button(x, y, down):
    flags = _MOUSEEVENTF_LEFTDOWN if down else _MOUSEEVENTF_LEFTUP
    _send_mouse_input(flags | _MOUSEEVENTF_ABSOLUTE, x, y)
