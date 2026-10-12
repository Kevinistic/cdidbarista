"""Background worker that decides what the character does next (take, make, serve, reset)."""
import threading
import time

import backend
import hint
import moves
import order
import recipes
import runner
from config import ORDER, SERVE_TAP_W, TAKE_E_HOLD, TAKE_RETRY

TEXT_ONLY_MAX = 4                      # E presses allowed when only the order text (no prompt) says "open"
DIALOGUE_MAX_TRIES = 12                # E presses / checks while closing the NPC dialogue before an order
MAX_ORDER_RETRIES = 2                  # re-runs of an aborted order whose order is still open in game
MAX_ERROR_BACKOFF = 5.0                # seconds; the worker backs off exponentially while it keeps failing


class Controller:
    """Background worker: serves submitted orders, then walks back to the Counter for the next one.

    Cycle: order read -> run_order (home, steps, serve) -> tap W at the counter -> take next order.
    The same reset also runs once at start-up, once each time the macro is re-enabled
    (F6) with Roblox focused, and after any aborted/failed order, because the character's
    position is unknown then. It runs only once per such event. The reset only walks to the counter;
    it then holds E at the counter every TAKE_RETRY seconds (only while the hint says
    to take an order) until a customer's dialog opens and order.py submits the order.
    """

    def __init__(self, enabled_event, status=print, window_fn=None, brew_event=None):
        self.brew_event = brew_event
        self.enabled = enabled_event
        self.status = status
        self.window_fn = window_fn or backend.get_game_rect
        self._pending = None               # (menu, syrup) waiting to be made
        self._retries = 0
        self._busy = False                 # an order was taken out of _pending and is being handled
        self._lock = threading.Lock()      # guards _pending and _busy
        self._need_reset = True
        self._ready_to_take = False
        self._last_take = 0.0
        self._run = runner.Run(self.window_fn, self._ok, status, brew_event)

    # ------------------------------------------------------------ public
    def submit(self, menu, syrup=None):
        """Queue a drink (and its syrup, if the customer asked for one)."""
        with self._lock:
            if self._busy:
                return  # already making a drink: a second read of the same dialogue must not queue another
            self._pending = (menu, syrup)
            self._retries = 0

    def start(self):
        """Load and check the routes, then start the worker thread. Returns False if it cannot run."""
        try:
            missing = runner.missing_routes()
        except Exception as exc:
            self.status(f"error: cannot load routes ({exc})")
            return False
        if missing:
            # recipes that need these will abort at the first missing route; the others still work
            self.status(f"missing routes: {', '.join(missing)}")
        threading.Thread(target=self._loop, daemon=True).start()
        return True

    # ------------------------------------------------------------ helpers
    def _roblox_focused(self):
        w = self.window_fn()
        return w is not None and backend.foreground_window() == w["hwnd"]

    def _ok(self):
        return self.enabled.is_set()

    def _order_still_open(self):
        """True if the game still has an order in progress (hint is anything but 'go ask the customer')."""
        window = self.window_fn()
        if window is None:
            return False
        return not hint.is_take(hint.read_hint_text(hint.grab_hint(window)))

    def _recover_order(self):
        """Hint says 'take a cup' but no drink is running: read the order from the screen (else use the remembered one)."""
        menu = syrup = None
        try:
            _raw, menu, syrup = order.read_customer_order()
        except Exception:
            menu = syrup = None
        if menu in recipes.RECIPES:
            if syrup is None and ORDER.get("menu") == menu:
                syrup = ORDER.get("syrup")  # an OCR miss must not drop the syrup read on the first pass
            ORDER.update(menu=menu, syrup=syrup)
            return menu, syrup
        menu = ORDER.get("menu")
        if menu in recipes.RECIPES:
            return menu, ORDER.get("syrup")
        return None, None

    def _try_take_order(self):
        """At the counter: hold E to ask for the order, but only if the hint says an order is due."""
        window = self.window_fn()
        if window is None:
            return
        text = hint.read_hint_text(hint.grab_hint(window))
        if hint.is_cup(text):
            # An order is already taken but the macro has no drink running for it (order.py missed the
            # dialogue, or a run was aborted).
            self.status("hint says take a cup, reading order...")
            menu, syrup = self._recover_order()
            if menu is not None:
                self.status(f"making {menu}")
                self.submit(menu, syrup)
            else:
                self.status("hint says take a cup, but the order is unreadable")
                self._last_take = time.perf_counter()
            return
        if not hint.is_take(text):
            self.status("at counter, hint is not 'take order'")
            self._last_take = time.perf_counter()
            return
        self.status("asking for order")
        moves.run_steps([("e", TAKE_E_HOLD)], force_focus=False, should_continue=self._ok)
        self._last_take = time.perf_counter()  # next attempt after TAKE_RETRY; order.py reads the dialog meanwhile

    def _dialogue_open(self):
        """Is the NPC dialogue's 'klik untuk lanjut' prompt on screen? (own OCR pass, no shared cache)"""
        window = self.window_fn()
        if window is None:
            return False
        return order.prompt_score(window) >= order.PROMPT_CUTOFF

    def _dialogue_has_order_text(self):
        """Second signal: a readable order inside the dialogue box means the dialogue is open even when the
        'klik untuk lanjut' prompt is not recognised (typing animation, OCR miss)."""
        try:
            _raw, menu, syrup = order.read_customer_order()
            return bool(menu or syrup)
        except Exception:
            return False

    def _close_dialogue(self):
        """Press E until the dialogue is gone (two clean checks in a row). Never walk while it is open.

        Open = prompt visible OR the dialogue box still reads as an order. The text-only signal is
        capped (TEXT_ONLY_MAX presses) so a lingering bubble can never block the order forever.
        """
        clean = 0
        text_only_presses = 0
        for _ in range(DIALOGUE_MAX_TRIES):
            if not self._ok():
                return False
            prompt = self._dialogue_open()
            text = (not prompt) and text_only_presses < TEXT_ONLY_MAX and self._dialogue_has_order_text()
            if prompt or text:
                clean = 0
                if text:
                    text_only_presses += 1
                self.status("dialogue open, pressing E")
                moves.run_steps([("e", 0.1)], force_focus=False, should_continue=self._ok)
            else:
                clean += 1
                if clean >= 2:
                    return True
            time.sleep(0.3)
        return False

    # ------------------------------------------------------------ worker
    def _handle_order(self, menu, syrup):
        """Close the dialogue, make the drink, serve it. `_busy` is already set by the caller."""
        try:
            self._ready_to_take = False
            self.status(f"making {menu}")
            ORDER.update(menu=menu, syrup=syrup)  # syrup.tick reads it from here
            if not self._close_dialogue():
                self.status("dialogue would not close, aborting order")
                self._need_reset = True
                return
            served = runner.run_order(menu, self.window_fn, self._ok, self.status, self.brew_event)
        finally:
            with self._lock:
                self._busy = False
        if served:
            # already standing at the Counter: skip home + Home>Counter, just tap W into the wall
            self.status("served")
            # forget the finished order: otherwise the next customer's "Ambil gelas" hint can
            # appear before order.py has read their order, and the old drink gets made again
            ORDER.update(menu=None, syrup=None)
            moves.run_steps([("w", SERVE_TAP_W)], force_focus=False, should_continue=self._ok)
            self._ready_to_take = True
            self._last_take = 0.0
        elif self._ok() and self._retries < MAX_ORDER_RETRIES and self._order_still_open():
            # the order is still taken in game (hint is not "take order"): redo it instead of dropping it
            self._retries += 1
            self.status(f"order aborted, retry {self._retries}/{MAX_ORDER_RETRIES}")
            with self._lock:
                self._pending = (menu, syrup)
        else:
            self.status("order aborted")
            self._need_reset = True

    def _return_to_counter(self):
        self.status("returning to counter")
        reached = (moves.home(force_focus=False, should_continue=self._ok)
                   and self._run.play("Home>Counter", ack=False, press_e=False))
        if reached:
            self._need_reset = False
            self._ready_to_take = True
            self._last_take = 0.0
            self.status("waiting for order")

    def _step(self):
        """One pass of the worker loop."""
        if not self._ok():
            # paused: the character may be moved by hand, so the position is unknown on resume
            self._need_reset = True
            self._ready_to_take = False
            with self._lock:
                self._pending = None
            return
        if not self._roblox_focused():
            return
        with self._lock:
            pending, self._pending = self._pending, None
            if pending:
                self._busy = True  # same critical section as the pop: submit() can never slip in between
        if pending:
            self._handle_order(*pending)
        elif self._need_reset:
            self._return_to_counter()
        elif self._ready_to_take and time.perf_counter() - self._last_take >= TAKE_RETRY:
            self._try_take_order()

    def _loop(self):
        failures = 0
        while True:
            time.sleep(0.1)
            try:
                self._step()
                failures = 0
            except Exception as exc:  # keep the worker alive
                self.status(f"error: {exc}")
                self._need_reset = True
                failures = min(failures + 1, 10)
                time.sleep(min(0.5 * 2 ** (failures - 1), MAX_ERROR_BACKOFF))  # don't spin on a persistent failure
