"""Presets: validated spec fragments promoted from accepted shots. The second
compounding layer. A preset is patched into a spec by id, and because it was a
valid fragment it cannot introduce an invalid state on its own.

profiles/presets/<kind>/<name>.json:
  { "kind", "name", "description", "tags", "shot_types", "register", ...fragment }
kinds: lighting (world + lights), camera (one camera's move), material, framing
"""

from __future__ import annotations

import copy
import json
import os
import re
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PRESET_DIR = ROOT / "profiles" / "presets"
KINDS = ("lighting", "camera", "material", "framing", "layout")
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def preset_path(kind: str, name: str) -> Path:
    return PRESET_DIR / kind / f"{name}.json"


def list_presets(kind: str | None = None) -> list[dict[str, Any]]:
    out = []
    for k in (KINDS if kind is None else [kind]):
        d = PRESET_DIR / k
        if not d.exists():
            continue
        for p in sorted(d.glob("*.json")):
            with open(p, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            out.append({"kind": k, "name": p.stem, "description": data.get("description", ""),
                        "tags": data.get("tags", []), "shot_types": data.get("shot_types", []),
                        "register": data.get("register")})
    return out


def load_preset(kind: str, name: str) -> dict[str, Any]:
    p = preset_path(kind, name)
    if not p.exists():
        have = ", ".join(x["name"] for x in list_presets(kind)) or "none"
        raise FileNotFoundError(f"no {kind} preset '{name}' (have: {have})")
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_preset(kind: str, name: str, data: dict[str, Any]) -> Path:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if not _NAME.match(name):
        raise ValueError("preset name must be lowercase snake_case")
    data = {**data, "kind": kind, "name": name, "created": data.get("created", time.time())}
    p = preset_path(kind, name)
    os.makedirs(p.parent, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    return p


# --- promote: spec -> fragment ------------------------------------------------------

def promote_lighting(spec: dict[str, Any], name: str, description: str, tags=()) -> Path:
    return save_preset("lighting", name, {
        "description": description, "tags": list(tags),
        "world": copy.deepcopy(spec["world"]), "lights": copy.deepcopy(spec["lights"])})


def promote_camera(spec: dict[str, Any], camera_id: str, name: str, description: str,
                   shot_types=(), register: str | None = None, tags=()) -> Path:
    cam = next((c for c in spec["cameras"] if c["id"] == camera_id), None)
    if cam is None:
        raise KeyError(f"no camera '{camera_id}'")
    if not cam.get("move"):
        raise ValueError("only landmark-anchored moves are promotable; world-space cameras "
                         "break when the character is repositioned (hard rule 15)")
    move = copy.deepcopy(cam["move"])
    # Generalize: strip the asset id so the move applies to any character.
    for key in move["keys"]:
        if isinstance(key["target"], str) and key["target"].startswith("@"):
            key["target"] = "@{subject}." + key["target"].split(".", 1)[1]
    return save_preset("camera", name, {
        "description": description, "tags": list(tags), "shot_types": list(shot_types),
        "register": register, "type": cam["type"], "dof": copy.deepcopy(cam.get("dof")),
        "move": move})


def promote_material(spec: dict[str, Any], asset_id: str, name: str, description: str) -> Path:
    asset = next((a for a in spec["assets"] if a["id"] == asset_id), None)
    if asset is None or not asset.get("material_overrides"):
        raise KeyError(f"asset '{asset_id}' has no material_overrides to promote")
    return save_preset("material", name, {"description": description,
                                          "material_overrides": copy.deepcopy(asset["material_overrides"])})


# --- apply: fragment -> JSON Patch operations -------------------------------------------

def apply_operations(kind: str, name: str, target_ids: dict[str, str] | None = None,
                     spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Turn a preset into patch operations for patch_spec. target_ids maps the
    preset's placeholders (subject, camera, asset, ...) to entity ids in the spec."""
    preset = load_preset(kind, name)
    t = target_ids or {}
    if kind == "lighting":
        ops = [{"op": "replace", "path": "/world", "value": preset["world"]},
               {"op": "replace", "path": "/lights", "value": preset["lights"]}]
        if spec is not None:
            ops.append({"op": "replace", "path": "/render/world_lighting_preset", "value": name}
                       if "world_lighting_preset" in spec["render"] else
                       {"op": "add", "path": "/render/world_lighting_preset", "value": name})
        return ops
    if kind == "camera":
        cam_id, subject = t.get("camera", "cam_main"), t.get("subject")
        if subject is None:
            raise ValueError("camera presets need target_ids['subject'] = the asset id to frame")
        move = copy.deepcopy(preset["move"])
        move["preset"] = name
        for key in move["keys"]:
            if isinstance(key["target"], str):
                key["target"] = key["target"].replace("{subject}", subject)
        base = f"/cameras/id={cam_id}"
        ops = [{"op": "add", "path": f"{base}/move", "value": move},
               {"op": "add", "path": f"{base}/track_target", "value": None},
               {"op": "add", "path": f"{base}/keyframes", "value": []}]
        if preset.get("dof") is not None:
            dof = copy.deepcopy(preset["dof"])
            if isinstance(dof.get("focus_target"), str):
                dof["focus_target"] = dof["focus_target"].replace("{subject}", subject)
            ops.append({"op": "add", "path": f"{base}/dof", "value": dof})
        return ops
    if kind == "material":
        aid = t.get("asset")
        if aid is None:
            raise ValueError("material presets need target_ids['asset']")
        return [{"op": "add", "path": f"/assets/id={aid}/material_overrides",
                 "value": preset["material_overrides"]}]
    raise ValueError(f"cannot apply preset kind {kind!r}")
