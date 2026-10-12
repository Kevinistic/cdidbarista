"""X11 backend (Linux Mint XFCE and friends) on pure-Python python-xlib.

* windows: EWMH (_NET_CLIENT_LIST / _NET_ACTIVE_WINDOW), no `xdotool` subprocess per poll
* input:   XTest by default; optional uinput for keys (config.LINUX_INPUT = "uinput")
* hotkeys: XGrabKey on the root window
Needs an X11 session (Sober through XWayland counts). Not usable on native Wayland.
"""
import threading
import time

from Xlib import X, XK, Xatom, display as xdisplay
from Xlib.error import XError
from Xlib.protocol import event as xevent
from Xlib.ext import xtest

from config import GAME_WINDOW_MATCH, LINUX_INPUT

NAME = "x11"
_MATCH = tuple(m.lower() for m in GAME_WINDOW_MATCH["linux"])

# A Display connection is not safe to share between threads (the overlay, controller and hotkey
# threads all talk to X), so every thread lazily opens its own.
_tls = threading.local()


def _dpy():
    d = getattr(_tls, "d", None)
    if d is None:
        d = xdisplay.Display()
        if not d.has_extension("XTEST"):
            raise RuntimeError("X server lacks the XTEST extension, cannot inject input")
        _tls.d = d
        _tls.atoms = {}
        _tls.keycodes = {}
    return d


def _atom(d, name):
    atoms = _tls.atoms
    if name not in atoms:
        atoms[name] = d.intern_atom(name)
    return atoms[name]


def _root(d):
    return d.screen().root


def _window(d, wid):
    return d.create_resource_object("window", wid)


def set_dpi_aware():
    """Nothing to do: X reports physical pixels."""


def raise_timer_resolution():
    """Nothing to do: Linux sleeps are already fine-grained."""


# ------------------------------------------------------------------ windows
def _text_prop(d, win, atom):
    prop = win.get_full_property(atom, X.AnyPropertyType)
    if prop is None or prop.format != 8:
        return ""
    value = prop.value
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def _window_title(d, wid):
    win = _window(d, wid)
    return _text_prop(d, win, _atom(d, "_NET_WM_NAME")) or _text_prop(d, win, Xatom.WM_NAME)


def _is_game(d, wid):
    """Title or WM_CLASS contains one of the configured substrings."""
    try:
        win = _window(d, wid)
        haystack = _window_title(d, wid)
        wm_class = win.get_wm_class()
        if wm_class:
            haystack += " " + " ".join(wm_class)
    except XError:
        return False
    haystack = haystack.lower()
    return any(m in haystack for m in _MATCH)


def foreground_window():
    d = _dpy()
    prop = _root(d).get_full_property(_atom(d, "_NET_ACTIVE_WINDOW"), X.AnyPropertyType)
    if prop is None or len(prop.value) == 0:
        return None
    return int(prop.value[0]) or None


def _viewable_area(d, wid):
    """Pixel area of a mapped window, or 0 when it is unmapped / gone."""
    try:
        win = _window(d, wid)
        if win.get_attributes().map_state != X.IsViewable:
            return 0
        g = win.get_geometry()
        return g.width * g.height
    except XError:
        return 0


def find_game_window():
    """The focused game window if there is one, else the largest mapped window that matches."""
    d = _dpy()
    active = foreground_window()
    if active is not None and _is_game(d, active):
        return active
    prop = _root(d).get_full_property(_atom(d, "_NET_CLIENT_LIST"), X.AnyPropertyType)
    best, best_area = None, 0
    for wid in (prop.value if prop is not None else ()):
        wid = int(wid)
        if _is_game(d, wid):
            area = _viewable_area(d, wid)
            if area > best_area:
                best, best_area = wid, area
    return best


def get_game_rect():
    """The game's client area in screen coordinates (decorations excluded), or None."""
    d = _dpy()
    wid = find_game_window()
    if wid is None:
        return None
    try:
        win = _window(d, wid)
        geometry = win.get_geometry()
        origin = _root(d).translate_coords(wid, 0, 0)  # (0, 0) of the client, in root coordinates
        return {
            "hwnd": wid,
            "title": _window_title(d, wid),
            "left": origin.x,
            "top": origin.y,
            "width": geometry.width,
            "height": geometry.height,
        }
    except XError:
        return None


def focus_window(wid, tries=5):
    """Ask the window manager to activate `wid` (same request `xdotool windowactivate` sends)."""
    d = _dpy()
    for _ in range(tries):
        if foreground_window() == wid:
            return True
        try:
            event = xevent.ClientMessage(
                window=_window(d, wid),
                client_type=_atom(d, "_NET_ACTIVE_WINDOW"),
                data=(32, [2, X.CurrentTime, 0, 0, 0]),  # source 2 = pager: honoured by focus-stealing prevention
            )
            _root(d).send_event(event, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
            d.sync()
        except XError:
            pass
        time.sleep(0.1)
    return foreground_window() == wid


# ------------------------------------------------------------------ keyboard
def _keycode(d, name):
    codes = _tls.keycodes
    if name not in codes:
        keysym = 0
        for candidate in (name, name.capitalize(), name.upper()):  # "f6" -> "F6"
            keysym = XK.string_to_keysym(candidate)
            if keysym:
                break
        code = d.keysym_to_keycode(keysym) if keysym else 0
        if not code:
            raise ValueError(f"no X keycode for key {name!r}")
        codes[name] = code
    return codes[name]


_uinput = None
_uinput_lock = threading.Lock()
_uinput_codes = {}


def _uinput_device():
    """Lazily create a virtual keyboard with just the keys the bot presses."""
    global _uinput
    with _uinput_lock:
        if _uinput is None:
            from evdev import UInput, ecodes
            names = {"w": "KEY_W", "a": "KEY_A", "s": "KEY_S", "d": "KEY_D", "e": "KEY_E", "space": "KEY_SPACE"}
            _uinput_codes.update({k: getattr(ecodes, v) for k, v in names.items()})
            _uinput = UInput({ecodes.EV_KEY: list(_uinput_codes.values())}, name="cdidbarista-keys")
        return _uinput


def _key_event(name, down):
    if LINUX_INPUT == "uinput":
        from evdev import ecodes
        dev = _uinput_device()
        code = _uinput_codes.get(name.lower())
        if code is not None:
            dev.write(ecodes.EV_KEY, code, 1 if down else 0)
            dev.syn()
            return
    d = _dpy()
    xtest.fake_input(d, X.KeyPress if down else X.KeyRelease, _keycode(d, name))
    d.sync()


def key_down(name):
    _key_event(name, True)


def key_up(name):
    _key_event(name, False)


def add_hotkey(name, fn):
    """Grab `name` globally (with and without NumLock/CapsLock) and call fn() from a daemon thread.

    The grabbed key is consumed: the game does not see it.
    """
    def run():
        d = xdisplay.Display()
        root = _root(d)
        keycode = d.keysym_to_keycode(XK.string_to_keysym(name) or XK.string_to_keysym(name.upper()))
        if not keycode:
            print(f"hotkey {name!r}: no such key")
            return
        failed = []
        for mods in (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask):
            root.grab_key(keycode, mods, True, X.GrabModeAsync, X.GrabModeAsync,
                          onerror=lambda err, request: failed.append(err))
        d.sync()
        if failed:
            print(f"hotkey {name!r}: another program already grabbed it ({failed[0]})")
        while True:
            event = d.next_event()
            if event.type == X.KeyPress and event.detail == keycode:
                try:
                    fn()
                except Exception as exc:
                    print(f"hotkey {name!r} handler failed: {exc!r}")

    threading.Thread(target=run, daemon=True, name=f"hotkey-{name}").start()


# ------------------------------------------------------------------ mouse
def cursor_pos():
    pointer = _root(_dpy()).query_pointer()
    return pointer.root_x, pointer.root_y


def mouse_move(x, y):
    d = _dpy()
    xtest.fake_input(d, X.MotionNotify, False, X.CurrentTime, X.NONE, int(x), int(y))  # absolute
    d.sync()


def mouse_button(x, y, down):
    d = _dpy()  # the pointer is already at (x, y): choice.click moves it first
    xtest.fake_input(d, X.ButtonPress if down else X.ButtonRelease, 1)
    d.sync()


# ------------------------------------------------------------------ overlay
def load_private_font(path):
    """Not supported without touching the user's font directories; Tk falls back to a system font."""
    return False


def prepare_overlay(root):
    """Override-redirect windows are invisible to the window manager, so they never take focus from the
    game; just keep lifting the overlay above a (fullscreen) game window."""
    def lift():
        try:
            root.lift()
            root.after(1000, lift)
        except Exception:
            pass

    lift()
