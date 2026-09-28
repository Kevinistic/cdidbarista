"""Build the café map from label bearings.

    python tools/mapper.py collect NAME       live: panorama at the current spot -> debug/map/
    python tools/mapper.py tour               live: walk to each landmark, panorama at each
    python tools/mapper.py solve [OBS.json]   observations (default debug/map/obs.json) -> map.json
    python tools/mapper.py show map.json      top-down picture of the map
    python tools/mapper.py selftest           solve a synthetic café with known answer

Model (top-down 2D, angles CCW, radians): each station label is a point L. The character
stands at a spot P; the third-person camera orbits it at distance D behind, heading yaw,
pitched down by `pitch`. A label seen at screen (u, v) has bearing b off the heading (+ right),
so the world direction from the camera to it is yaw - b. Least squares finds every L, P, yaw,
D and pitch that make those directions agree. Gauge: the two most-seen labels are pinned at
(0, 0) and (1, 0), so one map unit is the distance between them.
"""
import json
import math
import os
import sys
import time

import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
from nav import bearing, resect, wrap  # noqa: E402

VFOV = 70.0
VERBOSE = bool(os.environ.get("VERBOSE"))


def chain_yaws(obs, W, H, pitch):
    """Relative heading of every frame within its spot: consecutive frames of a panorama share
    labels, and a shared label's bearing shifts by exactly the turn between them."""
    by_spot = {}
    for o in obs:
        by_spot.setdefault(o["spot"], {}).setdefault(o["frame"], {})[o["label"]] = bearing(
            o["u"], o["v"], W, H, pitch, VFOV)
    rel, steps = {}, []
    for spot, frames in by_spot.items():
        order = sorted(frames)
        rel[order[0]] = 0.0
        for f0, f1 in zip(order, order[1:]):
            shared = set(frames[f0]) & set(frames[f1])
            d = np.median([wrap(frames[f1][n] - frames[f0][n]) for n in shared]) if shared else None
            if d is not None:
                steps.append(d)
            rel[f1] = rel[f0] + (d if d is not None else np.nan)
    fill = float(np.median(steps)) if steps else 0.0
    for spot, frames in by_spot.items():                  # no shared label: assume a typical step
        order = sorted(frames)
        for f0, f1 in zip(order, order[1:]):
            if np.isnan(rel[f1]):
                rel[f1] = rel[f0] + fill
    return rel


def triangulate(origins, dirs):
    """Least-squares intersection of 2D rays (point closest to all lines)."""
    A, rhs = np.zeros((2, 2)), np.zeros(2)
    for o, t in zip(origins, dirs):
        d = np.array([math.cos(t), math.sin(t)])
        M = np.eye(2) - np.outer(d, d)
        A += M
        rhs += M @ o
    return np.linalg.lstsq(A, rhs, rcond=None)[0]


def triangulate_all(origins, dirs, which, n):
    """triangulate() for many points at once: rays grouped by `which` (0..n-1)."""
    c, s = np.cos(dirs), np.sin(dirs)
    m00, m01, m11 = 1 - c * c, -c * s, 1 - s * s
    a00, a01, a11 = (np.bincount(which, w, n) for w in (m00, m01, m11))
    r0 = np.bincount(which, m00 * origins[:, 0] + m01 * origins[:, 1], n)
    r1 = np.bincount(which, m01 * origins[:, 0] + m11 * origins[:, 1], n)
    det = a00 * a11 - a01 * a01
    det = np.where(np.abs(det) < 1e-9, 1e-9, det)
    return np.stack([(a11 * r0 - a01 * r1) / det, (a00 * r1 - a01 * r0) / det], 1)


def incremental(obs, W, H, rel, pitch):
    """Initial layout: seed with the best-connected pair of spots, then add spots by resection,
    re-triangulating labels each time. -> (spot rotation, spot position, label position) dicts."""
    b = bearing(np.array([o["u"] for o in obs]), np.array([o["v"] for o in obs]), W, H, pitch, VFOV)
    rows = [(o["spot"], o["label"], rel[o["frame"]] - bi) for o, bi in zip(obs, b)]
    spots = sorted({r[0] for r in rows})
    seen_by = {sp: {r[1] for r in rows if r[0] == sp} for sp in spots}

    def mean_dir(sp, name):
        ds = [d for s2, n, d in rows if s2 == sp and n == name]
        return math.atan2(np.mean(np.sin(ds)), np.mean(np.cos(ds)))

    # seed pair: most shared labels
    pairs = [(len(seen_by[p] & seen_by[q]), p, q) for i, p in enumerate(spots) for q in spots[i + 1:]]
    _, A, B = max(pairs)
    shared = sorted(seen_by[A] & seen_by[B])
    dA = np.array([mean_dir(A, n) for n in shared])
    dB = np.array([mean_dir(B, n) for n in shared])
    best = None
    for rotB in np.linspace(-np.pi, np.pi, 120, endpoint=False):
        for phi in np.linspace(-np.pi, np.pi, 120, endpoint=False):
            PB = np.array([math.cos(phi), math.sin(phi)])
            L = triangulate_all(np.vstack([np.zeros((len(shared), 2)), np.tile(PB, (len(shared), 1))]),
                                np.concatenate([dA, dB + rotB]), np.tile(np.arange(len(shared)), 2), len(shared))
            eA = wrap(np.arctan2(L[:, 1], L[:, 0]) - dA)
            eB = wrap(np.arctan2(L[:, 1] - PB[1], L[:, 0] - PB[0]) - (dB + rotB))
            cost = np.mean(np.minimum(eA ** 2, 0.05)) + np.mean(np.minimum(eB ** 2, 0.05))
            if best is None or cost < best[0]:
                best = (cost, rotB, PB)
    rot = {A: 0.0, B: best[1]}
    pos = {A: np.zeros(2), B: best[2]}

    def retriangulate():
        names = sorted({n for sp in rot for n in seen_by[sp]})
        out = {}
        for n in names:
            obs_n = [(pos[sp], mean_dir(sp, n) + rot[sp]) for sp in rot if n in seen_by[sp]]
            if len(obs_n) >= 2:
                out[n] = triangulate([o for o, _ in obs_n], [d for _, d in obs_n])
        return out

    labels = retriangulate()
    left = [sp for sp in spots if sp not in rot]
    while left:
        left.sort(key=lambda sp: -len(seen_by[sp] & set(labels)))
        sp = left.pop(0)
        known = sorted(seen_by[sp] & set(labels))
        if len(known) < 2:
            continue                                          # can't place it: its frames get dropped
        P, r, _ = resect(np.array([labels[n] for n in known]), np.array([mean_dir(sp, n) for n in known]))
        rot[sp], pos[sp] = r, P
        labels = retriangulate()
    return rot, pos, labels


def placeable(obs, W, H):
    """Only what can be placed: labels seen from 2+ spots, spots that see 2+ of those (repeat
    until stable), then whatever a trial layout manages to place."""
    while True:
        spots_of = {}
        for o in obs:
            spots_of.setdefault(o["label"], set()).add(o["spot"])
        keep = [o for o in obs if len(spots_of[o["label"]]) >= 2]
        labels_of = {}
        for o in keep:
            labels_of.setdefault(o["spot"], set()).add(o["label"])
        keep = [o for o in keep if len(labels_of[o["spot"]]) >= 2]
        if len(keep) == len(obs):
            break
        obs = keep
    pitch = math.radians(20)
    rot, pos, L = incremental(obs, W, H, chain_yaws(obs, W, H, pitch), pitch)
    return [o for o in obs if o["spot"] in pos and o["label"] in L]


def solve(obs, W, H, pitches=range(6, 50, 4)):
    """obs: [{"frame": f, "spot": s, "label": name, "u": u, "v": v, "w": weight}].
    Returns dict with labels {name: (x, y)}, spots {s: (x, y)}, yaws {f: yaw}, D, pitch, rms (deg).
    Labels / spots that can't be placed are left out (listed under "dropped")."""
    given = obs
    obs = placeable(obs, W, H)
    dropped = sorted({o["label"] for o in given} - {o["label"] for o in obs}) +         sorted({o["spot"] for o in given} - {o["spot"] for o in obs})
    labels = sorted({o["label"] for o in obs}, key=lambda n: -sum(o["label"] == n for o in obs))
    frames = sorted({o["frame"] for o in obs})
    spots = sorted({o["spot"] for o in obs})
    a, b = labels[0], labels[1]                     # pinned: gauge (position, rotation, scale)
    free = labels[2:]
    li = {n: i for i, n in enumerate(free)}
    fi = {f: i for i, f in enumerate(frames)}
    si = {s: i for i, s in enumerate(spots)}
    nL, nF, nS = len(free), len(frames), len(spots)
    U = np.array([o["u"] for o in obs])
    V = np.array([o["v"] for o in obs])
    wts = np.array([o.get("w", 1.0) for o in obs])
    oL = [o["label"] for o in obs]
    oF = np.array([fi[o["frame"]] for o in obs])
    oS = np.array([si[o["spot"]] for o in obs])

    def unpack(x):
        pitch, D = x[0], x[1]
        L = x[2:2 + 2 * nL].reshape(nL, 2)
        P = x[2 + 2 * nL:2 + 2 * nL + 2 * nS].reshape(nS, 2)
        Y = x[2 + 2 * nL + 2 * nS:]
        return pitch, D, L, P, Y

    def label_xy(L):
        pos = {a: (0.0, 0.0), b: (1.0, 0.0)}
        pos.update({n: tuple(L[i]) for n, i in li.items()})
        return np.array([pos[n] for n in oL])

    def residuals(x):
        pitch, D, L, P, Y = unpack(x)
        yaw = Y[oF]
        cam = P[oS] - D * np.stack([np.cos(yaw), np.sin(yaw)], 1)
        lx = label_xy(L)
        seen = np.arctan2(lx[:, 1] - cam[:, 1], lx[:, 0] - cam[:, 0])
        return wts * wrap(seen - (yaw - bearing(U, V, W, H, pitch, VFOV)))

    sparsity = lil_matrix((len(obs), 2 + 2 * nL + 2 * nS + nF), dtype=int)
    for i, o in enumerate(obs):
        sparsity[i, 0:2] = 1
        if o["label"] in li:
            j = 2 + 2 * li[o["label"]]
            sparsity[i, j:j + 2] = 1
        j = 2 + 2 * nL + 2 * oS[i]
        sparsity[i, j:j + 2] = 1
        sparsity[i, 2 + 2 * nL + 2 * nS + oF[i]] = 1

    lo = np.concatenate([[0.0, 0.0], np.full(2 * nL + 2 * nS + nF, -np.inf)])
    hi = np.concatenate([[math.radians(70), 5.0], np.full(2 * nL + 2 * nS + nF, np.inf)])
    def fit(x0, pitch=None, nfev=4000):
        """Refine from x0; with pitch given, hold it fixed."""
        lo_, hi_ = lo.copy(), hi.copy()
        if pitch is not None:
            lo_[0], hi_[0] = pitch - 1e-6, pitch + 1e-6
            x0 = x0.copy()
            x0[0] = pitch
        return least_squares(residuals, x0, bounds=(lo_, hi_), loss="soft_l1", f_scale=0.05,
                             max_nfev=nfev, jac_sparsity=sparsity, x_scale="jac")

    # pitch is barely visible in bearings and a wrong one drags the layout into a wrong
    # minimum, so profile it: fit everything else at each pitch on a grid, then free it
    best = None
    for deg in pitches:
        pitch0 = math.radians(deg)
        rel = chain_yaws(obs, W, H, pitch0)
        rot, pos, L = incremental(obs, W, H, rel, pitch0)
        if a not in L or b not in L or any(sp not in pos for sp in spots) or any(n not in L for n in free):
            continue
        # move the layout into the label gauge: a -> (0, 0), b -> (1, 0)
        A, B = L[a], L[b]
        ang, sc = math.atan2(B[1] - A[1], B[0] - A[0]), np.linalg.norm(B - A) or 1.0
        R = np.array([[math.cos(-ang), -math.sin(-ang)], [math.sin(-ang), math.cos(-ang)]])
        tf = lambda p: R @ (np.asarray(p) - A) / sc      # noqa: E731
        Lg = np.array([tf(L[n]) for n in free]).ravel()
        Pg = np.array([tf(pos[sp]) for sp in spots]).ravel()
        spot_of = {o["frame"]: o["spot"] for o in obs}
        # each frame's heading straight from the placed labels (a chained guess can be far off):
        # heading = direction to the label + its bearing, averaged over the frame's labels
        Lpos = {n: tf(L[n]) for n in L}
        bear = bearing(U, V, W, H, pitch0, VFOV)
        Yg = np.array([rel[f] + rot[spot_of[f]] - ang for f in frames])
        for f, i in fi.items():
            idx = [k for k, o in enumerate(obs) if o["frame"] == f and o["label"] in Lpos]
            if idx:
                Psp = tf(pos[spot_of[f]])
                h = [math.atan2(Lpos[obs[k]["label"]][1] - Psp[1], Lpos[obs[k]["label"]][0] - Psp[0]) + bear[k]
                     for k in idx]
                Yg[i] = math.atan2(np.mean(np.sin(h)), np.mean(np.cos(h)))
        r = fit(np.concatenate([[pitch0, 0.05], Lg, Pg, wrap(Yg)]), pitch=pitch0, nfev=1500)
        if VERBOSE:
            print(f"  pitch {deg:2d}: cost {r.cost:8.3f}  rms {np.degrees(np.sqrt(np.mean(residuals(r.x) ** 2))):6.2f} deg")
        if best is None or r.cost < best.cost:
            best = r
    best = fit(best.x)
    if VERBOSE:
        print(f"  free: cost {best.cost:8.3f}  pitch {np.degrees(best.x[0]):.1f}")
    pitch, D, L, P, Y = unpack(best.x)
    res = residuals(best.x) / np.maximum(wts, 1e-9)
    pos = {a: (0.0, 0.0), b: (1.0, 0.0)}
    pos.update({n: tuple(map(float, L[i])) for n, i in li.items()})
    return {"labels": pos, "spots": {s: tuple(map(float, P[i])) for s, i in si.items()},
            "yaws": {f: float(Y[i]) for f, i in fi.items()}, "D": float(D), "pitch": float(pitch),
            "rms_deg": float(np.degrees(np.sqrt(np.mean(res ** 2)))), "cost": float(best.cost),
            "used": len(obs), "dropped": dropped}


# ---------------------------------------------------------------- synthetic test

def project(pt3, cam3, yaw, pitch, W, H):
    """World point -> screen (u, v) for a camera at cam3 with heading yaw, pitched down."""
    f = (H / 2) / math.tan(math.radians(VFOV) / 2)
    fwd = np.array([math.cos(yaw) * math.cos(pitch), math.sin(yaw) * math.cos(pitch), -math.sin(pitch)])
    right = np.array([math.sin(yaw), -math.cos(yaw), 0.0])
    down = np.cross(fwd, right)
    r = np.asarray(pt3) - np.asarray(cam3)
    z = r @ fwd
    if z <= 0.1:
        return None
    return W / 2 + f * (r @ right) / z, H / 2 + f * (r @ down) / z


def selftest():
    rng = np.random.default_rng(1)
    W, H = 1920, 1171
    labels = {f"L{i}": (rng.uniform(0, 10), rng.uniform(0, 6)) for i in range(14)}
    spots = {f"S{i}": (rng.uniform(1, 9), rng.uniform(1, 5)) for i in range(5)}
    D, pitch, cam_h, lab_h = 1.2, math.radians(22), 3.0, 2.2
    obs, truth_yaw = [], {}
    for s, (px, py) in spots.items():
        yaw0 = rng.uniform(-np.pi, np.pi)
        for k in range(20):
            yaw = yaw0 - k * math.radians(18) + rng.normal(0, 0.05)
            f = f"{s}_{k}"
            truth_yaw[f] = yaw
            cam = (px - D * math.cos(yaw), py - D * math.sin(yaw), cam_h)
            for n, (lx, ly) in labels.items():
                uv = project((lx, ly, lab_h), cam, yaw, pitch, W, H)
                if uv and 0 <= uv[0] < W and 0 <= uv[1] < H and rng.random() < 0.6:
                    obs.append({"frame": f, "spot": s, "label": n, "u": uv[0] + rng.normal(0, 3),
                                "v": uv[1] + rng.normal(0, 3), "w": 1.0})
    # the bearing formula must invert the projection
    cam = (0.0, 0.0, cam_h)
    for yaw in (0.3, 2.0):
        u, v = project((4.0, 2.5, lab_h), cam, yaw, pitch, W, H)
        truth = wrap(yaw - math.atan2(2.5, 4.0))
        assert abs(wrap(bearing(u, v, W, H, pitch, VFOV) - truth)) < 1e-6, "bearing() doesn't invert project()"
    print(f"{len(obs)} observations, {len(labels)} labels, {len(spots)} spots")
    m = solve(obs, W, H)
    # compare after the same gauge: map truth so the pinned pair sits at (0,0) and (1,0)
    a, b = list(m["labels"])[:2]
    A, B = np.array(labels[a]), np.array(labels[b])
    ang = math.atan2(*(B - A)[::-1])
    scale = np.linalg.norm(B - A)
    rot = np.array([[math.cos(-ang), -math.sin(-ang)], [math.sin(-ang), math.cos(-ang)]])
    err = [np.linalg.norm(rot @ (np.array(labels[n]) - A) / scale - np.array(m["labels"][n])) for n in labels]
    print(f"rms bearing {m['rms_deg']:.2f} deg, D {m['D']:.2f} (true {D / scale:.2f}),"
          f" pitch {math.degrees(m['pitch']):.1f} (true 22.0)")
    print(f"label position error: median {np.median(err):.3f}, max {max(err):.3f} map units")


def selftest_localize(trials=200):
    """nav.Map.localize on random poses in a synthetic café with a known map."""
    from nav import Map
    rng = np.random.default_rng(2)
    W, H = 1920, 1171
    labels = {f"L{i}": (rng.uniform(0, 10), rng.uniform(0, 6)) for i in range(14)}
    D, pitch = 1.2, math.radians(22)
    m = Map({"labels": labels, "pitch": pitch, "D": D})
    pos_err, yaw_err, n, t0 = [], [], 0, time.time()
    for _ in range(trials):
        P = np.array([rng.uniform(1, 9), rng.uniform(1, 5)])
        yaw = rng.uniform(-np.pi, np.pi)
        cam = (P[0] - D * math.cos(yaw), P[1] - D * math.sin(yaw), 3.0)
        obs = []
        for name, (lx, ly) in labels.items():
            uv = project((lx, ly, 2.2), cam, yaw, pitch, W, H)
            if uv and 0 <= uv[0] < W and 0 <= uv[1] < H:
                obs.append((name, uv[0] + rng.normal(0, 3), uv[1] + rng.normal(0, 3), 1.0))
        r = m.localize(obs, W, H)
        if r is None:
            continue
        n += 1
        pos_err.append(np.linalg.norm(r[0] - P))
        yaw_err.append(abs(math.degrees(wrap(r[1] - yaw))))
    print(f"localize: {n}/{trials} poses with 3+ labels, {1000 * (time.time() - t0) / trials:.0f} ms each;"
          f" position error median {np.median(pos_err):.3f} / p95 {np.percentile(pos_err, 95):.3f},"
          f" heading error median {np.median(yaw_err):.2f} / p95 {np.percentile(yaw_err, 95):.2f} deg")


# ---------------------------------------------------------------- live collection

MAP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "debug", "map")
TOUR = ["Milk", "Cup Rack", "Coffee Maker", "Syrup Bottle", "Bin", "Water Tap", "Ice Machine", "Bean Hopper"]


def append_obs(rows, W, H):
    os.makedirs(MAP_DIR, exist_ok=True)
    path = os.path.join(MAP_DIR, "obs.json")
    data = json.load(open(path)) if os.path.exists(path) else {"W": W, "H": H, "obs": []}
    data["obs"] += rows
    json.dump(data, open(path, "w"))
    return len(data["obs"])


def panorama(bot, spot, taps=26, tap_s=0.1):
    """Turn in taps at the current spot, observing labels each frame. The camera can pin against
    a wall and stop turning: then step back once and carry on as a new spot."""
    import cv2
    import nav
    rows, prev, stalls = [], None, 0
    for k in range(taps):
        img = bot.grab()
        H, W = img.shape[:2]
        cv2.imwrite(os.path.join(MAP_DIR, f"{spot}_{k:02d}.png"), img)
        if prev is not None and np.mean(np.abs(cv2.resize(img, (64, 40)).astype(int) -
                                               cv2.resize(prev, (64, 40)).astype(int))) < 2.0:
            stalls += 1
            if stalls == 2:
                print(f"  {spot}: camera pinned, stepping back; continuing as {spot}b")
                bot.tap(config.BACK, 0.2)
                time.sleep(0.4)
                spot += "b"
                stalls = 0
        else:
            stalls = 0
            for name, u, v, w in nav.observe(img):
                rows.append({"frame": f"{spot}_{k:02d}", "spot": spot, "label": name, "u": u, "v": v, "w": w})
        prev = img
        bot.tap(config.TURN_RIGHT, tap_s)
        time.sleep(0.35)
    total = append_obs(rows, W, H)
    print(f"  {spot}: {len(rows)} observations ({total} total)")


def live_bot():
    import threading
    import main
    from live_focus import focus
    bot = main.Bot(threading.Event())
    bot.enabled.set()
    bot.scr = main.Screen()
    bot.scr.find()
    focus(bot.scr)
    return bot


def collect(spot):
    os.makedirs(MAP_DIR, exist_ok=True)
    bot = live_bot()
    try:
        panorama(bot, spot)
    finally:
        bot.release_all()


def tour(stops=TOUR):
    """Walk to each landmark by its label text (no chips used), panorama there."""
    import main
    os.makedirs(MAP_DIR, exist_ok=True)
    bot = live_bot()
    try:
        panorama(bot, "start")
        for i, name in enumerate(stops):
            names, _ = main.station_info(name)
            names = names or [name]

            def locate(img, near):
                return main.find_station_label(img, names, near, chevron=False)

            def close_enough(img, target):          # stop once any prompt chip shows under it
                return next((c for c in main.find_chips(img) if target.box[1] < c.y < target.box[3] + 150), None)

            print(f"-> {name}")
            if bot.navigate(locate, close_enough, slow=True) is None:
                print(f"  couldn't reach {name}, panorama here anyway")
            panorama(bot, f"s{i}_{name.replace(' ', '')}")
    finally:
        bot.release_all()


def show(path, out):
    import cv2
    m = json.load(open(path))
    pts = list(m["labels"].values()) + list(m["spots"].values())
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    s = 700 / max(max(xs) - min(xs), max(ys) - min(ys), 1e-6)
    img = np.full((800, 800, 3), 24, np.uint8)

    def px(p):
        return int(50 + (p[0] - min(xs)) * s), int(750 - (p[1] - min(ys)) * s)

    for n, p in m["labels"].items():
        cv2.circle(img, px(p), 5, (80, 200, 250), -1)
        cv2.putText(img, n, (px(p)[0] + 7, px(p)[1] + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (230, 230, 230), 1)
    for n, p in m["spots"].items():
        cv2.drawMarker(img, px(p), (120, 230, 120), cv2.MARKER_CROSS, 12, 2)
        cv2.putText(img, n, (px(p)[0] + 7, px(p)[1] - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (120, 230, 120), 1)
    cv2.imwrite(out, img)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "selftest"
    if cmd == "selftest":
        selftest_localize()
        selftest()
    elif cmd == "collect":
        collect(sys.argv[2])
    elif cmd == "tour":
        tour()
    elif cmd == "solve":
        data = json.load(open(sys.argv[2] if len(sys.argv) > 2 else os.path.join(MAP_DIR, "obs.json")))
        m = solve(data["obs"], data["W"], data["H"])
        out = sys.argv[3] if len(sys.argv) > 3 else "map.json"
        json.dump(m, open(out, "w"), indent=1)
        print(f"{out}: {len(m['labels'])} labels, {len(m['spots'])} spots, rms {m['rms_deg']:.2f} deg,"
              f" D {m['D']:.2f}, pitch {math.degrees(m['pitch']):.1f}, {m['used']} obs used, dropped {m['dropped']}")
    elif cmd == "show":
        show(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "debug/map.png")
