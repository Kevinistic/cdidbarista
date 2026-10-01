import tkinter as tk
import tkinter.font as tkfont
import multiprocessing
import os
import ctypes
import math
import keyboard
import coffee


def _run_coffee(enabled_event):
    coffee.main(enabled_event=enabled_event)


if __name__ == "__main__":
    multiprocessing.freeze_support()

    # keeping out cuz windows doesnt like multiprocessing with frozen executables
    import config
    import cup
    import syrup
    import order

    width, height = 400, 200
    background = "#000000"
    border_color = "#ffffff"
    text_color = "#e1e1e1"
    transparency = 0.35
    corner_radius = int(min(width, height) * 0.10)
    transparent_color = "#ff00ff"

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-alpha", 1.0 - transparency)
    root.attributes("-transparentcolor", transparent_color)
    root.geometry(f"{width}x{height}+20+200")
    root.resizable(False, False)
    root.configure(bg=transparent_color)

    font_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "assets",
        "fonts",
        "Fredoka-Bold.ttf",
    )
    if os.path.isfile(font_path):
        add_font = ctypes.windll.gdi32.AddFontResourceExW
        add_font.argtypes = [ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
        add_font.restype = ctypes.c_int
        add_font(font_path, 0x10, None)  # FR_PRIVATE: available only to this process.

    available_fonts = {name.casefold() for name in tkfont.families(root)}
    font_family = "Fredoka" if "fredoka" in available_fonts else "Segoe UI"
    overlay_font = tkfont.Font(root=root, family=font_family, size=11, weight="bold")

    canvas = tk.Canvas(
        root,
        width=width,
        height=height,
        bg=transparent_color,
        highlightthickness=0,
        borderwidth=0,
    )
    canvas.pack(fill="both", expand=True)

    rounded_points = []
    for center_x, center_y, start_angle in (
        (corner_radius, corner_radius, 180),
        (width - corner_radius, corner_radius, 270),
        (width - corner_radius, height - corner_radius, 0),
        (corner_radius, height - corner_radius, 90),
    ):
        for step in range(9):
            angle = math.radians(start_angle + step * 90 / 8)
            rounded_points.append((
                center_x + corner_radius * math.cos(angle),
                center_y + corner_radius * math.sin(angle),
            ))

    canvas.create_polygon(
        rounded_points,
        fill=background,
        outline=border_color,
        width=10,
        smooth=True,
        splinesteps=12,
    )
    toggle_y = 8
    toggle_label = "Toggle: "
    canvas.create_text(
        10,
        toggle_y,
        text=toggle_label,
        fill=text_color,
        font=overlay_font,
        anchor="nw",
    )
    toggle_key_item = canvas.create_text(
        10 + overlay_font.measure(toggle_label),
        toggle_y,
        text=config.TOGGLE_KEY,
        fill="#ff0000",
        font=overlay_font,
        anchor="nw",
    )
    status_item = canvas.create_text(
        10,
        toggle_y + overlay_font.metrics("linespace"),
        text=f"Terminate: {config.FORCE_CLOSE_KEY}\nOrder: -",
        fill=text_color,
        font=overlay_font,
        justify="left",
        anchor="nw",
        width=width - 24,
    )
    credit_font = tkfont.Font(root=root, family=font_family, size=8, weight="bold")
    canvas.create_text(
        10,
        height - 12,
        text="Made with ♥ by @aoderu and @.dar_.",
        fill=text_color,
        font=credit_font,
        anchor="sw",
    )

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
    user32.SetWindowLongW(hwnd, -20, ex | 0x08000000 | 0x80)

    # EasyOCR only finishes initializing on its first inference. Do that now,
    # before the polling loop can see its first order/prompt.
    canvas.itemconfigure(status_item, text="Starting OCR models...")
    root.update_idletasks()
    try:
        order.warm_up_ocr()
        print("OCR warmed up.")
    except Exception as exc:
        # Keep the bot usable if warm-up fails; normal polling will surface the
        # same underlying OCR error in the overlay.
        print(f"OCR warm-up failed: {exc}")

    def update_status(text, status_background):
        toggle_status = ""
        for suffix in (" [PAUSED]", " [UNFOCUSED]", " [NOT FOUND]"):
            if text.endswith(suffix):
                text = text[:-len(suffix)]
                toggle_status = suffix
                break
        if text.lower().startswith("error:"):
            toggle_status = " [ERROR]"

        toggle_color = "#00ff00" if status_background == "#225522" else "#ff0000"
        canvas.itemconfigure(
            toggle_key_item,
            text=f"{config.TOGGLE_KEY}{toggle_status}",
            fill=toggle_color,
        )
        canvas.itemconfigure(
            status_item,
            text=f"Terminate: {config.FORCE_CLOSE_KEY}\n{text}",
        )

    enabled_event = multiprocessing.Event()
    enabled_event.set()  # Default: ON (active)

    def toggle_state():
        if enabled_event.is_set():
            enabled_event.clear()
            print(f"[{config.TOGGLE_KEY}] Bot PAUSED (OFF)")
        else:
            enabled_event.set()
            print(f"[{config.TOGGLE_KEY}] Bot RESUMED (ON)")

    def force_close():
        print(f"[{config.FORCE_CLOSE_KEY}] Force closing...")
        try:
            keyboard.release("space")
        except Exception:
            pass
        if coffee_proc.is_alive():
            coffee_proc.terminate()
        os._exit(0)

    keyboard.add_hotkey(config.TOGGLE_KEY, toggle_state)
    keyboard.add_hotkey(config.FORCE_CLOSE_KEY, force_close)

    coffee_proc = multiprocessing.Process(target=_run_coffee, args=(enabled_event,), daemon=True)
    coffee_proc.start()

    def _tick(win):
        if enabled_event.is_set():
            cup.tick(win)
            syrup.tick(win)

    print(f"CDID Barista Bot ready. {config.TOGGLE_KEY} = Toggle ON/OFF, {config.FORCE_CLOSE_KEY} = Force Close (Quit).")

    try:
        order.main(on_tick=_tick, enabled_event=enabled_event,
                   on_status=update_status, schedule=root.after)
        root.mainloop()
    finally:
        try:
            keyboard.release("space")
        except Exception:
            pass
        if coffee_proc.is_alive():
            coffee_proc.terminate()
