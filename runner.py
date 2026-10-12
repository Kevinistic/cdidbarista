"""Replay recorded routes (calibrate.py) to make one drink, using the hint as the step ack.

    run_order("Latte")     # recalibrate (home), walk every step, serve at the Counter

Flow per route: remember the hint, replay walks + E, then wait for the hint to change
(the game acknowledged the step). On timeout the E hold is retried once. If the new hint
says the cup is ruined, bin it (Home>Trash) and restart the drink once.

hint.py reads the hint, controller.py decides when to run an order.
"""
import enum
import json
import os
import time

import backend
import hint
import moves
import recipes
from config import HINT_SETTLE, ROUTES_FILE, STEP_TIMEOUT

E_RETRY_HOLD = 0.5
BREW_TIMEOUT = 90.0
BREW_CONFIRMATIONS = 2                 # consecutive positive reads required
MAX_ATTEMPTS = 3                       # later attempts = redoing the drink after binning a ruined cup

_routes_cache = None


def _routes_path():
    if os.path.isabs(ROUTES_FILE):
        return ROUTES_FILE
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), ROUTES_FILE)


def load_routes():
    global _routes_cache
    with open(_routes_path(), encoding="utf-8") as f:
        _routes_cache = json.load(f).get("routes", {})
    return _routes_cache


def get_routes():
    return _routes_cache if _routes_cache is not None else load_routes()


def missing_routes():
    """Route keys the recipes need that measurements.json does not have (raises if it cannot be loaded)."""
    routes = load_routes()
    return [k for k in recipes.all_route_keys() if k not in routes]


class Run:
    def __init__(self, window_fn, should_continue, status, brew_event=None):
        self.window_fn = window_fn
        self.should_continue = should_continue
        self.status = status
        self.brew_event = brew_event  # tells the minigame bot (coffee.py) when it may run
        self.ruined = False
        self.restart = False  # hint went back to "Ambil gelas" mid-drink: redo from home, nothing to bin

    def hint_state(self):
        """Classify the current hint (see hint.HintState)."""
        window = self.window_fn()
        if window is None:
            return hint.HintState.UNKNOWN
        return hint.classify(hint.read_hint_text(hint.grab_hint(window)))

    def wait_hint_change(self, baseline, timeout=STEP_TIMEOUT):
        """Block until the hint differs from `baseline`; returns the new frame or None on timeout."""
        baseline_thumb = hint.thumb(baseline)
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            if not self.should_continue():
                return None
            window = self.window_fn()
            if window is not None:
                frame = hint.grab_hint(window)
                if hint.changed_from(frame, baseline_thumb):
                    time.sleep(HINT_SETTLE)
                    return hint.grab_hint(window)
            time.sleep(0.05)
        return None

    def play(self, key, ack=True, press_e=True):
        route = get_routes().get(key)
        if route is None:
            self.status(f"missing route {key}")
            return False
        window = self.window_fn()
        if window is None:
            self.status("Roblox window not found")
            return False
        if not press_e:
            route = [item for item in route if item[0] != "e"]  # walk there but leave the interaction to the user
        baseline = hint.grab_hint(window) if ack else None
        self.status(f"route {key}")

        brewing = key.endswith(">Brew")

        def after_e():
            frame = self.wait_hint_change(baseline)
            if frame is None and self.should_continue():
                self.status(f"{key}: hint unchanged, retrying E")
                moves.press_for("e", E_RETRY_HOLD)
                time.sleep(moves.ROUTE_GAP)
                frame = self.wait_hint_change(baseline)
            if frame is None:
                # no change: a ruined cup keeps showing the same "ruined" hint, so check for that before giving up
                window = self.window_fn()
                cur = hint.read_hint_text(hint.grab_hint(window)) if window is not None else ""
                if key != "Home>Cup" and hint.is_cup(cur):
                    self.restart = True  # no cup in hand any more: the drink has to be redone from the start
                    return False
                if hint.is_ruined(cur):
                    self.ruined = True
                    return True
                return False
            text = hint.read_hint_text(frame)
            if key != "Home>Cup" and hint.is_cup(text):
                self.restart = True
                return False
            if brewing:
                # the first hint change only means the beans were loaded; the minigame is still running.
                # Proceed only after the hint POSITIVELY shows the next thing, several reads in a row.
                deadline = time.perf_counter() + BREW_TIMEOUT
                streak = 0
                while True:
                    if hint.is_cup(text):
                        # "Ambil gelas": the cup state is gone (macro started too early / cup lost)
                        self.restart = True
                        return False
                    self.status(f"{key}: brewing...")
                    streak = streak + 1 if hint.after_brew(text) else 0
                    if streak >= BREW_CONFIRMATIONS:
                        break
                    if not self.should_continue() or time.perf_counter() > deadline:
                        return False
                    time.sleep(0.5)
                    window = self.window_fn()
                    if window is None:
                        return False
                    text = hint.read_hint_text(hint.grab_hint(window))
            self.ruined = hint.is_ruined(text)
            return True

        def before_e():
            if brewing and self.brew_event is not None:
                self.brew_event.set()  # minigame is about to start: let coffee.py steer

        try:
            return moves.run_steps(route, after_e=after_e if ack else None, force_focus=False,
                                   should_continue=self.should_continue, before_e=before_e)
        finally:
            if self.brew_event is not None:
                self.brew_event.clear()  # minigame over (or aborted): coffee.py must never press space again


class _Attempt(enum.Enum):
    SERVED = "served"      # every route done, drink handed over
    RESTART = "restart"    # cup lost mid-drink: redo from home, there is nothing to bin
    RUINED = "ruined"      # cup ruined: recalibrate, bin it, start over
    FAILED = "failed"      # a route did not complete (paused, focus lost, no hint change)


def _attempt(run, keys):
    """One pass over a drink's routes."""
    run.ruined = False
    run.restart = False
    for key in keys:
        if not run.play(key):
            if run.restart:
                return _Attempt.RESTART
            return _Attempt.RUINED if run.ruined else _Attempt.FAILED
        if run.ruined and key != keys[-1]:
            return _Attempt.RUINED
    return _Attempt.SERVED


def run_order(menu, window_fn=None, should_continue=lambda: True, status=print, brew_event=None):
    """Make and serve `menu`. Returns True once the drink was served (hint left hint_serve)."""
    if menu not in recipes.RECIPES:
        status(f"unknown drink {menu!r}")
        return False
    if window_fn is None:
        window_fn = backend.get_game_rect
    run = Run(window_fn, should_continue, status, brew_event)
    keys = recipes.route_keys(menu)

    def go_home():
        return moves.home(force_focus=False, should_continue=should_continue)

    for attempt in range(MAX_ATTEMPTS):
        if not go_home():
            return False
        if attempt:
            status(f"redoing {menu}")
        # Preflight: we are at home. A ruined / half-made cup must be binned BEFORE Home>Cup, otherwise the
        # E at the cup rack does nothing, the run times out and the character wanders around for nothing.
        state = run.hint_state()
        if state is hint.HintState.TAKE:
            status("order no longer open")
            return False
        if state in (hint.HintState.RUINED, hint.HintState.BUSY):
            status("cup in hand / ruined, binning first")
            if not run.play("Home>Trash") and not should_continue():
                return False
            run.ruined = False
            if not go_home():
                return False

        outcome = _attempt(run, keys)
        if outcome is _Attempt.SERVED:
            return True
        if outcome is _Attempt.FAILED:
            return False
        if outcome is _Attempt.RESTART:
            status("cup lost (hint: take a cup), redoing from home")
            continue  # next attempt starts with `home` and Home>Cup; there is no cup to bin
        # ruined: recalibrate, bin the cup, then start the drink over from the Cup
        run.ruined = False
        status("cup ruined, binning it")
        if not go_home():
            return False
        if not run.play("Home>Trash") and not should_continue():
            return False  # paused / aborted; otherwise redo anyway, the bin press most likely worked
        run.ruined = False
    return False
