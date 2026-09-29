"""Where am I: station labels as landmarks.

Every station has a floating name label at a fixed spot in the café, drawn even through walls,
so the labels work like lighthouses: the horizontal screen position of a few known labels gives
their bearings, and bearings to known points fix the camera's position and heading.
"""
import difflib
import math
import time

import numpy as np

import config
import main
from ocr import REF_H, squash

# every label seen in the café, including stations the recipes don't use: all of them are landmarks
LANDMARKS = {
    "Cup Rack": ["Cup Rack", "Rak Gelas"], "Ice Machine": ["Ice Machine", "Mesin Es"],
    "Bean Hopper": ["Bean Hopper", "Wadah Biji"], "Coffee Maker": ["Coffee Maker", "Mesin Kopi"],
    "Foam Maker": ["Foam Maker"], "Milk": ["Milk", "Susu"], "Bin": ["Bin", "Tong Sampah"],
    "Syrup Bottle": ["Syrup Bottle", "Botol Sirup"], "Water Tap": ["Water Tap", "Keran Air"],
    "Tea Box": ["Tea Box", "Kotak Teh"], "Matcha Jar": ["Matcha Jar", "Toples Matcha"],
    "Chocolate Jar": ["Chocolate Jar", "Toples Cokelat"], "Boba Pot": ["Boba Pot", "Panci Boba"],
    "Lemon Board": ["Lemon Board", "Talenan Lemon"], "Cream Dispenser": ["Cream Dispenser", "Dispenser Krim"],
    "Carbonator": ["Carbonator", "Karbonator"],
}
READ_CUTOFF = 0.75        # whole-text similarity: merged labels ("Tea BoxMatcha Jar") don't pass


def identify(text):
    """Label text -> landmark name if the whole text is one label, else None."""
    t = squash(text)
    if len(t) < 3:
        return None
    best, score = None, 0.0
    for name, aliases in LANDMARKS.items():
        for a in aliases:
            r = difflib.SequenceMatcher(None, t, squash(a)).ratio()
            if r > score:
                best, score = name, r
    return best if score >= READ_CUTOFF else None


def split_merged(text):
    """Names inside a run of overlapping labels ("Tea BoxMatcha JarChocolate Jar") with the
    fraction of the text where each sits -> [(name, centre_fraction)], left to right."""
    t = squash(text)
    found = []
    for name, aliases in LANDMARKS.items():
        for a in (squash(x) for x in aliases):
            best, at = 0.0, None
            for n in (len(a) - 1, len(a), len(a) + 1):
                for i in range(0, max(1, len(t) - n + 1)):
                    r = difflib.SequenceMatcher(None, t[i:i + n], a).ratio()
                    if r > best:
                        best, at = r, (i, i + n)
            if best >= 0.8 and at:
                found.append((best, name, at))
    taken, out = [], []
    for score, name, (a, b) in sorted(found, reverse=True):         # best matches claim their text first
        if all(b <= x or a >= y for x, y in taken) and name not in [o[0] for o in out]:
            taken.append((a, b))
            out.append((name, (a + b) / 2 / max(len(t), 1)))
    return sorted(out, key=lambda o: o[1])


def observe(img):
    """Station labels on screen -> [(name, cx, cy, weight)]. A label read on its own weighs 1;
    one picked out of overlapping labels weighs 0.5 (its x is estimated from the text). A name
    seen twice (a misread somewhere) is dropped."""
    H, W = img.shape[:2]
    k = H / REF_H
    world = main.world_mask(W, H)

    def clipped(b):                                   # cut off by the HUD or the screen edge: centre unknown
        cy, pad = int((b[1] + b[3]) / 2), int(4 * k)
        return any(not 0 <= x < W or not world[cy, x] for x in (int(b[0]) - pad, int(b[2]) + pad))

    seen = {}
    for m in (main.highlight_mask(img), main.white_mask(img)):
        for b in main.find_labels(img, m):
            if b[3] - b[1] < 18 * k or clipped(b):   # player name tags are smaller than station labels
                continue
            text = main.read_label(img, b, m)
            cy = (b[1] + b[3]) / 2
            name = identify(text)
            if name:
                seen.setdefault(name, []).append(((b[0] + b[2]) / 2, cy, 1.0))
                continue
            for name, frac in split_merged(text):
                seen.setdefault(name, []).append((b[0] + frac * (b[2] - b[0]), cy, 0.5))
    return [(n, *obs[0]) for n, obs in seen.items() if len(obs) == 1]


# Walls pull the camera in: its distance behind the character is D * pull, with a weak prior
# toward pull 1. Pull may exceed 1 a little, so D settles at the typical distance, not the largest.
MIN_PULL, MAX_PULL, PULL_W = 0.1, 1.5, 0.02


def bearing(u, v, W, H, pitch, vfov=config.CAMERA_VFOV):
    """Horizontal angle (rad, + = right) of screen point (u, v) off the camera's heading, for a
    camera pitched down by `pitch` rad. Works on scalars or numpy arrays."""
    f = (H / 2) / math.tan(math.radians(vfov) / 2)
    x, y = (np.asarray(u) - W / 2) / f, (np.asarray(v) - H / 2) / f     # ray in camera space, z = 1
    fwd = np.cos(pitch) - y * np.sin(pitch)                              # z component after un-pitching
    return np.arctan2(x, fwd)


def wrap(a):
    return (a + np.pi) % (2 * np.pi) - np.pi


class Map:
    """The café from tools/mapper.py: label positions (map units), camera pitch and the camera's
    distance D behind the character."""

    def __init__(self, data):
        self.labels = {n: np.array(p) for n, p in data["labels"].items()}
        self.pitch, self.D = data["pitch"], data["D"]
        self.spots = {k: np.array(v) for k, v in data.get("spots", {}).items()}

    @classmethod
    def load(cls, path=config.MAP_FILE):
        import json
        import os
        return cls(json.load(open(path))) if os.path.exists(path) else None

    def localize(self, obs, W, H, prev=None):
        """Labels seen in one frame -> (character xy, heading, rms deg), or None with under 3
        known labels. prev (xy, heading) seeds the search when the rough answer is ambiguous."""
        from scipy.optimize import least_squares
        known = [(n, u, v, w) for n, u, v, w in obs if n in self.labels]
        if len(known) < 3:
            return None
        L = np.array([self.labels[n] for n, *_ in known])
        b = bearing(np.array([u for _, u, _, _ in known]), np.array([v for _, _, v, _ in known]),
                    W, H, self.pitch)
        wts = np.array([w for *_, w in known])
        cam, yaw, _ = resect(L, -b)                        # world direction = heading - bearing

        def bear_res(x):         # x: character xy, heading, camera pull-in (walls move the camera closer)
            c = x[:2] - x[3] * self.D * np.array([math.cos(x[2]), math.sin(x[2])])
            return wts * wrap(np.arctan2(L[:, 1] - c[1], L[:, 0] - c[0]) - (x[2] - b))

        def res(x):
            return np.append(bear_res(x), PULL_W * (1 - x[3]))

        starts = [np.array([*(cam + self.D * np.array([math.cos(yaw), math.sin(yaw)])), yaw, 1.0])]
        if prev is not None:
            starts.append(np.array([*prev[0], prev[1], 1.0]))
        bounds = ([-np.inf, -np.inf, -np.inf, MIN_PULL], [np.inf, np.inf, np.inf, MAX_PULL])
        best = min((least_squares(res, x0, bounds=bounds, loss="soft_l1", f_scale=0.05) for x0 in starts),
                   key=lambda r: r.cost)
        rms = math.degrees(math.sqrt(np.mean((bear_res(best.x) / wts) ** 2)))
        return best.x[:2], float(wrap(best.x[2])), rms


def resect(L, dirs, steps=360):
    """Where a camera is, from known label points L (k x 2) and the directions it sees them at
    relative to its unknown heading: try every heading; each turns the directions into lines
    through the labels, whose meeting point is the camera. -> (xy, heading, rms rad)."""
    rots = np.linspace(-np.pi, np.pi, steps, endpoint=False)
    t = rots[:, None] + dirs[None, :]
    c, s = np.cos(t), np.sin(t)
    m00, m01, m11 = 1 - c * c, -c * s, 1 - s * s
    r0 = (m00 * L[:, 0] + m01 * L[:, 1]).sum(1)
    r1 = (m01 * L[:, 0] + m11 * L[:, 1]).sum(1)
    a00, a01, a11 = m00.sum(1), m01.sum(1), m11.sum(1)
    det = np.where(np.abs(a00 * a11 - a01 ** 2) < 1e-9, 1e-9, a00 * a11 - a01 ** 2)
    P = np.stack([(a11 * r0 - a01 * r1) / det, (a00 * r1 - a01 * r0) / det], 1)
    seen = np.arctan2(L[None, :, 1] - P[:, None, 1], L[None, :, 0] - P[:, None, 0])
    cost = np.mean(np.minimum(wrap(seen - t) ** 2, 0.05), 1)
    i = int(np.argmin(cost))
    return P[i], float(rots[i]), float(np.sqrt(cost[i]))


class Navigator:
    """Pose tracking, a roadmap of floor the bot has actually walked, and learned standing spots.

    Roadmap nodes are places the character stood (labels render through walls, so a straight line
    at a label can run into the counter; paths along walked floor can't). Consecutive poses make
    edges; an edge where the bot got stuck is blocked. Standing spots are where a target's chip
    (station, register, bin) was used successfully.
    """

    def __init__(self, cafe, learned=config.LEARNED_FILE):
        self.map, self.path = cafe, learned
        self.pose = None                  # (xy, heading, time)
        self.nodes, self.edges, self.blocked, self.spots = [], set(), set(), {}
        self._last_node = None
        self.load()
        if not self.nodes:
            self.seed()

    def seed(self):
        """Start the roadmap from the mapping tour: its spots are floor the character stood on,
        walked in order (start, s0_..., s1_...)."""
        for name in sorted(self.map.spots, key=lambda s: (s != "start", s)):
            self.visit(self.map.spots[name])
        self._last_node = None

    # ---------------------------------------------------------- persistence
    def load(self):
        import json
        import os
        if os.path.exists(self.path):
            d = json.load(open(self.path))
            self.nodes = [np.array(p) for p in d.get("nodes", [])]
            self.edges = {tuple(e) for e in d.get("edges", [])}
            self.blocked = {tuple(e) for e in d.get("blocked", [])}
            self.spots = {k: [np.array(p) for p in v] for k, v in d.get("spots", {}).items()}

    def save(self):
        import json
        json.dump({"nodes": [list(map(float, p)) for p in self.nodes], "edges": sorted(self.edges),
                   "blocked": sorted(self.blocked),
                   "spots": {k: [list(map(float, p)) for p in v] for k, v in self.spots.items()}},
                  open(self.path, "w"))

    # ---------------------------------------------------------- pose
    def update(self, img):
        """Localise from one frame; also grows the roadmap. -> pose or None."""
        H, W = img.shape[:2]
        prev = (self.pose[0], self.pose[1]) if self.pose else None
        r = self.map.localize(observe(img), W, H, prev)
        if r is None or r[2] > config.LOCALIZE_MAX_RMS:
            return None
        self.pose = (r[0], r[1], time.time())
        self.visit(r[0])
        return self.pose

    def turned(self, radians):
        """Dead reckoning between fixes: the camera heading moved by `radians` (+ = left/CCW)."""
        if self.pose:
            self.pose = (self.pose[0], wrap(self.pose[1] + radians), self.pose[2])

    def visit(self, xy):
        """Stood at xy: add a node if it's new floor, and an edge from the previous node."""
        near = self.nearest(xy)
        if near is None or np.linalg.norm(self.nodes[near] - xy) > config.NODE_SPACING * self.map.D:
            self.nodes.append(np.array(xy, float))
            near = len(self.nodes) - 1
        if self._last_node is not None and self._last_node != near:
            e = tuple(sorted((self._last_node, near)))
            if e not in self.blocked:
                self.edges.add(e)
        self._last_node = near

    def stuck(self):
        """The last walk went nowhere: block the edge toward where we were heading."""
        if self._target_edge:
            self.blocked.add(self._target_edge)
            self.edges.discard(self._target_edge)

    _target_edge = None

    def nearest(self, xy, among=None):
        idx = range(len(self.nodes)) if among is None else among
        best = min(idx, key=lambda i: np.linalg.norm(self.nodes[i] - xy), default=None)
        return best

    # ---------------------------------------------------------- targets
    def learn(self, target, xy=None):
        """A target's chip was used from here: remember the spot (a few per target)."""
        xy = xy if xy is not None else (self.pose[0] if self.pose else None)
        if xy is None:
            return
        spots = self.spots.setdefault(target, [])
        if all(np.linalg.norm(p - xy) > 0.5 * config.NODE_SPACING * self.map.D for p in spots):
            spots.append(np.array(xy, float))
            del spots[:-config.SPOTS_PER_TARGET]
        self.save()

    def goal(self, target):
        """Where to stand for target: the learned spot nearest to us, else walked floor near its label."""
        here = self.pose[0] if self.pose else None
        if self.spots.get(target):
            return min(self.spots[target], key=lambda p: np.linalg.norm(p - here) if here is not None else 0)
        label = self.map.labels.get(target)
        if label is None:
            return None
        if self.nodes:                                     # the walked floor closest to the label
            return self.nodes[self.nearest(label)]
        return label

    def route(self, goal):
        """Waypoints from the current pose to goal along walked floor (A*), ending at goal."""
        import heapq
        if not self.pose or not self.nodes:
            return [goal]
        start, end = self.nearest(self.pose[0]), self.nearest(goal)
        adj = {}
        for a, b in self.edges:
            adj.setdefault(a, []).append(b)
            adj.setdefault(b, []).append(a)
        dist = {start: 0.0}
        came = {}
        heap = [(np.linalg.norm(self.nodes[start] - self.nodes[end]), start)]
        while heap:
            _, n = heapq.heappop(heap)
            if n == end:
                break
            for m in adj.get(n, []):
                d = dist[n] + np.linalg.norm(self.nodes[n] - self.nodes[m])
                if d < dist.get(m, float("inf")):
                    dist[m], came[m] = d, n
                    heapq.heappush(heap, (d + np.linalg.norm(self.nodes[m] - self.nodes[end]), m))
        if end not in dist:
            return [goal]                                   # not connected yet: head straight for it
        path, n = [end], end
        while n != start:
            n = came[n]
            path.append(n)
        pts = [self.nodes[i] for i in reversed(path)]
        return pts[1:] + [goal] if len(pts) > 1 else [goal]

    def heading_error(self, xy):
        """Radians to turn (+ = left) to face xy from the current pose."""
        p, yaw, _ = self.pose
        return wrap(math.atan2(xy[1] - p[1], xy[0] - p[0]) - yaw)
