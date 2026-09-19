import multiprocessing
import keyboard
import coffee
import cup
import order


def _run_coffee():
    coffee.main()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    coffee_proc = multiprocessing.Process(target=_run_coffee, daemon=True)
    coffee_proc.start()
    try:
        order.main(on_tick=cup.tick)   # owns the tkinter mainloop; calls cup.tick every poll
    finally:
        if coffee_proc.is_alive():
            coffee_proc.terminate()
        keyboard.release("space")