"""Lights: type, energy, color, shape, tracking, keyframes."""

from __future__ import annotations

from typing import Any

import bpy
from mathutils import Vector

from .cameras import apply_keyframes
from .landmarks import add_track_to, target_object


def build_light(ctx, light: dict[str, Any]) -> bpy.types.Object:
    lid, kind = light["id"], light["type"]
    data = bpy.data.lights.new(lid, kind)
    data.energy = light["energy"]
    data.color = tuple(light["color"])
    size = light.get("size")
    if kind == "AREA":
        data.shape = "SQUARE"
        data.size = size
    elif kind == "SUN":
        data.angle = size if size is not None else 0.00918   # ~0.53 degrees, the real sun
    else:
        data.shadow_soft_size = size if size is not None else 0.1
    if kind == "SPOT":
        data.spot_size = light.get("spot_size", 0.785398)
        data.spot_blend = light.get("spot_blend", 0.15)
    data.use_shadow = True

    obj = bpy.data.objects.new(lid, data)
    ctx.link(obj, "Lights")
    from .assets import apply_transform
    t = light["transform"]
    override = Vector((0, 0, 0)) if isinstance(t["location"], str) or t.get("anchor") else None
    apply_transform(obj, t, location_override=override)
    ctx.register(lid, obj)
    return obj


def finish_light(ctx, light: dict[str, Any]) -> None:
    obj = ctx.objects[light["id"]]
    if light.get("track_target"):
        add_track_to(obj, target_object(ctx, light["track_target"]))
    scalar = {"energy": (obj.data, "energy"), "color": (obj.data, "color")}
    keyframes = light.get("keyframes", [])
    # "color" is a vec3 channel on the light data block, not the object.
    vec_color = [k for k in keyframes if k["channel"] == "color"]
    rest = [k for k in keyframes if k["channel"] != "color"]
    apply_keyframes(ctx, obj, rest, scalar)
    from .cameras import insert_key
    for kf in vec_color:
        insert_key(obj.data, "color", kf["frame"], tuple(kf["value"]), kf["interpolation"])
