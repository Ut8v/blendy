"""Model turntable: fixed views on a neutral ground, plus a head close-up for
characters, and a side-by-side with the reference image if the recipe names one.
This is what the modeling agent compares against."""

from __future__ import annotations

import math
import os
from typing import Any

import bpy
from mathutils import Vector

from compiler.coords import look_at_euler
from compiler.scene import apply_render_settings

VIEW_AZ = {"front": 0.0, "three_quarter": 35.0, "side": 90.0, "back": 180.0, "three_quarter_back": 145.0}


def _bounds(objects) -> tuple[Vector, Vector]:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    los, his = [], []
    for obj in objects.values():
        if obj.type != "MESH":
            continue
        ev = obj.evaluated_get(depsgraph)
        pts = [ev.matrix_world @ Vector(c) for c in ev.bound_box]
        los.append(Vector(map(min, *pts))); his.append(Vector(map(max, *pts)))
    if not los:
        return Vector((-0.5, -0.5, 0)), Vector((0.5, 0.5, 1))
    return Vector(map(min, *los)), Vector(map(max, *his))


def _studio_lights(scene, center: Vector, extent: float) -> None:
    for name, loc, energy, color in (("key", (-1.2, -1.5, 1.6), 220, (1.0, 0.96, 0.9)),
                                     ("fill", (1.6, -1.2, 0.8), 80, (0.85, 0.9, 1.0)),
                                     ("rim", (0.6, 1.6, 1.4), 160, (1.0, 1.0, 1.0))):
        data = bpy.data.lights.new(f"TT_{name}", "AREA")
        data.energy, data.color, data.size = energy * extent, color, 1.2 * extent
        obj = bpy.data.objects.new(f"TT_{name}", data)
        scene.collection.objects.link(obj)
        obj.location = center + Vector(loc) * extent
        obj.rotation_euler = look_at_euler(obj.location, center)
    world = bpy.data.worlds.new("TT_World")
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.42, 0.42, 0.45, 1.0)
    bg.inputs["Strength"].default_value = 0.35
    scene.world = world
    ground = bpy.data.meshes.new("TT_ground")
    import bmesh
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=extent * 6)
    bm.to_mesh(ground); bm.free()
    g = bpy.data.objects.new("TT_ground", ground)
    scene.collection.objects.link(g)
    g.location = Vector((center.x, center.y, 0.0))
    mat = bpy.data.materials.new("TT_ground")
    mat.diffuse_color = (0.3, 0.3, 0.32, 1.0)
    ground.materials.append(mat)


def render(scene, model: dict[str, Any], objects, out_dir: str, views: list[str], quality: str) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    lo, hi = _bounds(objects)
    center, extent = (lo + hi) / 2, max((hi - lo).length, 0.2)
    _studio_lights(scene, center, extent)
    engine = "BLENDER_EEVEE" if quality == "lookdev" else "BLENDER_WORKBENCH"
    apply_render_settings(scene, {"engine": engine, "samples": 16, "resolution": [720, 900], "output": "",
                                  "camera": "", "film_transparent": False, "file_format": "PNG"})
    scene.render.use_overwrite = True
    cam_data = bpy.data.cameras.new("TT_cam")
    cam_data.lens = 60.0
    cam = bpy.data.objects.new("TT_cam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    outputs = []
    for view in views:
        if view == "head":
            if model["kind"] != "character":
                continue
            head = Vector((center.x, center.y, hi.z - 0.12 * (hi.z - lo.z)))
            dist = 0.22 * (hi.z - lo.z) + 0.3
            az = math.radians(20.0)
            cam.location = head + Vector((dist * math.sin(az), -dist * math.cos(az), dist * 0.15))
            cam.rotation_euler = look_at_euler(cam.location, head)
            cam_data.lens = 85.0
        else:
            az = math.radians(VIEW_AZ.get(view, 0.0))
            dist = extent * 1.9
            cam.location = center + Vector((dist * math.sin(az), -dist * math.cos(az), extent * 0.12))
            cam.rotation_euler = look_at_euler(cam.location, center)
            cam_data.lens = 60.0
        path = os.path.join(out_dir, f"{view}.png")
        scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        outputs.append(path)
    ref = model.get("reference")
    if ref and os.path.exists(ref if os.path.isabs(ref) else str(_root() / ref)):
        outputs.append(_side_by_side(out_dir, outputs[0], ref if os.path.isabs(ref) else str(_root() / ref)))
    return outputs


def _root():
    from compiler.refs import ROOT
    return ROOT


def _side_by_side(out_dir: str, render_path: str, ref_path: str) -> str:
    """Reference beside the front render, same height, via Blender's compositor-free
    image API: load both, scale the reference, paste into one canvas."""
    a = bpy.data.images.load(render_path, check_existing=False)
    b = bpy.data.images.load(ref_path, check_existing=False)
    h = a.size[1]
    bw = max(1, int(b.size[0] * h / max(1, b.size[1])))
    b.scale(bw, h)
    w = a.size[0] + bw
    canvas = bpy.data.images.new("TT_compare", w, h, alpha=False)
    import numpy as np
    pa = np.array(a.pixels[:], dtype=np.float32).reshape(h, a.size[0], 4)
    pb = np.array(b.pixels[:], dtype=np.float32).reshape(h, bw, 4)
    out = np.concatenate([pb, pa], axis=1)
    canvas.pixels = out.ravel().tolist()
    path = os.path.join(out_dir, "compare.png")
    canvas.filepath_raw = path
    canvas.file_format = "PNG"
    canvas.save()
    for img in (a, b, canvas):
        bpy.data.images.remove(img)
    return path
