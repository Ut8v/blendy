"""Final render: frames, not movies, into renders/<shot>/. Resumable: frames
that already exist on disk are skipped, so a crash costs the frame it was on.
"""

from __future__ import annotations

import os
from typing import Any

import bpy

from .frames import frames_done, parse_range
from .refs import ROOT

RENDER_DIR = ROOT / "renders"

def render_frames(ctx, frames: str | None = None, out_dir: str | None = None
                  ) -> tuple[list[str], dict[str, Any]]:
    scene, spec = ctx.scene, ctx.spec
    shot = spec.get("sequence", {}).get("shot_id") or spec["meta"]["name"]
    out_dir = out_dir or str(RENDER_DIR / shot)
    os.makedirs(out_dir, exist_ok=True)
    a, b = parse_range(frames, spec["meta"]["frame_start"], spec["meta"]["frame_end"])
    done = frames_done(out_dir)
    ext = {"PNG": "png", "OPEN_EXR": "exr", "JPEG": "jpg"}[scene.render.image_settings.file_format]

    scene.render.use_overwrite = False
    written, skipped = [], []
    for f in range(a, b + 1):
        path = os.path.join(out_dir, f"frame_{f:04d}.{ext}")
        if f in done:
            skipped.append(f)
            continue
        scene.frame_set(f)
        scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        written.append(path)
    total = spec["meta"]["frame_end"] - spec["meta"]["frame_start"] + 1
    now_done = frames_done(out_dir)
    return written, {"shot": shot, "out_dir": out_dir, "range": [a, b], "skipped": skipped,
                     "frames_done": len(now_done), "frames_total": total,
                     "complete": len(now_done) >= total}
