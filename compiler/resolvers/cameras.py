"""Cameras: lens, depth of field, tracking, explicit keyframes, landmark moves."""

from __future__ import annotations

import math
from typing import Any

import bpy
from mathutils import Euler, Vector

from ..coords import spherical_to_offset
from .landmarks import add_track_to, target_object, world_position

_CHANNEL_PATH = {
    "location": ("location", 3), "rotation_euler": ("rotation_euler", 3),
    "scale": ("scale", 3),
}


def insert_key(id_block, data_path: str, frame: int, value, interpolation: str, index=-1):
    """Keyframe via the data API. Returns the keyframe point(s) so easing can be set."""
    if isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            setattr_index(id_block, data_path, i, v)
        id_block.keyframe_insert(data_path=data_path, frame=frame)
    else:
        setattr_path(id_block, data_path, value)
        id_block.keyframe_insert(data_path=data_path, frame=frame, index=index)
    action = id_block.animation_data.action
    for fc in _fcurves(action):
        if fc.data_path == data_path:
            for kp in fc.keyframe_points:
                if abs(kp.co.x - frame) < 1e-6:
                    kp.interpolation = interpolation


def _fcurves(action):
    """Action f-curves across the 4.4+ slotted-action API and the legacy one."""
    if hasattr(action, "layers") and action.layers:
        out = []
        for layer in action.layers:
            for strip in layer.strips:
                for cb in strip.channelbags:
                    out.extend(cb.fcurves)
        return out
    return action.fcurves


def setattr_path(block, path: str, value) -> None:
    parts = path.split(".")
    for p in parts[:-1]:
        block = getattr(block, p)
    setattr(block, parts[-1], value)


def setattr_index(block, path: str, index: int, value) -> None:
    parts = path.split(".")
    for p in parts[:-1]:
        block = getattr(block, p)
    vec = getattr(block, parts[-1])
    vec[index] = value
    setattr(block, parts[-1], vec)


def apply_keyframes(ctx, obj: bpy.types.Object, keyframes: list[dict[str, Any]],
                    scalar_paths: dict[str, tuple[Any, str]]) -> None:
    """scalar_paths maps a spec channel to (id_block, data_path) for non-transform channels."""
    for kf in keyframes:
        ch, frame, value, interp = kf["channel"], kf["frame"], kf["value"], kf["interpolation"]
        if ch in _CHANNEL_PATH:
            insert_key(obj, _CHANNEL_PATH[ch][0], frame, value, interp)
        elif ch in scalar_paths:
            block, path = scalar_paths[ch]
            insert_key(block, path, frame, value, interp)
        else:
            raise RuntimeError(f"{obj.name}: cannot keyframe channel '{ch}'")


def build_camera(ctx, cam: dict[str, Any], lens_set: list[float] | None = None) -> bpy.types.Object:
    cid = cam["id"]
    data = bpy.data.cameras.new(cid)
    data.type = cam["type"]
    data.sensor_fit = "HORIZONTAL"
    data.sensor_width = 36.0
    if cam["type"] == "PERSP":
        data.lens = cam["focal"]
    else:
        data.ortho_scale = cam["focal"]
    data.clip_start, data.clip_end = 0.05, 500.0

    obj = bpy.data.objects.new(cid, data)
    ctx.link(obj, "Cameras")
    from .assets import apply_transform
    t = cam["transform"]
    apply_transform(obj, t, location_override=Vector((0, 0, 0)) if isinstance(t["location"], str)
                    or t.get("anchor") else None)
    ctx.register(cid, obj)

    dof = cam.get("dof")
    if dof:
        data.dof.use_dof = True
        data.dof.focus_distance = dof["focus_distance"]
        data.dof.aperture_fstop = dof["f_stop"]
    return obj


def finish_camera(ctx, cam: dict[str, Any]) -> None:
    """Second pass, after landmarks exist: tracking, focus target, keyframes, moves."""
    obj = ctx.objects[cam["id"]]
    data = obj.data
    dof = cam.get("dof")
    if dof and dof.get("focus_target"):
        data.dof.focus_object = target_object(ctx, dof["focus_target"])
    if cam.get("track_target"):
        add_track_to(obj, target_object(ctx, cam["track_target"]))

    scalar = {"focal": (data, "lens" if cam["type"] == "PERSP" else "ortho_scale"),
              "dof.focus_distance": (data, "dof.focus_distance"),
              "dof.f_stop": (data, "dof.aperture_fstop")}
    apply_keyframes(ctx, obj, cam.get("keyframes", []), scalar)

    if cam.get("move"):
        build_move(ctx, obj, cam["move"])


def build_move(ctx, cam_obj: bpy.types.Object, move: dict[str, Any]) -> None:
    """A landmark-anchored move (hard rule 15).

    Two helper empties: an aim point whose location is keyframed to the resolved
    target at each key, and a rig that tracks it and carries the keyframed camera
    position. The camera is parented to the rig; its own local Z rotation is roll.
    Position is derived here, at compile time, from target + spherical offset, so
    the move follows the character when the shot is re-blocked.
    """
    cid = cam_obj.name
    aim = bpy.data.objects.new(f"MOVE_{cid}_aim", None)
    rig = bpy.data.objects.new(f"MOVE_{cid}_rig", None)
    for e in (aim, rig):
        e.empty_display_type, e.empty_display_size = "PLAIN_AXES", 0.1
        ctx.link(e, "Helpers")
    add_track_to(rig, aim)
    cam_obj.parent = rig
    cam_obj.matrix_parent_inverse.identity()
    cam_obj.location = (0, 0, 0)
    cam_obj.rotation_euler = Euler((0, 0, 0), "XYZ")
    for con in list(cam_obj.constraints):
        cam_obj.constraints.remove(con)

    lens_path = "lens" if cam_obj.data.type == "PERSP" else "ortho_scale"
    for key in move["keys"]:
        frame = key["frame"]
        target = world_position(ctx, key["target"], frame)
        offset = Vector(spherical_to_offset(key["distance"], key["azimuth"], key["elevation"]))
        interp = key["interpolation"]
        insert_key(aim, "location", frame, tuple(target), interp)
        insert_key(rig, "location", frame, tuple(target + offset), interp)
        insert_key(cam_obj, "rotation_euler", frame, (0.0, 0.0, math.radians(key.get("roll", 0.0))),
                   interp)
        insert_key(cam_obj.data, lens_path, frame, key["focal"], interp)
    ctx.scene.frame_set(ctx.spec["meta"]["frame_start"])


def scene_bounds(ctx) -> tuple[Vector, Vector]:
    """Axis-aligned bounds over every asset mesh, for the fixed preview angles."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    boxes = []
    for objs in ctx.meshes.values():
        for obj in objs:
            ev = obj.evaluated_get(depsgraph)
            pts = [ev.matrix_world @ Vector(c) for c in ev.bound_box]
            boxes.append((Vector(map(min, *pts)), Vector(map(max, *pts))))
    # Floors and ground planes are flat and huge; framing them hides the subject.
    solid = [b for b in boxes if (b[1].z - b[0].z) > 0.02]
    use = solid or boxes
    if not use:
        return Vector((-1, -1, 0)), Vector((1, 1, 2))
    lo = Vector(map(min, *(b[0] for b in use))) if len(use) > 1 else use[0][0].copy()
    hi = Vector(map(max, *(b[1] for b in use))) if len(use) > 1 else use[0][1].copy()
    return lo, hi
