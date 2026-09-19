import multiprocessing
import os
import keyboard
import config
import coffee
import cup
import syrup
import order


def _run_coffee(enabled_event):
    coffee.main(enabled_event=enabled_event)


if __name__ == "__main__":
    multiprocessing.freeze_support()

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
        order.main(on_tick=_tick, enabled_event=enabled_event)
    finally:
        try:
            keyboard.release("space")
        except Exception:
            pass
        if coffee_proc.is_alive():
            coffee_proc.terminate()