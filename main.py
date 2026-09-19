import subprocess
import sys
import time

import keyboard

# add "syrup.py" / "ml.py" here later
SCRIPTS = ["order.py", "coffee.py"]


def main():
    procs = [subprocess.Popen([sys.executable, script]) for script in SCRIPTS]
    try:
        # run until every script exits (coffee quits on F7, order on Ctrl+C / window close)
        while any(p.poll() is None for p in procs):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            p.wait()
        keyboard.release("space")  # a killed coffee.py can't release the key itself


if __name__ == "__main__":
    main()