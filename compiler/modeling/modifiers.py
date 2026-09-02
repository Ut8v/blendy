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
            _cloth(obj, m, name, objects, frame_end)
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


def _cloth(obj: bpy.types.Object, m: dict[str, Any], name: str,
           objects: dict[str, bpy.types.Object], frame_end: int) -> None:
    """Pin part of the sheet, let it fall onto the colliders, freeze at a frame.

    Deterministic for fixed settings: the same recipe bakes the same drape.
    """
    frame = int(m["frame"])
    scene = bpy.context.scene
    group = obj.vertex_groups.new(name="pin")
    group.add(_pinned(obj, m), 1.0, "REPLACE")

    for part in m.get("collide", []):
        collider = objects[part]
        if not any(mod.type == "COLLISION" for mod in collider.modifiers):
            collider.modifiers.new("collision", "COLLISION")
            collider.collision.thickness_outer = m.get("clearance", 0.008)

    mod = obj.modifiers.new(name, "CLOTH")
    st = mod.settings
    st.vertex_group_mass = "pin"
    st.quality = int(m.get("quality", 8))
    st.mass = m.get("mass", 0.25)
    st.tension_stiffness = st.compression_stiffness = m.get("stiffness", 12.0)
    st.shear_stiffness = m.get("stiffness", 12.0) * 0.5
    st.bending_stiffness = m.get("bending", 0.4)
    st.air_damping = 1.2
    if m.get("collide"):
        mod.collision_settings.use_collision = True
        mod.collision_settings.distance_min = m.get("clearance", 0.008)
        mod.collision_settings.use_self_collision = bool(m.get("self_collision", False))
    mod.point_cache.frame_start, mod.point_cache.frame_end = 1, max(frame, 2)

    for f in range(1, frame + 1):
        scene.frame_set(f)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    baked = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph))
    old_mesh = obj.data
    obj.modifiers.remove(mod)
    obj.data = baked
    bpy.data.meshes.remove(old_mesh)
    scene.frame_set(1)


def _pinned(obj: bpy.types.Object, m: dict[str, Any]) -> list[int]:
    """Vertices held in place: a named side, or a normalized span on one axis."""
    region = m.get("pin_region")
    if region:
        idx = _AXIS[region["axis"]]
        vals = [v.co[idx] for v in obj.data.vertices]
        lo, hi = min(vals), max(vals)
        span = (hi - lo) or 1.0
        r0, r1 = region.get("min", 0.0), region.get("max", 1.0)
        return [v.index for v in obj.data.vertices if r0 <= (v.co[idx] - lo) / span <= r1]
    axis, sign = {"top": (2, 1), "bottom": (2, -1), "left": (0, -1), "right": (0, 1),
                  "front": (1, -1), "back": (1, 1)}[m["pin"]]
    coords = [v.co[axis] for v in obj.data.vertices]
    extreme = max(coords) if sign > 0 else min(coords)
    span = (max(coords) - min(coords)) or 1.0
    return [v.index for v in obj.data.vertices if abs(v.co[axis] - extreme) < 0.06 * span]
