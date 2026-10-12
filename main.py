import math
import multiprocessing
import os
import tkinter as tk
import tkinter.font as tkfont

import backend
import coffee

WIDTH, HEIGHT = 400, 200
BACKGROUND = "#000000"
BORDER_COLOR = "#ffffff"
TEXT_COLOR = "#e1e1e1"
TRANSPARENCY = 0.35
TRANSPARENT_COLOR = "#ff00ff"
ON_COLOR, OFF_COLOR = "#00ff00", "#ff0000"

FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts", "Fredoka-Bold.ttf")


def _run_coffee(enabled_event, brew_event):
    coffee.main(enabled_event=enabled_event, brew_event=brew_event)


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


class Overlay:
    """Draggable always-on-top status window that never steals focus from Roblox."""

    def __init__(self, toggle_key, close_key):
        self.toggle_key = toggle_key
        self.close_key = close_key

        self.root = root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 1.0 - TRANSPARENCY)
        try:
            root.attributes("-transparentcolor", TRANSPARENT_COLOR)  # Windows only
            canvas_bg = TRANSPARENT_COLOR
        except tk.TclError:
            canvas_bg = BACKGROUND  # no colour keying: square corners on a black panel instead
        root.geometry(f"{WIDTH}x{HEIGHT}+20+200")
        root.resizable(False, False)
        root.configure(bg=canvas_bg)

        backend.load_private_font(FONT_PATH)
        available_fonts = {name.casefold() for name in tkfont.families(root)}
        font_family = next((f for f in ("Fredoka", "Segoe UI", "DejaVu Sans", "Liberation Sans")
                            if f.casefold() in available_fonts),
                           tkfont.nametofont("TkDefaultFont").actual("family"))
        font = tkfont.Font(root=root, family=font_family, size=11, weight="bold")
        credit_font = tkfont.Font(root=root, family=font_family, size=8, weight="bold")

        self.canvas = canvas = tk.Canvas(root, width=WIDTH, height=HEIGHT, bg=canvas_bg,
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

        backend.prepare_overlay(root)  # never steal focus from the game

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
    import controller
    import cup
    import moves
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

    # spawn on every OS (Windows' only mode): a forked child would inherit torch / X11 connections
    mp = multiprocessing.get_context("spawn")
    enabled_event = mp.Event()
    enabled_event.set()  # Default: ON (active)
    brew_event = mp.Event()  # set by the runner only while the Brew minigame is running

    # Started before the hotkeys exist, so force_close() can always see it.
    coffee_proc = mp.Process(target=_run_coffee, args=(enabled_event, brew_event), daemon=True)
    coffee_proc.start()

    run_status = {"text": ""}

    def update_status(text, tag=None):
        if tag != order.STATUS_ERROR:
            if run_status["text"]:
                text = f"{text} | {run_status['text']}"
            if not coffee_proc.is_alive():
                text = f"{text} | brew bot stopped"
        overlay.set_status(text, tag)

    worker = controller.Controller(enabled_event, status=lambda t: run_status.update(text=t),
                                   brew_event=brew_event)
    worker.start()

    def toggle_state():
        if enabled_event.is_set():
            enabled_event.clear()
            print(f"[{config.TOGGLE_KEY}] Bot PAUSED (OFF)")
        else:
            enabled_event.set()
            print(f"[{config.TOGGLE_KEY}] Bot RESUMED (ON)")

    def stop_workers():
        moves.release_keys("space", "e")
        if coffee_proc.is_alive():
            coffee_proc.terminate()

    def force_close():
        print(f"[{config.FORCE_CLOSE_KEY}] Force closing...")
        stop_workers()
        os._exit(0)

    backend.add_hotkey(config.TOGGLE_KEY, toggle_state)
    backend.add_hotkey(config.FORCE_CLOSE_KEY, force_close)

    def tick(win):
        if enabled_event.is_set():
            cup.tick(win)
            syrup.tick(win)

    print(f"CDID Barista Bot ready. {config.TOGGLE_KEY} = Toggle ON/OFF, {config.FORCE_CLOSE_KEY} = Force Close (Quit).")

    try:
        order.main(on_tick=tick, enabled_event=enabled_event, on_status=update_status,
                   schedule=overlay.root.after, on_order=worker.submit)
        overlay.root.mainloop()
    finally:
        stop_workers()


if __name__ == "__main__":
    main()
