"""Fuzzy text matching tolerant to OCR noise. Pure Python (difflib only)."""
from difflib import SequenceMatcher


def squash(s):
    """Lowercase and drop all whitespace, so 'Thai Tea' and 'ThaiTea' compare equal."""
    return "".join(s.lower().split())


def ratio(a, b):
    """Similarity (0..1) of two strings after squashing."""
    return SequenceMatcher(None, squash(a), squash(b)).ratio()


def contains(text, phrase, cutoff=0.75):
    """Fuzzy substring test: does any phrase-sized window of `text` match `phrase` at >= cutoff?"""
    text, phrase = squash(text), squash(phrase)
    if phrase in text:  # an exact window has ratio 1.0
        return True
    n = len(phrase)
    sm = SequenceMatcher(None)
    sm.set_seq2(phrase)  # seq2 is analysed once and reused for every window
    for i in range(max(1, len(text) - n + 1)):
        sm.set_seq1(text[i:i + n])
        # quick_ratio/real_quick_ratio are upper bounds of ratio(), so these prune without changing the result
        if (sm.real_quick_ratio() >= cutoff and sm.quick_ratio() >= cutoff
                and sm.ratio() >= cutoff):
            return True
    return False


def best_match(text, candidates, cutoff=0.7):
    """Best fuzzy match of any candidate against same-length word windows of text, or None.

    Compared with spaces removed and with windows of n-1..n+1 words, so OCR that glues or splits
    words ("ThailTea" for "Thai Tea", "Es Kopi Susu" read as "EsKopi Susu") still matches.
    """
    words = text.split()
    best, best_score = None, cutoff
    sm = SequenceMatcher(None)
    for cand in candidates:
        sm.set_seq2(squash(cand))
        n = len(cand.split())
        for size in range(max(1, n - 1), n + 2):
            for i in range(len(words) - size + 1):
                sm.set_seq1(squash("".join(words[i:i + size])))
                if sm.real_quick_ratio() <= best_score or sm.quick_ratio() <= best_score:
                    continue  # cannot beat the current best
                score = sm.ratio()
                if score > best_score:
                    best, best_score = cand, score
    return best
