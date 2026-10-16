import ctypes
import math
import multiprocessing
import os
import tkinter as tk
import tkinter.font as tkfont

import keyboard
import coffee

WIDTH, HEIGHT = 400, 200
BACKGROUND = "#000000"
BORDER_COLOR = "#ffffff"
TEXT_COLOR = "#e1e1e1"
TRANSPARENCY = 0.35
TRANSPARENT_COLOR = "#ff00ff"
ON_COLOR, OFF_COLOR = "#00ff00", "#ff0000"

FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts", "Fredoka-Bold.ttf")


def _run_coffee(enabled_event):
    coffee.main(enabled_event=enabled_event)


def _rounded_rect_points(width, height, radius):
    points = []
    for center_x, center_y, start_angle in (
        (radius, radius, 180),
        (width - radius, radius, 270),
        (width - radius, height - radius, 0),
        (radius, height - radius, 90),
    ):
        for step in range(9):
            angle = math.radians(start_angle + step * 90 / 8)
            points.append((center_x + radius * math.cos(angle), center_y + radius * math.sin(angle)))
    return points


def _load_private_font(path):
    if not os.path.isfile(path):
        return
    add_font = ctypes.windll.gdi32.AddFontResourceExW
    add_font.argtypes = [ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
    add_font.restype = ctypes.c_int
    add_font(path, 0x10, None)  # FR_PRIVATE: available only to this process.


class Overlay:
    """Draggable always-on-top status window that never steals focus from Roblox."""

    def __init__(self, toggle_key, close_key):
        self.toggle_key = toggle_key
        self.close_key = close_key

        self.root = root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 1.0 - TRANSPARENCY)
        root.attributes("-transparentcolor", TRANSPARENT_COLOR)
        root.geometry(f"{WIDTH}x{HEIGHT}+20+200")
        root.resizable(False, False)
        root.configure(bg=TRANSPARENT_COLOR)

        _load_private_font(FONT_PATH)
        available_fonts = {name.casefold() for name in tkfont.families(root)}
        font_family = "Fredoka" if "fredoka" in available_fonts else "Segoe UI"
        font = tkfont.Font(root=root, family=font_family, size=11, weight="bold")
        credit_font = tkfont.Font(root=root, family=font_family, size=8, weight="bold")

        self.canvas = canvas = tk.Canvas(root, width=WIDTH, height=HEIGHT, bg=TRANSPARENT_COLOR,
                                         highlightthickness=0, borderwidth=0)
        canvas.pack(fill="both", expand=True)
        canvas.create_polygon(
            _rounded_rect_points(WIDTH, HEIGHT, int(min(WIDTH, HEIGHT) * 0.10)),
            fill=BACKGROUND, outline=BORDER_COLOR, width=10, smooth=True, splinesteps=12,
        )

        top = 8
        toggle_label = "Toggle: "
        canvas.create_text(10, top, text=toggle_label, fill=TEXT_COLOR, font=font, anchor="nw")
        self._toggle_key_item = canvas.create_text(
            10 + font.measure(toggle_label), top, text=toggle_key, fill=OFF_COLOR, font=font, anchor="nw")
        self._status_item = canvas.create_text(
            10, top + font.metrics("linespace"),
            text=f"Terminate: {close_key}\nOrder: -",
            fill=TEXT_COLOR, font=font, justify="left", anchor="nw", width=WIDTH - 24,
        )
        canvas.create_text(10, HEIGHT - 12, text="Made with ♥ by @aoderu and @.dar_.",
                           fill=TEXT_COLOR, font=credit_font, anchor="sw")

        drag = {"x": 0, "y": 0}

        def start_drag(event):
            drag["x"] = event.x_root - root.winfo_x()
            drag["y"] = event.y_root - root.winfo_y()

        def do_drag(event):
            root.geometry(f"+{event.x_root - drag['x']}+{event.y_root - drag['y']}")

        canvas.bind("<Button-1>", start_drag)
        canvas.bind("<B1-Motion>", do_drag)
        root.update()

        # Keep the overlay from stealing focus from Roblox.
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(root.winfo_id())
        ex = user32.GetWindowLongW(hwnd, -20)
        user32.SetWindowLongW(hwnd, -20, ex | 0x08000000 | 0x80)  # WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW

    def show_text(self, text):
        """Replace the status line immediately (used before the event loop runs)."""
        self.canvas.itemconfigure(self._status_item, text=text)
        self.root.update_idletasks()

    def set_status(self, text, tag=None):
        """`tag` (e.g. "PAUSED") is shown next to the toggle key; None means running normally."""
        suffix = f" [{tag}]" if tag else ""
        self.canvas.itemconfigure(self._toggle_key_item, text=f"{self.toggle_key}{suffix}",
                                  fill=ON_COLOR if tag is None else OFF_COLOR)
        self.canvas.itemconfigure(self._status_item, text=f"Terminate: {self.close_key}\n{text}")


def main():
    multiprocessing.freeze_support()

    # keeping out cuz windows doesnt like multiprocessing with frozen executables
    import config
    import cup
    import order
    import syrup

    overlay = Overlay(config.TOGGLE_KEY, config.FORCE_CLOSE_KEY)

    # EasyOCR only finishes initializing on its first inference. Do that now,
    # before the polling loop can see its first order/prompt.
    overlay.show_text("Starting OCR models...")
    try:
        order.warm_up_ocr()
        print("OCR warmed up.")
    except Exception as exc:
        # Keep the bot usable if warm-up fails; normal polling will surface the
        # same underlying OCR error in the overlay.
        print(f"OCR warm-up failed: {exc}")

    enabled_event = multiprocessing.Event()
    enabled_event.set()  # Default: ON (active)

    # Started before the hotkeys exist, so force_close() can always see it.
    coffee_proc = multiprocessing.Process(target=_run_coffee, args=(enabled_event,), daemon=True)
    coffee_proc.start()

    def update_status(text, tag=None):
        if tag != order.STATUS_ERROR and not coffee_proc.is_alive():
            text = f"{text} | brew bot stopped"
        overlay.set_status(text, tag)

    def toggle_state():
        if enabled_event.is_set():
            enabled_event.clear()
            print(f"[{config.TOGGLE_KEY}] Bot PAUSED (OFF)")
        else:
            enabled_event.set()
            print(f"[{config.TOGGLE_KEY}] Bot RESUMED (ON)")

    def stop_workers():
        for key in ("space", config.DIALOGUE_KEY):  # minigame steering + dialogue key: never leave either held
            try:
                keyboard.release(key)
            except Exception:
                pass
        if coffee_proc.is_alive():
            coffee_proc.terminate()

    def force_close():
        print(f"[{config.FORCE_CLOSE_KEY}] Force closing...")
        stop_workers()
        os._exit(0)

    keyboard.add_hotkey(config.TOGGLE_KEY, toggle_state)
    keyboard.add_hotkey(config.FORCE_CLOSE_KEY, force_close)

    def tick(win):
        if enabled_event.is_set():
            cup.tick(win)
            syrup.tick(win)

    print(f"CDID Barista Bot ready. {config.TOGGLE_KEY} = Toggle ON/OFF, {config.FORCE_CLOSE_KEY} = Force Close (Quit).")

    try:
        order.main(on_tick=tick, enabled_event=enabled_event, on_status=update_status,
                   schedule=overlay.root.after)
        overlay.root.mainloop()
    finally:
        stop_workers()


if __name__ == "__main__":
    main()
