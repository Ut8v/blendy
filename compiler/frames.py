"""Frame-range helpers shared by the renderer (inside Blender) and the render
queue (outside). No bpy."""

from __future__ import annotations

import os
import re

_FRAME_RE = re.compile(r"^frame_(\d{4})\.(png|exr|jpg)$")


def parse_range(text: str | None, start: int, end: int) -> tuple[int, int]:
    if not text:
        return start, end
    m = re.match(r"^(\d+)-(\d+)$", text.strip())
    if not m:
        raise RuntimeError(f"bad frame range '{text}', expected a-b")
    a, b = int(m.group(1)), int(m.group(2))
    if a > b:
        raise RuntimeError(f"bad frame range '{text}': start after end")
    return max(a, start), min(b, end)


def frames_done(out_dir: str) -> set[int]:
    if not os.path.isdir(out_dir):
        return set()
    return {int(m.group(1)) for f in os.listdir(out_dir) if (m := _FRAME_RE.match(f))}
