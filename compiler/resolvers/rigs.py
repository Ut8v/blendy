"""Rig binding: find the armature an asset imported with, attach the retarget
profile, and compute the character's landmark table (bone + end + offset) from
the skeleton profile so it can be stored at ingest and used at scene time.
"""

from __future__ import annotations

import json
from typing import Any

import bpy
from mathutils import Vector

from ..refs import ROOT

SKELETON_DIR = ROOT / "profiles" / "skeletons"


def load_skeleton_profile(name: str) -> dict[str, Any]:
    path = SKELETON_DIR / f"{name}.json"
    if not path.exists():
        have = ", ".join(sorted(p.stem for p in SKELETON_DIR.glob("*.json"))) or "none"
        raise RuntimeError(f"retarget profile '{name}' not found in profiles/skeletons "
                           f"(have: {have})")
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def find_armature(objects: list[bpy.types.Object]) -> bpy.types.Object | None:
    arms = [o for o in objects if o.type == "ARMATURE"]
    return arms[0] if arms else None


def bind_rig(ctx, rig: dict[str, Any]) -> bpy.types.Object:
    """Locate the asset's armature and register it under the rig id. Binding
    itself (skin weights) came with the asset; auto-rigging happens at ingest."""
    rid, aid = rig["id"], rig["asset_id"]
    arm = ctx.armatures.get(f"asset:{aid}")
    if arm is None:
        raise RuntimeError(f"rig '{rid}': asset '{aid}' imported without an armature. "
                           "Auto-rig it at ingest (Mixamo / Auto-Rig Pro) before binding.")
    profile = load_skeleton_profile(rig["retarget_profile"])
    if profile.get("skeleton") != rig["skeleton"]:
        raise RuntimeError(f"rig '{rid}': retarget profile '{rig['retarget_profile']}' is for "
                           f"skeleton '{profile.get('skeleton')}', spec says '{rig['skeleton']}'")
    missing = [b for b in _required_bones(profile) if b not in arm.data.bones]
    if missing:
        raise RuntimeError(f"rig '{rid}': armature lacks bones the '{rig['skeleton']}' profile "
                           f"needs: {', '.join(missing[:8])}")
    arm["blendy_rig_id"] = rid
    arm["blendy_skeleton"] = rig["skeleton"]
    ctx.armatures[rid] = arm
    ctx.register(rid, arm)
    return arm


def _required_bones(profile: dict[str, Any]) -> list[str]:
    names = {profile.get("root_bone")}
    for entry in profile.get("landmarks", {}).values():
        if "fallback" in entry:
            names.add(entry["fallback"]["bone"])
        else:
            names.add(entry["bone"])
    return sorted(n for n in names if n)


# --- ingest-time: character landmark table -----------------------------------------

def character_landmarks(arm: bpy.types.Object, profile: dict[str, Any],
                        ground_z: float = 0.0) -> dict[str, dict[str, Any]]:
    """Turn the skeleton profile's recipe into concrete per-bone entries for one
    armature. Offsets are in bone-local space (Y along the bone) and are scaled
    by the head bone length so the same recipe fits characters of any size."""
    out: dict[str, dict[str, Any]] = {}
    bones = arm.data.bones
    for name, recipe in profile.get("landmarks", {}).items():
        bone_name, end = recipe["bone"], recipe.get("end", "head")
        frac = recipe.get("offset_frac")
        if bone_name not in bones and "fallback" in recipe:
            fb = recipe["fallback"]
            bone_name, end, frac = fb["bone"], fb.get("end", "head"), fb.get("offset_frac")
        if bone_name not in bones:
            raise RuntimeError(f"cannot map core landmark '{name}': no bone '{bone_name}'")
        entry: dict[str, Any] = {"kind": "bone", "bone": bone_name, "end": end}
        if frac:
            ref_len = bones[bone_name].length or 0.1
            entry["offset"] = [frac[0] * ref_len, frac[1] * ref_len, frac[2] * ref_len]
        if recipe.get("project_to_ground"):
            # Ground contact sits under the hips at the floor: offset the bone-local
            # position down by the hip height in rest pose.
            head_world = arm.matrix_world @ bones[bone_name].head_local
            entry["offset"] = [0.0, 0.0, 0.0]
            entry["world_z_override"] = ground_z
            entry["rest_height"] = float(head_world.z - ground_z)
        out[name] = entry
    return out


def character_height(arm: bpy.types.Object, meshes: list[bpy.types.Object]) -> float:
    zs = []
    for obj in meshes:
        for corner in obj.bound_box:
            zs.append((obj.matrix_world @ Vector(corner)).z)
    if not zs:
        return float(arm.dimensions.z)
    return float(max(zs) - min(zs))
