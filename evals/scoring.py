"""Eval scoring: compare a rendered preview against the accepted reference.

Metrics (all in [0, 1], higher is better):
  pixel      1 - mean absolute difference on a 64x64 downsample (composition, placement)
  luminance  1 - |mean luminance delta|                          (exposure, lighting key)
  structure  cosine similarity of 8x8 block gradient histograms  (edges: framing, silhouettes)

Deliberately simple and deterministic. Reference outputs are human-accepted
renders; the score says "did it move away from what was accepted", nothing more.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import numpy as np

METRICS = ("pixel", "luminance", "structure")
REGRESSION_TOLERANCE = 0.01     # a metric may drop this much and still count as no regression


def read_png(path: str | Path) -> np.ndarray:
    """Minimal PNG reader (8-bit RGB/RGBA/gray, non-interlaced) -> float32 HxWx3 in [0,1]."""
    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    pos, chunks, meta = 8, [], {}
    while pos < len(data):
        length, = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            w, h, depth, color, _, _, interlace = struct.unpack(">IIBBBBB", body)
            meta = {"w": w, "h": h, "depth": depth, "color": color, "interlace": interlace}
        elif ctype == b"IDAT":
            chunks.append(body)
        pos += 12 + length
    if meta["depth"] != 8 or meta["interlace"]:
        raise ValueError("only 8-bit non-interlaced PNGs are supported")
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[meta["color"]]
    raw = zlib.decompress(b"".join(chunks))
    w, h = meta["w"], meta["h"]
    stride = w * channels
    out = np.zeros((h, stride), dtype=np.uint8)
    prev = np.zeros(stride, dtype=np.int32)
    p = 0
    for y in range(h):
        f = raw[p]
        line = np.frombuffer(raw[p + 1:p + 1 + stride], dtype=np.uint8).astype(np.int32)
        p += 1 + stride
        if f == 0:
            cur = line
        elif f == 1:
            cur = line.copy()
            for i in range(channels, stride):
                cur[i] = (cur[i] + cur[i - channels]) & 0xFF
        elif f == 2:
            cur = (line + prev) & 0xFF
        elif f == 3:
            cur = line.copy()
            for i in range(stride):
                left = cur[i - channels] if i >= channels else 0
                cur[i] = (cur[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif f == 4:
            cur = line.copy()
            for i in range(stride):
                a = cur[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                cur[i] = (cur[i] + pred) & 0xFF
        else:
            raise ValueError(f"bad PNG filter {f}")
        out[y] = cur
        prev = cur
    img = out.reshape(h, w, channels).astype(np.float32) / 255.0
    if channels == 1:
        img = np.repeat(img, 3, axis=2)
    elif channels == 2:
        img = np.repeat(img[:, :, :1], 3, axis=2)
    elif channels == 4:
        img = img[:, :, :3]
    return img


def downsample(img: np.ndarray, size: int = 64) -> np.ndarray:
    h, w, _ = img.shape
    ys = (np.arange(size) * h // size)
    xs = (np.arange(size) * w // size)
    return img[ys][:, xs]


def luminance(img: np.ndarray) -> np.ndarray:
    return img[..., 0] * 0.2126 + img[..., 1] * 0.7152 + img[..., 2] * 0.0722


def gradient_hist(img: np.ndarray, blocks: int = 8, bins: int = 8) -> np.ndarray:
    lum = luminance(downsample(img, 64))
    gy, gx = np.gradient(lum)
    mag, ang = np.hypot(gx, gy), np.arctan2(gy, gx)
    b = 64 // blocks
    hist = []
    for by in range(blocks):
        for bx in range(blocks):
            m = mag[by * b:(by + 1) * b, bx * b:(bx + 1) * b].ravel()
            a = ang[by * b:(by + 1) * b, bx * b:(bx + 1) * b].ravel()
            h, _ = np.histogram(a, bins=bins, range=(-np.pi, np.pi), weights=m)
            hist.append(h)
    v = np.concatenate(hist)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def score(candidate: str | Path, reference: str | Path) -> dict[str, float]:
    a, b = read_png(candidate), read_png(reference)
    da, db = downsample(a), downsample(b)
    pixel = float(1.0 - np.mean(np.abs(da - db)))
    lum = float(1.0 - abs(luminance(da).mean() - luminance(db).mean()))
    ha, hb = gradient_hist(a), gradient_hist(b)
    structure = float(np.dot(ha, hb)) if ha.any() and hb.any() else 1.0
    return {"pixel": pixel, "luminance": lum, "structure": structure}


def regressed(new: dict[str, float], old: dict[str, float], tol: float = REGRESSION_TOLERANCE) -> list[str]:
    return [m for m in METRICS if m in old and new.get(m, 0.0) < old[m] - tol]
