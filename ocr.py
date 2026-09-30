"""Shared text / vision helpers: EasyOCR, fuzzy matching, Roblox-style screen regions.

EasyOCR's text detector takes seconds per region on CPU, so callers locate text by pixels
(white chips and cards, gold labels, the red CANCEL button) and only run the recogniser
(read_line) on single-line crops. ocr() with the detector is kept for slow fallbacks.
"""
import difflib
import re
from dataclasses import dataclass

import cv2

import config

REF_H = 1172          # client height the pixel constants were measured at


_reader = None

ALLOWLIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!?,.'#:-() "

def reader():
    """EasyOCR (CRAFT detector + CRNN recogniser), loaded once on first use."""
    global _reader
    if _reader is None:
        import easyocr
        import torch
        _reader = easyocr.Reader(["en"], gpu=torch.cuda.is_available(), verbose=False)
    return _reader

@dataclass
class Word:
    text: str
    box: tuple            # x0, y0, x1, y1 in client pixels

    @property
    def cx(self):
        return (self.box[0] + self.box[2]) / 2

    @property
    def cy(self):
        return (self.box[1] + self.box[3]) / 2

    @property
    def h(self):
        return self.box[3] - self.box[1]

def ocr(img, box=None, scale=1.0):
    """Words in img (or in the client-pixel box of it), with boxes in img coordinates."""
    x0, y0 = (box[0], box[1]) if box else (0, 0)
    crop = img[box[1]:box[3], box[0]:box[2]] if box else img
    if crop.size == 0:
        return []
    if scale != 1.0:
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    words = []
    for pts, text, conf in reader().readtext(crop, detail=1, paragraph=False,
                                             allowlist=ALLOWLIST, width_ths=0.3):
        if conf < config.OCR_MIN_CONF or not text.strip():
            continue
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        words.append(Word(text.strip(), (x0 + min(xs) / scale, y0 + min(ys) / scale,
                                         x0 + max(xs) / scale, y0 + max(ys) / scale)))
    return words

def lines(words):
    """Group words into text lines: same row and nearly touching, left to right."""
    out = []
    for w in sorted(words, key=lambda w: w.box[0]):
        for ln in out:
            last = ln[-1]
            h = max(w.h, last.h)
            if abs(w.cy - last.cy) < 0.5 * h and -0.5 * h < w.box[0] - last.box[2] < 1.0 * h:
                ln.append(w)
                break
        else:
            out.append([w])
    return sorted(out, key=lambda ln: (ln[0].cy, ln[0].box[0]))

def join(ln):
    """A line of words as one Word."""
    return Word(" ".join(w.text for w in ln),
                (min(w.box[0] for w in ln), min(w.box[1] for w in ln),
                 max(w.box[2] for w in ln), max(w.box[3] for w in ln)))

def squash(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())

def fuzzy_in(text, phrase):
    """How well phrase appears somewhere in text, 0..1. Short phrases must match a whole word."""
    t, p = squash(text), squash(phrase)
    if not t or not p:
        return 0.0
    if len(p) <= 4:                     # one OCR slip allowed ("Hil" for "Hi")
        r = max((difflib.SequenceMatcher(None, squash(w), p).ratio() for w in text.split()), default=0.0)
        return r if r >= 0.8 else 0.0
    if p in t:
        return 1.0
    best = 0.0
    for n in {len(p) - 1, len(p), len(p) + 1}:
        for i in range(max(1, len(t) - n + 1)):
            best = max(best, difflib.SequenceMatcher(None, t[i:i + n], p).ratio())
    return best

def best_alias(text, table, cutoff=None):
    """table {canonical: [aliases]} -> canonical whose alias best appears in text, or None."""
    cutoff = config.MATCH_CUTOFF if cutoff is None else cutoff
    best, best_score = None, cutoff
    for name, aliases in table.items():
        score = max(fuzzy_in(text, a) for a in aliases)
        if score > best_score or (score == best_score == 1.0 and best and len(name) > len(best)):
            best, best_score = name, score
    return best

def best_words(text, table, cutoff=0.75):
    """Like best_alias, but compares whole-word windows, so 'please' can't pass for 'Maple'."""
    words = [squash(w) for w in text.split()]
    words = [w for w in words if w]
    best, best_score, best_len = None, cutoff, 0
    for name, aliases in table.items():
        for alias in aliases:
            a = squash(alias)
            n = len(alias.split())
            score = 1.0 if a in "".join(words) and len(a) > 4 else 0.0
            for m in {max(1, n - 1), n, n + 1}:
                for i in range(len(words) - m + 1):
                    score = max(score, difflib.SequenceMatcher(None, "".join(words[i:i + m]), a).ratio())
            if score > best_score or (score == best_score and best and len(a) > best_len):
                best, best_score, best_len = name, score, len(a)
    return best

def whole_match(text, phrase):
    """Similarity of a whole line to a phrase (card labels, where substrings would lie)."""
    return difflib.SequenceMatcher(None, squash(text), squash(phrase)).ratio()

def _parent_rect(W, H):
    """Shared parent (the Roblox client viewport) in client pixels."""
    return (W * config.PARENT_POS_X, H * config.PARENT_POS_Y,
            W * config.PARENT_SIZE_X, H * config.PARENT_SIZE_Y)

def region_box(region, W, H):
    """Client-pixel box (x0, y0, x1, y1) of a Roblox-style ((anchor), (position), (size)) region."""
    (ax, ay), (px, py), (sx, sy) = region
    pl, pt, pw, ph = _parent_rect(W, H)
    bw, bh = pw * sx, ph * sy
    x0 = pl + pw * px - bw * ax
    y0 = pt + ph * py - bh * ay
    return int(x0), int(y0), int(x0 + bw), int(y0 + bh)

def read_line(img, box, scale=2.0, allowlist=ALLOWLIST):
    """Recognise one line of text in box -> (text, confidence). No detector, so it's fast."""
    H, W = img.shape[:2]
    x0, y0, x1, y1 = max(0, int(box[0])), max(0, int(box[1])), min(W, int(box[2])), min(H, int(box[3]))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return "", 0.0
    crop = img[y0:y1, x0:x1]
    if crop.ndim == 3:
        crop = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    if scale != 1.0:
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    h, w = crop.shape
    res = reader().recognize(crop, horizontal_list=[[0, w, 0, h]], free_list=[],
                             detail=1, allowlist=allowlist)
    if not res:
        return "", 0.0
    return res[0][1].strip(), float(res[0][2])

def read_white_text(img, box):
    """World text is white with a dark outline: keep only the white fill, as black on white."""
    x0, y0, x1, y1 = (int(v) for v in box)
    crop = img[max(0, y0):y1, max(0, x0):x1]
    if crop.size == 0:
        return ""
    ink = 255 - cv2.inRange(crop, (245, 245, 245), (255, 255, 255))
    return read_line(ink, (0, 0, ink.shape[1], ink.shape[0]), 2.0)[0]

def white_boxes(img, region, w_range, h_range, min_fill):
    """Pure-white UI boxes (chips, cards) inside region; sizes in pixels at REF_H."""
    H, W = img.shape[:2]
    k = H / REF_H
    x0, y0, x1, y1 = region
    m = cv2.inRange(img[y0:y1, x0:x1], (250, 250, 250), (255, 255, 255))
    n, _, stats, _ = cv2.connectedComponentsWithStats(m)
    out = []
    for x, y, w, h, area in stats[1:]:
        if w_range[0] * k <= w <= w_range[1] * k and h_range[0] * k <= h <= h_range[1] * k \
                and area >= min_fill * w * h:
            out.append((int(x + x0), int(y + y0), int(x + x0 + w), int(y + y0 + h)))
    return out
