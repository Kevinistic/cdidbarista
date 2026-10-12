"""Interactive kitchen measurement + route recording tool (PLAN.md step 1).

Run:  python calibrate.py
Run it from a console that is NOT Roblox. Each walking command briefly focuses
Roblox, sends the key presses, then hands focus back to this console.

Coordinate system (PLAN 0.2.4): origin (0,0) = top-left corner of the kitchen,
x grows right (D), y grows down (S). Units = seconds of walking at scale 1.0.

Routes: a route "A>B" is the exact list of walks (+ the final E hold) that takes you
from stop A to stop B. runner.py replays them. Everything is saved to measurements.json.
"""
import json
import os

import backend
import recipes
from moves import HOME_HOLD, run_steps

OUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "measurements.json")
DEFAULT_E_HOLD = 0.5

KEY_VEC = {"w": (0, -1), "a": (-1, 0), "s": (0, 1), "d": (1, 0)}

state = {
    "pos": [0.0, 0.0],   # tracked position since last `home` (only valid if no wall was hit)
    "route": [],         # [(keys, seconds)] since last `home`
    "rec": None,         # {"key": "A>B", "items": [[keys, sec], ...]} while recording a route
    "console": None,
}
data = {"size": {}, "stations": {}, "notes": {}, "routes": {}}


def load():
    if os.path.isfile(OUT_FILE):
        with open(OUT_FILE, encoding="utf-8") as f:
            data.update(json.load(f))
    data.setdefault("routes", {})


def save():
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def hold(keys, seconds):
    return run_steps([(keys, seconds)], return_to=state["console"])


def record_item(keys, seconds):
    if state["rec"] is not None:
        state["rec"]["items"].append([keys, seconds])
        print(f"  [rec {state['rec']['key']}] {state['rec']['items']}")


def walk(keys, seconds):
    keys = "".join(dict.fromkeys(keys.lower()))
    if not keys or any(k not in KEY_VEC for k in keys):
        print("  keys must be a combination of w a s d")
        return
    if hold(keys, seconds):
        dx = sum(KEY_VEC[k][0] for k in keys)
        dy = sum(KEY_VEC[k][1] for k in keys)
        state["pos"][0] += dx * seconds
        state["pos"][1] += dy * seconds
        state["route"].append([keys, seconds])
        show_pos()
        record_item(keys, seconds)


def press_e(seconds):
    if hold("e", seconds):
        record_item("e", seconds)


def home():
    if state["rec"] is not None:
        print("  can't `home` while recording; `cancel` first (routes start from where you stand)")
        return
    if run_steps([("w", HOME_HOLD), ("a", HOME_HOLD)], return_to=state["console"]):
        state["pos"] = [0.0, 0.0]
        state["route"] = []
        show_pos()


def show_pos():
    x, y = state["pos"]
    print(f"  tracked pos = ({x:.2f}, {y:.2f})  walks since home = {state['route']}")


def ask(prompt):
    return input(prompt).strip().lower()


def bisect(axis_key, lo, hi, label):
    """Binary-search the time to walk from `home` to the opposite wall."""
    print(f"  Searching {label}: after each walk answer  y = at the wall, n = not yet, q = stop")
    while hi - lo > 0.04:
        mid = (lo + hi) / 2
        print(f"  home -> hold {axis_key} for {mid:.2f}s")
        home()
        hold(axis_key, mid)
        ans = ask("  reached wall? [y/n/q] ")
        if ans == "q":
            break
        if ans == "y":
            hi = mid
        else:
            lo = mid
    result = round(hi, 2)
    print(f"  => {label} ~= {result}s (upper bound {hi:.2f}, lower bound {lo:.2f})")
    return result


# ---------------------------------------------------------------- routes
def rec_start(a, b):
    key = recipes.route_key(a, b)
    if state["rec"] is not None:
        print(f"  already recording {state['rec']['key']}; `done` or `cancel` first")
        return
    state["rec"] = {"key": key, "items": []}
    print(f"  recording {key}. Make sure you are standing at {a} now. Walk (and `e`), then `done`.")


def rec_done():
    rec = state["rec"]
    if rec is None:
        print("  not recording")
        return
    if not rec["items"] or rec["items"][-1][0] != "e":
        print("  route must end with an `e` (the interaction at the destination); not saved. "
              "`e` then `done`, or `cancel`")
        return
    data["routes"][rec["key"]] = rec["items"]
    save()
    print(f"  saved route {rec['key']}: {rec['items']}")
    state["rec"] = None


def rec_cancel():
    state["rec"] = None
    print("  recording discarded")


def rec_undo():
    rec = state["rec"]
    if rec and rec["items"]:
        rec["items"].pop()
        print(f"  [rec {rec['key']}] {rec['items']}  (the game position is NOT undone)")


def route_todo():
    need = recipes.all_route_keys()
    done = [k for k in need if k in data["routes"]]
    missing = [k for k in need if k not in data["routes"]]
    print(f"  recorded {len(done)}/{len(need)}")
    if missing:
        print("  missing: " + ", ".join(missing))


def play_route(key):
    route = data["routes"].get(key)
    if route is None:
        print(f"  no route {key}")
        return False
    return run_steps(route, return_to=state["console"])


def run_recipe(menu):
    """Replay a whole drink from home, pausing after each route so you can handle pickers/minigames."""
    if menu not in recipes.RECIPES:
        print("  unknown drink; one of: " + ", ".join(recipes.RECIPES))
        return
    keys = recipes.route_keys(menu)
    miss = [k for k in keys if k not in data["routes"]]
    if miss:
        print("  missing routes: " + ", ".join(miss))
        return
    home()
    for k in keys:
        if ask(f"  next: {k}  [enter = go, q = stop] ") == "q":
            return
        if not play_route(k):
            return


HELP = """
Commands
  home                       W {hh}s then A {hh}s -> top-left corner, resets tracked pos to (0,0)
  <keys> <sec>               walk, e.g.  d 1.2   s 0.5   wd 0.3   (tracked from last home)
  e [sec]                    hold E for sec (default {e})
  pos                        show tracked position + walks since home
  at <x> <y>                 override tracked position (use after bumping a wall by hand)
  width / height             bisect time home -> right / bottom wall; stores size.*
  save <Station> [hold]      store tracked pos for a station (optional Hold time)
  counter                    shorthand for: save Counter
  note <Station> <text>      free-text note
  show                       print everything stored so far
  del <Station>              remove a station entry

Routes (hardcoded walk sequences, replayed by runner.py)
  rec <From> <To>            start recording; stand at From first (Home = right after `home`).
                             Stops: Home, Counter, Trash, or a step name (Cup, Beans, ...)
                             Every walk/e you then do is recorded. A route must END with `e`.
  done / cancel / undo       finish+save / discard / drop last recorded item
  todo                       list which required routes are recorded / missing
  routes                     print recorded routes
  play <From> <To>           replay one recorded route from where you stand
  run <Drink>                home, then replay the whole drink route by route (asks before each)
  delroute <From> <To>       delete a recorded route
  help / quit
Drinks: {drinks}
Tip: the order matters for correctness, record routes in drink order
(Home>Cup, Cup>Beans, ...), and replay the previous routes (`run`) to reach each start point.
""".format(hh=HOME_HOLD, e=DEFAULT_E_HOLD, drinks=", ".join(recipes.RECIPES))


def main():
    load()
    state["console"] = backend.foreground_window()
    print("Kitchen calibration. Type 'help'.")
    print(HELP)
    while True:
        try:
            line = input("rec> " if state["rec"] else "cal> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        cmd, *args = line.split()
        cmd = cmd.lower()
        try:
            if cmd in ("quit", "exit", "q"):
                break
            elif cmd == "help":
                print(HELP)
            elif cmd == "home":
                home()
            elif cmd == "pos":
                show_pos()
            elif cmd == "at":
                state["pos"] = [float(args[0]), float(args[1])]
                show_pos()
            elif cmd == "width":
                data["size"]["width"] = bisect("d", 0.2, 2.5, "width")
                save()
            elif cmd == "height":
                data["size"]["height"] = bisect("s", 0.2, 2.5, "height")
                save()
            elif cmd == "e":
                press_e(float(args[0]) if args else DEFAULT_E_HOLD)
            elif cmd in ("save", "counter"):
                name = "Counter" if cmd == "counter" else args[0]
                rest = args if cmd == "counter" else args[1:]
                entry = {"pos": [round(v, 2) for v in state["pos"]]}
                if rest:
                    entry["hold"] = float(rest[0])
                data["stations"][name] = entry
                save()
                print(f"  saved {name}: {entry}")
            elif cmd == "note":
                data["notes"][args[0]] = " ".join(args[1:])
                save()
            elif cmd == "show":
                print(json.dumps(data, indent=2))
            elif cmd == "del":
                data["stations"].pop(args[0], None)
                save()
            elif cmd == "rec":
                rec_start(args[0], args[1])
            elif cmd == "done":
                rec_done()
            elif cmd == "cancel":
                rec_cancel()
            elif cmd == "undo":
                rec_undo()
            elif cmd == "todo":
                route_todo()
            elif cmd == "routes":
                for k, v in data["routes"].items():
                    print(f"  {k}: {v}")
            elif cmd == "play":
                play_route(recipes.route_key(args[0], args[1]))
            elif cmd == "run":
                run_recipe(" ".join(args))
            elif cmd == "delroute":
                data["routes"].pop(recipes.route_key(args[0], args[1]), None)
                save()
            elif all(c in KEY_VEC for c in cmd) and args:
                walk(cmd, float(args[0]))
            else:
                print("  unknown command, type 'help'")
        except (IndexError, ValueError) as e:
            print(f"  bad arguments ({e}); type 'help'")


if __name__ == "__main__":
    main()
