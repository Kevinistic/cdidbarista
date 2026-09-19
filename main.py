import multiprocessing
import keyboard
import coffee
import cup
import syrup
import order


def _run_coffee():
    coffee.main()


def _tick(win):
    cup.tick(win)
    syrup.tick(win)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    coffee_proc = multiprocessing.Process(target=_run_coffee, daemon=True)
    coffee_proc.start()
    try:
        order.main(on_tick=_tick)   # owns the tkinter mainloop; calls cup.tick & syrup.tick every poll
    finally:
        if coffee_proc.is_alive():
            coffee_proc.terminate()
        keyboard.release("space")