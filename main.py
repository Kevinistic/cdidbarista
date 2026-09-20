import multiprocessing
import os
import ctypes
import tkinter as tk
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

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.geometry("300x160+20+200")
    root.resizable(False, False)
    label = tk.Label(
        root,
        text=(f"Toggle: {config.TOGGLE_KEY}\n"
              f"Terminate: {config.FORCE_CLOSE_KEY}\n"
              "Order: -"),
        bg="#222222",
        fg="white",
        font=("Segoe UI", 11, "bold"),
        justify="left",
        anchor="w",
        wraplength=284,
        padx=8,
        pady=4,
    )
    label.pack(fill="both", expand=True)

    drag = {"x": 0, "y": 0}

    def start_drag(event):
        drag["x"] = event.x_root - root.winfo_x()
        drag["y"] = event.y_root - root.winfo_y()

    def do_drag(event):
        root.geometry(f"+{event.x_root - drag['x']}+{event.y_root - drag['y']}")

    label.bind("<Button-1>", start_drag)
    label.bind("<B1-Motion>", do_drag)
    root.update()

    # Keep the overlay from stealing focus from Roblox.
    user32 = ctypes.windll.user32
    hwnd = user32.GetParent(root.winfo_id())
    ex = user32.GetWindowLongW(hwnd, -20)
    user32.SetWindowLongW(hwnd, -20, ex | 0x08000000 | 0x80)

    # EasyOCR only finishes initializing on its first inference. Do that now,
    # before the polling loop can see its first order/prompt.
    label.config(text="Starting OCR models...", bg="#222222")
    root.update_idletasks()
    try:
        order.warm_up_ocr()
        print("OCR warmed up.")
    except Exception as exc:
        # Keep the bot usable if warm-up fails; normal polling will surface the
        # same underlying OCR error in the overlay.
        print(f"OCR warm-up failed: {exc}")

    def update_status(text, background):
        label.config(
            text=(f"Toggle: {config.TOGGLE_KEY}\n"
                  f"Terminate: {config.FORCE_CLOSE_KEY}\n"
                  f"{text}"),
            bg=background,
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
