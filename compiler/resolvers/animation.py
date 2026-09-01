"""Animation clips onto rigs via the NLA. Retargeting is a bone-name remap plus
an optional rest-pose correction from the skeleton profile; clips from the same
skeleton type pass through unchanged.

Timing: the scene fps is already set (compiler step 3) before any clip imports.
"""

from __future__ import annotations

import math
import os
import re
from typing import Any

import bpy
from mathutils import Quaternion

from ..refs import ROOT
from .assets import _import_with_override
from .rigs import load_skeleton_profile

CLIP_DIR = ROOT / "clips"
_BONE_PATH = re.compile(r'^pose\.bones\["(.+?)"\]\.(.+)$')


def clip_path(skeleton: str, clip_ref: str) -> str:
    """'mixamo/walking_forward' -> clips/mixamo/walking_forward.fbx (or .glb/.blend)."""
    base = CLIP_DIR / clip_ref if "/" in clip_ref else CLIP_DIR / skeleton / clip_ref
    for ext in (".fbx", ".glb", ".gltf", ".blend"):
        p = base.with_suffix(ext)
        if p.exists():
            return str(p)
    have = sorted(p.stem for p in (CLIP_DIR / skeleton).glob("*")) if (CLIP_DIR / skeleton).exists() else []
    raise RuntimeError(f"clip '{clip_ref}' not found under clips/{skeleton}/ "
                       f"(have: {', '.join(have) or 'none'})")


def _fcurves(action):
    if hasattr(action, "layers") and action.layers:
        return [fc for layer in action.layers for strip in layer.strips
                for cb in strip.channelbags for fc in cb.fcurves]
    return list(action.fcurves)


def import_clip_action(path: str, name: str) -> bpy.types.Action:
    """Import a clip file, keep its action, delete the carrier objects."""
    before_obj, before_act = set(bpy.data.objects), set(bpy.data.actions)
    ext = os.path.splitext(path)[1].lower()
    if ext == ".fbx":
        _import_with_override(bpy.ops.import_scene.fbx, filepath=path, use_anim=True,
                              automatic_bone_orientation=False, ignore_leaf_bones=False)
    elif ext in (".glb", ".gltf"):
        _import_with_override(bpy.ops.import_scene.gltf, filepath=path)
    elif ext == ".blend":
        with bpy.data.libraries.load(path, link=False) as (src, dst):
            dst.actions = [a for a in src.actions]
    new_actions = [a for a in bpy.data.actions if a not in before_act]
    for obj in [o for o in bpy.data.objects if o not in before_obj]:
        bpy.data.objects.remove(obj, do_unlink=True)
    if not new_actions:
        raise RuntimeError(f"clip '{path}' contained no animation")
    action = max(new_actions, key=lambda a: a.frame_range[1] - a.frame_range[0])
    for extra in new_actions:
        if extra != action:
            bpy.data.actions.remove(extra)
    action.name = name
    action.use_fake_user = True
    return action


def retarget_action(action: bpy.types.Action, profile: dict[str, Any]) -> None:
    """Remap bone names and apply rest-pose corrections in place."""
    bone_map = profile.get("bone_map", {})
    corrections = {b: Quaternion(q) for b, q in profile.get("rest_correction", {}).items()}
    strip_prefix = profile.get("source_prefix")
    add_prefix = profile.get("prefix", "")
    quats: dict[str, list] = {}
    for fc in _fcurves(action):
        m = _BONE_PATH.match(fc.data_path)
        if not m:
            continue
        src, channel = m.group(1), m.group(2)
        dst = bone_map.get(src)
        if dst is None:
            base = src[len(strip_prefix):] if strip_prefix and src.startswith(strip_prefix) else src
            dst = base if base.startswith(add_prefix) else add_prefix + base
        if dst != src:
            fc.data_path = f'pose.bones["{dst}"].{channel}'
        if channel == "rotation_quaternion" and dst in corrections:
            quats.setdefault(dst, [None] * 4)[fc.array_index] = fc
    for bone, fcs in quats.items():
        if any(fc is None for fc in fcs):
            continue
        corr = corrections[bone]
        n = min(len(fc.keyframe_points) for fc in fcs)
        for i in range(n):
            q = Quaternion([fcs[k].keyframe_points[i].co.y for k in range(4)])
            q = corr @ q
            for k in range(4):
                fcs[k].keyframe_points[i].co.y = q[k]
        for fc in fcs:
            fc.update()


def apply_clip(ctx, anim: dict[str, Any]) -> None:
    """One NLA strip per spec entry. Blends come from the spec, never from auto-blend."""
    rig_spec = next(r for r in ctx.spec["rigs"] if r["id"] == anim["rig_id"])
    arm = ctx.armatures[anim["rig_id"]]
    profile = load_skeleton_profile(rig_spec["retarget_profile"])
    path = clip_path(rig_spec["skeleton"], anim["clip"])

    action_name = f"clip.{anim['rig_id']}.{anim['id']}"
    action = import_clip_action(path, action_name)
    retarget_action(action, profile)

    if arm.animation_data is None:
        arm.animation_data_create()
    track = arm.animation_data.nla_tracks.new()
    track.name = anim["id"]
    strip = track.strips.new(anim["id"], anim["frame_start"], action)
    strip.name = anim["id"]
    strip.use_auto_blend = False
    strip.blend_in = anim["blend_in"]
    strip.blend_out = anim["blend_out"]
    strip.blend_type = "REPLACE"
    strip.extrapolation = "HOLD_FORWARD"
    if anim["loop"]:
        length = action.frame_range[1] - action.frame_range[0]
        remaining = ctx.spec["meta"]["frame_end"] - anim["frame_start"]
        if length > 0 and remaining > length:
            strip.repeat = math.ceil(remaining / length)
    arm.animation_data.action = None      # the NLA owns playback


def bake_visible_motion(ctx, arm: bpy.types.Object) -> None:
    """Not used in normal builds. Kept for the proxy exporter, which needs the NLA
    result as plain keyframes so glTF can carry it."""
    scene = ctx.scene
    with bpy.context.temp_override(active_object=arm, selected_objects=[arm],
                                   scene=scene, object=arm):
        bpy.ops.nla.bake(frame_start=scene.frame_start, frame_end=scene.frame_end,
                         only_selected=False, visual_keying=True, clear_constraints=False,
                         use_current_action=False, bake_types={"POSE"})
