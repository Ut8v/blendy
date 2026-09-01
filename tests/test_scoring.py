import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals.scoring import read_png, regressed, score   # noqa: E402


def write_png(path, img):
    """Minimal RGB8 PNG writer (filter 0) for tests."""
    h, w, _ = img.shape
    raw = b"".join(b"\x00" + (img[y] * 255).astype(np.uint8).tobytes() for y in range(h))
    def chunk(t, b):
        return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b) & 0xFFFFFFFF)
    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    Path(path).write_bytes(data)


def gradient(w=96, h=64, shift=0.0):
    img = np.zeros((h, w, 3), dtype=np.float32)
    img[..., 0] = np.linspace(0, 0.7, w)[None, :]
    img[..., 1] = np.linspace(0, 0.7, h)[:, None]
    img[..., 2] = 0.5
    return np.clip(img + shift, 0, 1)


class TestScoring(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_png_round_trip(self):
        img = gradient()
        write_png(self.tmp / "a.png", img)
        back = read_png(self.tmp / "a.png")
        self.assertEqual(back.shape, img.shape)
        self.assertLess(np.abs(back - img).max(), 1 / 255 + 1e-6)

    def test_identical_scores_one(self):
        write_png(self.tmp / "a.png", gradient())
        s = score(self.tmp / "a.png", self.tmp / "a.png")
        for m, v in s.items():
            self.assertAlmostEqual(v, 1.0, places=6, msg=m)

    def test_exposure_shift_lowers_luminance_not_structure(self):
        write_png(self.tmp / "a.png", gradient())
        write_png(self.tmp / "b.png", gradient(shift=0.2))
        s = score(self.tmp / "b.png", self.tmp / "a.png")
        self.assertLess(s["luminance"], 0.85)
        self.assertGreater(s["structure"], 0.95)

    def test_regression_detection_with_tolerance(self):
        self.assertEqual(regressed({"pixel": 0.995}, {"pixel": 1.0}), [])
        self.assertEqual(regressed({"pixel": 0.9}, {"pixel": 1.0}), ["pixel"])


if __name__ == "__main__":
    unittest.main()
