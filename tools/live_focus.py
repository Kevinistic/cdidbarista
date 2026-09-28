"""Bring the Roblox window to the foreground before live tools send input."""
import ctypes
import time


def focus(scr):
    """scr: main.Screen after find(). An Alt tap lets SetForegroundWindow through."""
    import win32con
    import win32gui
    if scr.focused():
        return True
    ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
    ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
    win32gui.ShowWindow(scr.hwnd, win32con.SW_SHOW)
    win32gui.SetForegroundWindow(scr.hwnd)
    time.sleep(0.3)
    return scr.focused()
