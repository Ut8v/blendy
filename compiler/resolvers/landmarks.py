"""Runtime landmark resolution. Runs after assets load and rigs bind, so bone
landmarks are live and follow the pose.

A landmark becomes a named empty:
  bone landmark   -> empty parented to the bone (parent_type BONE), offset to head/tail
  socket landmark -> empty parented to the asset root at the socket's local position
Cameras and lights then track the empty; anchored objects are parented to it.
"""

from __future__ import annotations

from typing import Any

import bpy
from mathutils import Matrix, Vector

from ..refs import is_ref, parse_ref


def _profile_for(ctx, asset_id: str) -> dict[str, Any] | None:
    resolved = ctx.resolved.get(asset_id, {})
    if resolved.get("profile"):
        return resolved["profile"]
    asset = next(a for a in ctx.spec["assets"] if a["id"] == asset_id)
    return ctx.profiles.profile_for(asset)


def _asset_root(ctx, asset_id: str) -> bpy.types.Object:
    root = ctx.objects[asset_id]
    return root


def _armature_for(ctx, asset_id: str) -> bpy.types.Object | None:
    for rig in ctx.spec["rigs"]:
        if rig["asset_id"] == asset_id and rig["id"] in ctx.armatures:
            return ctx.armatures[rig["id"]]
    return ctx.armatures.get(f"asset:{asset_id}")


def landmark_empty(ctx, ref: str) -> bpy.types.Object:
    """Get or create the empty for "@asset.landmark"."""
    if ref in ctx.landmark_empties:
        return ctx.landmark_empties[ref]
    lref = parse_ref(ref)
    profile = _profile_for(ctx, lref.asset_id)
    table = (profile or {}).get("landmarks", {})
    if lref.landmark not in table:
        raise RuntimeError(f"asset '{lref.asset_id}' has no landmark '{lref.landmark}' "
                           f"(have: {', '.join(sorted(table)) or 'none'})")
    entry = table[lref.landmark]
    name = f"LM_{lref.asset_id}_{lref.landmark}"
    empty = bpy.data.objects.new(name, None)
    empty.empty_display_type, empty.empty_display_size = "SPHERE", 0.04
    ctx.link(empty, "Landmarks")

    if entry["kind"] == "bone":
        arm = _armature_for(ctx, lref.asset_id)
        if arm is None:
            raise RuntimeError(f"landmark '{ref}' is a bone landmark but asset "
                               f"'{lref.asset_id}' has no armature in this scene")
        bone = arm.data.bones.get(entry["bone"])
        if bone is None:
            raise RuntimeError(f"landmark '{ref}': armature has no bone '{entry['bone']}'")
        empty.parent = arm
        empty.parent_type = "BONE"
        empty.parent_bone = bone.name
        # Bone parenting attaches at the bone TAIL. Offset back to the head if asked.
        offset = Vector(entry.get("offset", (0, 0, 0)))
        if entry.get("end", "head") == "head":
            offset = offset + Vector((0, -bone.length, 0))
        empty.matrix_parent_inverse = Matrix.Identity(4)
        empty.location = offset
    elif entry["kind"] == "socket":
        root = _asset_root(ctx, lref.asset_id)
        empty.parent = root
        empty.matrix_parent_inverse = Matrix.Identity(4)
        empty.location = Vector(entry["position"])
        n = Vector(entry.get("normal", (0, 0, 1)))
        # to_track_quat("Z", "Y") is degenerate when the normal already is +Z: it
        # returns a 180-degree roll about Z, which mirrors every anchored child in
        # X and Y. A +Z normal means "no rotation", so leave the empty at identity.
        if n.length > 0 and (n.normalized() - Vector((0, 0, 1))).length > 1e-6:
            empty.rotation_mode = "QUATERNION"
            empty.rotation_quaternion = n.normalized().to_track_quat("Z", "Y")
        # A socket sits on a possibly non-uniformly scaled asset. Cancel that scale
        # on the empty so anything anchored to it keeps its own spec scale and
        # offsets are in meters, not in the parent's local units.
        bpy.context.view_layer.update()
        ws = root.matrix_world.to_scale()
        empty.scale = Vector(tuple(1.0 / s if abs(s) > 1e-9 else 1.0 for s in ws))
    else:
        raise RuntimeError(f"landmark '{ref}' has unknown kind {entry['kind']!r}")

    ctx.landmark_empties[ref] = empty
    return empty


def target_object(ctx, target: str) -> bpy.types.Object:
    """Object to aim at: a landmark empty or an entity's object."""
    if is_ref(target):
        return landmark_empty(ctx, target)
    if target not in ctx.objects:
        raise RuntimeError(f"target '{target}' is not a built entity")
    return ctx.objects[target]


def world_position(ctx, target, frame: int | None = None) -> Vector:
    """World position of a target (id, landmark ref, or vec3) at a frame."""
    if isinstance(target, (list, tuple)):
        return Vector(target)
    obj = target_object(ctx, target)
    if frame is not None:
        ctx.scene.frame_set(frame)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    return obj.evaluated_get(depsgraph).matrix_world.translation.copy()


def place_anchored(ctx) -> None:
    """Finish transforms that used a landmark: either a landmark-ref location
    (static world position at frame_start) or an anchor (live, parented)."""
    start = ctx.spec["meta"]["frame_start"]
    for section in ("assets", "cameras", "lights"):
        for entity in ctx.spec[section]:
            t = entity["transform"]
            obj = ctx.objects[entity["id"]]
            if t.get("anchor"):
                empty = landmark_empty(ctx, t["anchor"])
                obj.parent = empty
                obj.matrix_parent_inverse = Matrix.Identity(4)
                obj.location = Vector(t.get("anchor_offset", (0, 0, 0)))
            elif is_ref(t["location"]):
                obj.location = world_position(ctx, t["location"], start)
    ctx.scene.frame_set(start)


def add_track_to(obj: bpy.types.Object, target: bpy.types.Object) -> None:
    con = obj.constraints.new("TRACK_TO")
    con.name = "track_target"
    con.target = target
    con.track_axis = "TRACK_NEGATIVE_Z"
    con.up_axis = "UP_Y"
