"""Screen capture and change detection shared by the OCR modules (no EasyOCR dependency)."""
import threading

import cv2
import mss
import numpy as np

_MSS = getattr(mss, "MSS", None) or mss.mss  # mss.mss is deprecated in newer releases
_tls = threading.local()  # mss handles are per-thread on Windows


def grab(region):
    """BGRA screenshot of `region` ({left, top, width, height}) as a numpy array. Treat it as read-only."""
    sct = getattr(_tls, "sct", None)
    if sct is None:
        sct = _tls.sct = _MSS()
    return np.asarray(sct.grab(region))


def to_bgr(bgra):
    return cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)


class FrameGate:
    """Tells a polling loop whether a screen region still looks like the last frame it processed.

    The frame is shrunk to a tiny grayscale thumbnail and compared by mean absolute difference.
    The reference thumbnail only advances when a frame counts as changed, so slow drift still trips it.
    """

    def __init__(self, thumb_size, threshold):
        self._size = thumb_size
        self._threshold = threshold
        self._prev = None

    def unchanged(self, frame):
        small = cv2.resize(frame[:, :, :3], self._size, interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
        if self._prev is not None and float(np.mean(np.abs(gray - self._prev))) < self._threshold:
            return True
        self._prev = gray
        return False
