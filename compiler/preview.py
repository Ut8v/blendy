"""Preview renders: three fixed angles, fast. The agent reads these back.

fast    Workbench, 480p, no AA. Placement, scale, composition, occlusion.
lookdev EEVEE at low samples. Materials and lighting.
Cycles never runs here.
"""

from __future__ import annotations

import math
import os
from typing import Any

import bpy
from mathutils import Vector

from .coords import look_at_euler
from .refs import ROOT
from .resolvers.cameras import scene_bounds

DEFAULT_ANGLES = ["camera", "top", "three_quarter"]
PREVIEW_DIR = ROOT / "preview"

QUALITY = {
    "fast": {"engine": "BLENDER_WORKBENCH", "height": 480, "samples": 1},
    "lookdev": {"engine": "BLENDER_EEVEE", "height": 540, "samples": 8},
}


def _preview_camera(ctx, name: str, lo: Vector, hi: Vector) -> bpy.types.Object:
    """Deterministic helper cameras derived from scene bounds."""
    center = (lo + hi) / 2
    extent = max((hi - lo).length, 1.0)
    data = bpy.data.cameras.new(f"PREVIEW_{name}")
    obj = bpy.data.objects.new(f"PREVIEW_{name}", data)
    ctx.link(obj, "Helpers")
    if name == "top":
        data.type = "ORTHO"
        data.ortho_scale = extent * 1.15
        obj.location = Vector((center.x, center.y, hi.z + extent))
        obj.rotation_euler = (0.0, 0.0, 0.0)
    elif name == "three_quarter":
        data.type, data.lens = "PERSP", 35.0
        eye = center + Vector((1.0, -1.0, 0.7)).normalized() * extent * 2.1
        obj.location = eye
        obj.rotation_euler = look_at_euler(eye, center)
    else:
        raise RuntimeError(f"unknown preview angle '{name}'")
    data.clip_end = extent * 10 + 100
    return obj


def _set_quality(scene: bpy.types.Scene, quality: str) -> tuple[int, int]:
    q = QUALITY[quality]
    from .scene import apply_render_settings
    base = {"engine": q["engine"], "samples": q["samples"], "resolution": [1, 1],
            "output": "", "camera": "", "film_transparent": False, "file_format": "PNG"}
    apply_render_settings(scene, base, None)
    aspect = scene.render.resolution_x / scene.render.resolution_y if scene.render.resolution_y else 16 / 9
    return q["height"], aspect


def render_preview(ctx, quality: str = "fast", angles: list[str] | None = None,
                   out_dir: str | None = None, frame: int | None = None) -> list[str]:
    scene = ctx.scene
    spec = ctx.spec
    angles = angles or DEFAULT_ANGLES
    out_dir = out_dir or str(PREVIEW_DIR / spec["meta"]["name"])
    os.makedirs(out_dir, exist_ok=True)

    res_x, res_y = spec["render"]["resolution"]
    aspect = res_x / res_y
    height, _ = _set_quality(scene, quality)
    scene.render.resolution_x = int(round(height * aspect))
    scene.render.resolution_y = height
    scene.render.image_settings.file_format = "PNG"
    scene.render.use_overwrite = True
    scene.frame_set(frame if frame is not None else spec["meta"]["frame_start"])

    lo, hi = scene_bounds(ctx)
    shot_camera = scene.camera
    outputs = []
    for angle in angles:
        cam = shot_camera if angle == "camera" else _preview_camera(ctx, angle, lo, hi)
        scene.camera = cam
        path = os.path.join(out_dir, f"{angle}_{quality}_f{scene.frame_current:04d}.png")
        scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        outputs.append(path)
    scene.camera = shot_camera
    return outputs
