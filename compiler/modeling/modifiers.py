"""Modifier stack from a recipe part. Each entry maps to one Blender modifier,
applied in order. Part references (boolean, shrinkwrap) resolve against the
objects built so far."""

from __future__ import annotations

from typing import Any

import bpy

_AXIS = {"x": 0, "y": 1, "z": 2}


def apply_modifiers(obj: bpy.types.Object, mods: list[dict[str, Any]],
                    objects: dict[str, bpy.types.Object], frame_end: int = 1) -> None:
    for i, m in enumerate(mods):
        t = m["type"]
        name = f"{i:02d}_{t}"
        if t == "subdivision":
            mod = obj.modifiers.new(name, "SUBSURF")
            mod.levels = mod.render_levels = int(m["levels"])
        elif t == "bevel":
            mod = obj.modifiers.new(name, "BEVEL")
            mod.width = m["width"]
            mod.segments = int(m.get("segments", 3))
            mod.limit_method = "ANGLE"
            mod.angle_limit = m.get("angle", 0.523599)
        elif t == "mirror":
            mod = obj.modifiers.new(name, "MIRROR")
            mod.use_axis = [False, False, False]
            mod.use_axis[_AXIS[m["axis"]]] = True
            mod.use_mirror_merge = bool(m.get("merge", True))
        elif t == "solidify":
            mod = obj.modifiers.new(name, "SOLIDIFY")
            mod.thickness = m["thickness"]
            mod.offset = m.get("offset", -1.0) if isinstance(m.get("offset", -1.0), (int, float)) else -1.0
        elif t == "boolean":
            mod = obj.modifiers.new(name, "BOOLEAN")
            mod.operation = m["operation"].upper()
            mod.object = objects[m["part"]]
            mod.solver = "EXACT"
        elif t == "displace":
            tex = bpy.data.textures.new(f"{obj.name}.{name}", "CLOUDS")
            tex.noise_scale = m.get("scale", 0.25)
            tex.noise_depth = int(m.get("detail", 2))
            mod = obj.modifiers.new(name, "DISPLACE")
            mod.texture = tex
            mod.strength = m["strength"]
            mod.mid_level = 0.5
            mod.texture_coords = "LOCAL"
        elif t == "array":
            mod = obj.modifiers.new(name, "ARRAY")
            mod.count = int(m["count"])
            mod.use_relative_offset = False
            mod.use_constant_offset = True
            mod.constant_offset_displace = m["offset"]
        elif t == "shrinkwrap":
            mod = obj.modifiers.new(name, "SHRINKWRAP")
            mod.target = objects[m["part"]]
            mod.offset = m.get("offset", 0.0) if isinstance(m.get("offset", 0.0), (int, float)) else 0.0
            mod.wrap_method = "NEAREST_SURFACEPOINT"
            mod.wrap_mode = "OUTSIDE_SURFACE"
        elif t == "smooth":
            mod = obj.modifiers.new(name, "SMOOTH")
            mod.factor = m["factor"]
            mod.iterations = int(m.get("iterations", 5))
        elif t == "decimate":
            mod = obj.modifiers.new(name, "DECIMATE")
            mod.ratio = m["ratio"]
        elif t == "push":
            _push(obj, m)
        elif t == "cloth":
            _cloth(obj, m, name)
        else:
            raise RuntimeError(f"unknown modifier type {t!r}")


def _push(obj: bpy.types.Object, m: dict[str, Any]) -> None:
    """Sculpt by number: swell or dent the mesh around a point. `direction` makes
    it directional, otherwise it pushes radially away from the axis."""
    import bmesh

    from .loft import push, push_radial
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    if m.get("direction"):
        push(bm.verts, m["center"], m["radius"], m["direction"], m["strength"],
             m.get("falloff", "smooth"))
    else:
        push_radial(bm.verts, m["center"], m["radius"], m["strength"], m.get("axis", "z"))
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


def _cloth(obj: bpy.types.Object, m: dict[str, Any], name: str) -> None:
    """Pin one edge of the mesh, simulate, and freeze at a frame. Deterministic
    for fixed settings and cache. Milestone 23 refines this."""
    side, frame = m["pin"], int(m["frame"])
    axis = {"top": (2, 1), "bottom": (2, -1), "left": (0, -1), "right": (0, 1),
            "front": (1, -1), "back": (1, 1)}[side]
    coords = [v.co[axis[0]] for v in obj.data.vertices]
    extreme = max(coords) if axis[1] > 0 else min(coords)
    span = (max(coords) - min(coords)) or 1.0
    group = obj.vertex_groups.new(name="pin")
    pinned = [v.index for v in obj.data.vertices if abs(v.co[axis[0]] - extreme) < 0.06 * span]
    group.add(pinned, 1.0, "REPLACE")
    mod = obj.modifiers.new(name, "CLOTH")
    mod.settings.vertex_group_mass = "pin"
    mod.settings.quality = 6
    mod.settings.mass = 0.3
    mod.settings.tension_stiffness = mod.settings.compression_stiffness = m.get("stiffness", 15.0)
    mod.settings.bending_stiffness = 0.5
    mod.settings.air_damping = 1.0
    mod.point_cache.frame_start, mod.point_cache.frame_end = 1, frame
    scene = bpy.context.scene
    for f in range(1, frame + 1):
        scene.frame_set(f)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    baked = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph))
    old = obj.data
    obj.modifiers.remove(mod)
    obj.data = baked
    bpy.data.meshes.remove(old)
    scene.frame_set(1)
