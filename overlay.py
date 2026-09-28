"""Status overlay: a card docked beside the Roblox window. It's hidden from screen capture, so
the bot's own screenshots never contain it even when it sits over the game."""
import ctypes
import math
import os
import threading
import time

import numpy as np

import config
import main
from ocr import reader

W, PAD, ROW = 344, 16, 24
KEY = "#010203"                           # transparent colour: gives the card rounded corners
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
C = {"card": "#111318", "edge": "#2a2f3a", "text": "#e8eaed", "muted": "#9aa0a6", "dim": "#5f6670",
     "track": "#262a33", "done": "#4ade80", "now": "#fbbf24", "warn": "#f87171", "row": "#1a1d24"}
PILLS = {"RUNNING": ("#12351f", "#4ade80"), "PAUSED": ("#3a2e0f", "#fbbf24"),
         "NO FOCUS": ("#23262d", "#9aa0a6"), "LOADING": ("#10263f", "#60a5fa")}
SYRUP_COLOURS = {"Blueberry": "#6366f1", "Caramel": "#d97706", "Grape": "#7c3aed", "Hazelnut": "#a16207",
                 "Mango": "#f59e0b", "Maple": "#b45309", "Mint": "#10b981", "Orange": "#f97316",
                 "Peach": "#fb923c", "Raspberry": "#e11d48", "Strawberry": "#f43f5e", "Vanilla": "#eab308"}
WDA_EXCLUDEFROMCAPTURE = 0x11


def work_areas():
    """(left, top, right, bottom) work area of every monitor."""
    import win32api
    return [win32api.GetMonitorInfo(m[0])["Work"] for m in win32api.EnumDisplayMonitors()]


def dock_spot(w, h, gap=12):
    """Where the card goes: beside the Roblox window on its monitor, else on the neighbouring
    monitor next to it, else inside the game under the BARISTA panel (capture-excluded anyway)."""
    areas = work_areas()
    win = main.get_roblox_client_rect()
    if not win:
        l, t, _, _ = areas[0]
        return l + 24, t + 24
    rl, rt = win["left"], win["top"]
    rr, rb = rl + win["width"], rt + win["height"]
    cx, cy = (rl + rr) // 2, (rt + rb) // 2
    home = [a for a in areas if a[0] <= cx < a[2] and a[1] <= cy < a[3]]
    for l, t, r, b in home:
        if rl - l >= w + 2 * gap:
            return rl - gap - w, max(t + gap, rt + gap)
        if r - rr >= w + 2 * gap:
            return rr + gap, max(t + gap, rt + gap)
    others = [a for a in areas if a not in home]
    if others:
        l, t, r, b = min(others, key=lambda a: min(abs(a[2] - rl), abs(a[0] - rr)))
        x = r - w - gap if r <= rl + 1 else l + gap
        return x, min(max(t + gap, rt + gap), b - h - gap)
    return rr - w - gap, rt + int(0.40 * win["height"])


def fmt_time(s):
    s = int(s)
    return f"{s // 60}:{s % 60:02d}" if s >= 60 else f"{s}s"


def demo_status(bot):
    """Sample state for --demo: an Iced Milk Coffee halfway through."""
    import coffee
    bot.set_order("Iced Milk Coffee", "Raspberry")
    s = bot.status
    s.customer, s.served, s.order_times = "ardzf", 3, [104, 131, 97]
    s.started = time.time() - 612
    for key in ("ask", "cup", "Take Beans", "Load Beans", "Pull the Shot"):
        s.enter(key)
        s.since -= 9
    s.enter("Pour the Milk")
    s.state = "walking to Milk"
    s.route = coffee.route("Iced Milk Coffee", "Raspberry")
    s.goal = "Milk"
    if bot.nav is None:
        import nav
        rng = np.random.default_rng(3)
        cafe = nav.Map({"labels": {n: (rng.uniform(0, 8), rng.uniform(0, 5)) for n in
                                   ("Milk", "Bin", "Cup Rack", "Ice Machine", "Bean Hopper", "Coffee Maker",
                                    "Syrup Bottle", "Water Tap", "Tea Box")}, "pitch": 0.3, "D": 1.0})
        bot.nav = nav.Navigator(cafe, learned="debug/_demo_learned.json")
        bot.nav.nodes, bot.nav.edges, bot.nav.spots = [], set(), {}
        for p in [(1, 1), (2, 1), (3, 1.2), (4, 1.5), (5, 2), (5.5, 3), (5, 4), (3.5, 4.2)]:
            bot.nav.visit(np.array(p, float))
        bot.nav.spots = {"Milk": [cafe.labels["Milk"] * 0.8 + np.array([4, 2.5]) * 0.2]}
        bot.nav.pose = (np.array([4.2, 1.6]), 0.9, time.time() + 1e9)


def run(demo=False):
    """demo: show sample state without starting the bot, visible to screen capture."""
    import keyboard
    import tkinter as tk

    enabled = threading.Event()
    enabled.set()
    bot = main.Bot(enabled)
    loaded = threading.Event()

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-transparentcolor", KEY)
    root.geometry(f"{W}x200+{dock_spot(W, 200)[0]}+{dock_spot(W, 200)[1]}")
    cv = tk.Canvas(root, width=W, height=200, bg=KEY, highlightthickness=0)
    cv.pack(fill="both", expand=True)
    root.update()

    u32 = ctypes.windll.user32
    hwnd = u32.GetParent(root.winfo_id())
    # never take focus from Roblox (NOACTIVATE | TOOLWINDOW), and stay out of screen captures
    u32.SetWindowLongW(hwnd, -20, u32.GetWindowLongW(hwnd, -20) | 0x08000000 | 0x80)
    hidden = not demo and bool(u32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE))

    place = {"docked": True, "dx": 0, "dy": 0, "h": 200}

    def grab(e):
        place.update(dx=e.x_root - root.winfo_x(), dy=e.y_root - root.winfo_y())

    def move(e):
        place["docked"] = False                      # dragged: stay where the user put it
        root.geometry(f"+{e.x_root - place['dx']}+{e.y_root - place['dy']}")

    def redock(_=None):
        place["docked"] = True

    cv.bind("<Button-1>", grab)
    cv.bind("<B1-Motion>", move)
    cv.bind("<Double-Button-1>", redock)             # double-click: snap back beside Roblox

    def dock():
        if place["docked"]:
            x, y = dock_spot(W, place["h"])
            if (root.winfo_x(), root.winfo_y()) != (x, y):
                root.geometry(f"+{x}+{y}")
        root.after(1000, dock)

    def load():
        reader().readtext(np.zeros((64, 256, 3), np.uint8))       # first inference finishes init
        loaded.set()
        if demo:
            demo_status(bot)
            return
        threading.Thread(target=bot.run, daemon=True).start()
        print(f"CDID barista bot running. {config.TOGGLE_KEY} = pause/resume, {config.FORCE_CLOSE_KEY} = quit.")

    def toggle():
        (enabled.clear if enabled.is_set() else enabled.set)()
        print(f"[{config.TOGGLE_KEY}] {'resumed' if enabled.is_set() else 'paused'}")

    def force_close():
        print(f"[{config.FORCE_CLOSE_KEY}] quitting")
        bot.release_all()
        for key in main.SCANCODES:
            main.send_key(key, False)
        os._exit(0)

    keyboard.add_hotkey(config.TOGGLE_KEY, toggle)
    keyboard.add_hotkey(config.FORCE_CLOSE_KEY, force_close)
    threading.Thread(target=load, daemon=True).start()

    def text(x, y, s, color=C["text"], size=13, weight="", anchor="nw", **kw):
        """size in pixels (negative Tk size), so display scaling can't blow up the layout."""
        font = ("Segoe UI Semibold" if weight == "bold" else "Segoe UI", -size)
        return cv.create_text(x, y, text=s, fill=color, font=font, anchor=anchor, **kw)

    def rounded(x0, y0, x1, y1, r, **kw):
        pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1,
               x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
        return cv.create_polygon(pts, smooth=True, **kw)

    def width(s, size, weight=""):
        tid = text(0, 0, s, size=size, weight=weight)
        w = cv.bbox(tid)[2] - cv.bbox(tid)[0]
        cv.delete(tid)
        return w

    def pill(x_right, y, label, bg, fg, blink):
        x0 = x_right - width(label, 11, "bold") - 30
        rounded(x0, y, x_right, y + 22, 11, fill=bg, outline="")
        cv.create_oval(x0 + 10, y + 8, x0 + 16, y + 14, fill=fg if blink < 0.6 else bg, outline=fg)
        text(x0 + 22, y + 11, label, fg, 11, "bold", anchor="w")

    def cup(x, y):
        """Small coffee cup icon (Tk can't draw colour emoji)."""
        rounded(x, y + 4, x + 15, y + 20, 4, fill=C["now"], outline="")
        cv.create_arc(x + 10, y + 7, x + 21, y + 17, start=-90, extent=180, style="arc", outline=C["now"], width=2)
        for dx in (4, 9):
            cv.create_line(x + dx, y, x + dx - 1, y + 2, x + dx, y + 3, fill=C["muted"], smooth=True)

    def minimap(y, nav, goal, t, h=150):
        """Top-down café: walked floor, labels, learned spots, the bot's pose and goal."""
        x0, x1 = PAD, W - PAD
        rounded(x0, y, x1, y + h, 10, fill="#0c0e12", outline=C["edge"])
        pts = list(nav.map.labels.values()) + nav.nodes
        if not pts:
            return y + h + 6
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        span = max(max(xs) - min(xs), max(ys) - min(ys), 1e-6)
        sc = min((x1 - x0 - 24) / span, (h - 24) / span)
        cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2

        def px(p):                     # map y up -> screen y down
            return (x0 + x1) / 2 + (p[0] - cx) * sc, y + h / 2 - (p[1] - cy) * sc

        for a, b in nav.edges:
            (ax, ay), (bx, by) = px(nav.nodes[a]), px(nav.nodes[b])
            cv.create_line(ax, ay, bx, by, fill="#2b3340", width=3, capstyle="round")
        for name, p in nav.map.labels.items():
            lx, ly = px(p)
            hot = name == goal
            r = 4 if hot else 2.5
            cv.create_oval(lx - r, ly - r, lx + r, ly + r, fill=C["now"] if hot else C["muted"], outline="")
            if hot or name in nav.spots:
                text(lx + 6, ly, name, C["now"] if hot else C["dim"], 10, anchor="w")
        for name, spots in nav.spots.items():
            for p in spots:
                sx, sy = px(p)
                cv.create_rectangle(sx - 2, sy - 2, sx + 2, sy + 2, fill="#60a5fa", outline="")
        if nav.pose:
            (bx, by), yaw = px(nav.pose[0]), nav.pose[1]
            fresh = t - nav.pose[2] < 3
            col = C["done"] if fresh else C["muted"]
            tip = (bx + 11 * math.cos(yaw), by - 11 * math.sin(yaw))
            left = (bx + 6 * math.cos(yaw + 2.5), by - 6 * math.sin(yaw + 2.5))
            right = (bx + 6 * math.cos(yaw - 2.5), by - 6 * math.sin(yaw - 2.5))
            cv.create_polygon(*tip, *left, *right, fill=col, outline="")
        else:
            text((x0 + x1) / 2, y + h - 12, "position unknown", C["dim"], 10, anchor="center")
        return y + h + 6

    def refresh():
        t = time.time()
        spin = SPINNER[int(t * 12) % len(SPINNER)]
        blink = (t * 1.5) % 1.0
        s = bot.status
        cv.delete("all")
        h = place["h"]
        rounded(0, 0, W - 1, h - 1, 16, fill=C["card"], outline=C["edge"])

        cup(PAD, 13)
        text(PAD + 30, 27, "Barista Bot", C["text"], 16, "bold", anchor="w")
        if not loaded.is_set():
            state = "LOADING"
        elif not enabled.is_set():
            state = "PAUSED"
        elif not s.focused:
            state = "NO FOCUS"
        else:
            state = "RUNNING"
        pill(W - PAD, 16, state, *PILLS[state], blink)
        y = 54

        if not loaded.is_set():
            text(PAD, y, f"{spin}  Warming up OCR models…", C["muted"], 13)
            y += 26
            x = PAD + (t * 200) % (W - 2 * PAD + 90) - 90
            rounded(PAD, y, W - PAD, y + 6, 3, fill=C["track"], outline="")
            rounded(max(PAD, x), y, min(W - PAD, x + 90), y + 6, 3, fill="#60a5fa", outline="")
            y += 22
        else:
            drink, syrup = bot.order or (None, None)
            if drink:
                text(PAD, y, drink, C["text"], 21, "bold")
                y += 34
                x = PAD
                if syrup:
                    tw = width(syrup, 11, "bold")
                    rounded(x, y, x + tw + 20, y + 22, 11, fill=SYRUP_COLOURS.get(syrup, "#475569"), outline="")
                    text(x + 10, y + 11, syrup, "#ffffff", 11, "bold", anchor="w")
                    x += tw + 30
                text(x, y + 11, f"for {s.customer}" if s.customer else "customer not tagged", C["muted"], 12, anchor="w")
                y += 36
            else:
                text(PAD, y, "Waiting for an order", C["muted"], 18, "bold")
                y += 36

            keys = [k for k, _ in s.route]
            cur = keys.index(s.current) if s.current in keys else (
                keys.index("recipe") if "recipe" in keys and s.current not in (None, "ask", "cup") else -1)
            n, seg_gap = len(keys), 4
            seg_w = (W - 2 * PAD - seg_gap * (n - 1)) / n
            for i in range(n):
                x0 = PAD + i * (seg_w + seg_gap)
                color = C["done"] if i < cur else C["now"] if i == cur and blink < 0.7 else C["track"]
                rounded(x0, y, x0 + seg_w, y + 6, 3, fill=color, outline="")
            y += 18

            if s.current == "bin":
                rounded(PAD - 6, y - 2, W - PAD + 6, y + ROW - 2, 8, fill="#3b1216", outline="")
                text(PAD, y + 11, f"{spin}  Drink ruined, binning it", C["warn"], 13, "bold", anchor="w")
                y += ROW + 4
            for i, (key, label) in enumerate(s.route):
                name, _, station = label.partition("  @ ")
                if i < cur:
                    icon, color, when = "✓", C["dim"], s.took.get(key)
                elif i == cur:
                    rounded(PAD - 6, y - 1, W - PAD + 6, y + ROW - 3, 8, fill=C["row"], outline="")
                    icon, color, when = ("❚❚" if state != "RUNNING" else spin), C["now"], t - s.since
                else:
                    icon, color, when = "•", C["dim"], None
                mid = y + ROW // 2 - 1
                text(PAD, mid, icon, C["done"] if i < cur else color, 13, "bold", anchor="w")
                nid = text(PAD + 22, mid, name, color if i != cur else C["text"], 13, "bold" if i == cur else "",
                           anchor="w")
                if station:
                    st = text(cv.bbox(nid)[2] + 7, mid + 1, station, C["dim"], 11, anchor="w")
                    if cv.bbox(st)[2] > W - PAD - 44:        # no room before the timer column
                        cv.delete(st)
                if when is not None:
                    text(W - PAD, mid, fmt_time(when), color, 12, anchor="e")
                y += ROW

            if bot.nav:
                y = minimap(y + 8, bot.nav, s.goal, t)
            cv.create_line(PAD, y + 4, W - PAD, y + 4, fill=C["edge"])
            sid = text(PAD, y + 12, f"›  {s.state}", C["muted"], 12, width=W - 2 * PAD)
            y = cv.bbox(sid)[3] + 10

            avg = fmt_time(sum(s.order_times) / len(s.order_times)) if s.order_times else "–"
            text(PAD, y, f"Orders {s.served}   ·   avg {avg}   ·   session {fmt_time(t - s.started)}", C["muted"], 11)
            y += 18
        keys_line = f"{config.TOGGLE_KEY} pause   {config.FORCE_CLOSE_KEY} quit   double-click: re-dock"
        if not hidden and not demo:
            keys_line += "   (visible to capture!)"
        text(PAD, y, keys_line, C["dim"], 11)
        y += 24

        if y != h:
            place["h"] = y
            cv.config(height=y)
            root.geometry(f"{W}x{y}")
        root.after(80, refresh)

    refresh()
    dock()
    try:
        root.mainloop()
    finally:
        force_close()


if __name__ == "__main__":
    import sys
    run(demo="--demo" in sys.argv)
