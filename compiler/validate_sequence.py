"""Sequence-level validation. Plain Python, no bpy.

- bible and breakdown against their schemas, plus reference integrity
- shots inherit from the bible and may not redefine it (hard rule 12)
- continuity at shot boundaries: enters_with == previous exits_with (hard rule 13)
- dialogue shots are at least as long as their lines
- character scale matches the bible's scale_reference
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .issues import Issue, ValidationResult, pointer
from .validate import BIBLE_SCHEMA, SEQUENCE_SCHEMA, load_schema, schema_issues

ROOT = Path(__file__).resolve().parent.parent
PHONEME_DIR = ROOT / "audio" / "phonemes"
SCALE_TOLERANCE = 0.03      # 3 percent; scale drift is invisible alone, glaring in a cut


def _ids(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {i["id"]: i for i in items}


# --- bible ---------------------------------------------------------------------------

def validate_bible(bible: Any) -> ValidationResult:
    issues = schema_issues(bible, load_schema(BIBLE_SCHEMA))
    if issues:
        return ValidationResult(issues)
    locs = _ids(bible["locations"])
    seen: dict[str, str] = {}
    for section in ("cast", "locations", "looks"):
        for i, item in enumerate(bible[section]):
            if item["id"] in seen:
                issues.append(Issue("error", "sequence", "duplicate_id",
                                    f"'{item['id']}' already used in {seen[item['id']]}",
                                    pointer([section, i, "id"]), item["id"]))
            seen[item["id"]] = section
    for i, look in enumerate(bible["looks"]):
        if look["location_id"] not in locs:
            issues.append(Issue("error", "sequence", "unknown_ref",
                                f"look references location '{look['location_id']}' which does not exist",
                                pointer(["looks", i, "location_id"]), look["id"]))
    return ValidationResult(issues)


# --- breakdown ---------------------------------------------------------------------------

def validate_breakdown(breakdown: Any, bible: dict[str, Any],
                       script_line_ids: set[str] | None = None) -> ValidationResult:
    """Every shot must resolve against the bible. Silent invention of a character
    is the worst failure mode here, so unresolved names are errors."""
    issues = schema_issues(breakdown, load_schema(SEQUENCE_SCHEMA))
    if issues:
        return ValidationResult(issues)
    cast, locs, looks = _ids(bible["cast"]), _ids(bible["locations"]), _ids(bible["looks"])
    seen: set[str] = set()
    for i, shot in enumerate(breakdown["shots"]):
        sid, p = shot["id"], ["shots", i]
        if sid in seen:
            issues.append(Issue("error", "sequence", "duplicate_id", "shot id repeated",
                                pointer(p + ["id"]), sid))
        seen.add(sid)
        if not sid.startswith(shot["scene_id"] + "_"):
            issues.append(Issue("error", "sequence", "scene_mismatch",
                                f"shot id '{sid}' does not belong to scene '{shot['scene_id']}'",
                                pointer(p + ["id"]), sid))
        if shot["location"] not in locs:
            issues.append(Issue("error", "sequence", "unresolved_location",
                                f"location '{shot['location']}' is not in the bible "
                                f"(have: {', '.join(sorted(locs))})", pointer(p + ["location"]), sid))
        look = looks.get(shot["look"])
        if look is None:
            issues.append(Issue("error", "sequence", "unresolved_look",
                                f"look '{shot['look']}' is not in the bible "
                                f"(have: {', '.join(sorted(looks))})", pointer(p + ["look"]), sid))
        elif look["location_id"] != shot["location"]:
            issues.append(Issue("error", "sequence", "look_location_mismatch",
                                f"look '{shot['look']}' belongs to location '{look['location_id']}', "
                                f"shot is at '{shot['location']}'", pointer(p + ["look"]), sid))
        for c in shot["cast"]:
            if c not in cast:
                issues.append(Issue("error", "sequence", "unresolved_cast",
                                    f"cast '{c}' is not in the bible; add them deliberately, "
                                    f"never invent (have: {', '.join(sorted(cast))})",
                                    pointer(p + ["cast"]), sid))
        for who in shot["enters_with"]["positions"]:
            if who not in shot["cast"]:
                issues.append(Issue("error", "sequence", "position_for_absent_cast",
                                    f"enters_with positions '{who}' who is not in the shot",
                                    pointer(p + ["enters_with"]), sid))
        if script_line_ids is not None:
            for ref in shot["script_ref"] + shot["dialogue"]:
                if ref not in script_line_ids:
                    issues.append(Issue("error", "sequence", "unknown_script_ref",
                                        f"'{ref}' is not a line id in the script",
                                        pointer(p + ["script_ref"]), sid))
        issues.extend(_dialogue_duration_issues(shot, bible["style"]["fps"], p))
    issues.extend(continuity_issues(breakdown["shots"]))
    return ValidationResult(issues)


def _dialogue_duration_issues(shot, fps, p) -> list[Issue]:
    """Shot duration follows dialogue duration, never the reverse."""
    total = 0.0
    for line_id in shot["dialogue"]:
        cue_path = PHONEME_DIR / f"{line_id}.json"
        if cue_path.exists():
            with open(cue_path, "r", encoding="utf-8") as fh:
                cues = json.load(fh).get("mouthCues", [])
            if cues:
                total += cues[-1]["end"]
    need = int(round(total * fps))
    if need and shot["duration_frames"] < need:
        return [Issue("error", "sequence", "shot_shorter_than_dialogue",
                      f"duration_frames {shot['duration_frames']} is shorter than its dialogue "
                      f"({need} frames at {fps} fps)", pointer(p + ["duration_frames"]), shot["id"])]
    return []


# --- continuity (hard rule 13) -----------------------------------------------------------------

def continuity_issues(shots: list[dict[str, Any]]) -> list[Issue]:
    issues = []
    prev = None
    for i, shot in enumerate(shots):
        if prev is not None and prev["scene_id"] == shot["scene_id"] and not shot.get("cut_before"):
            diff = _state_diff(prev["exits_with"], shot["enters_with"])
            if diff:
                issues.append(Issue("error", "continuity", "boundary_mismatch",
                                    f"enters_with does not match '{prev['id']}'.exits_with: "
                                    + "; ".join(diff), pointer(["shots", i, "enters_with"]), shot["id"]))
        prev = shot
    return issues


def _state_diff(exit_state, enter_state) -> list[str]:
    out = []
    for section in ("positions", "props", "world"):
        a, b = exit_state.get(section, {}), enter_state.get(section, {})
        for key in sorted(set(a) | set(b)):
            if a.get(key) != b.get(key):
                out.append(f"{section}.{key}: {json.dumps(a.get(key))} -> {json.dumps(b.get(key))}")
    return out


# --- shot inherits from bible (hard rule 12) -----------------------------------------------

def validate_shot_inheritance(shot: dict[str, Any], bible: dict[str, Any],
                              breakdown_shot: dict[str, Any] | None = None,
                              measured_heights: dict[str, float] | None = None) -> ValidationResult:
    issues: list[Issue] = []
    seq = shot.get("sequence")
    if not seq:
        return ValidationResult(issues)
    sid = seq["shot_id"]
    style = bible["style"]
    cast, locs, looks = _ids(bible["cast"]), _ids(bible["locations"]), _ids(bible["looks"])
    assets = _ids(shot["assets"])
    rigs = _ids(shot["rigs"])

    def err(code, msg, path):
        issues.append(Issue("error", "sequence", code, msg, path, sid))

    if shot["meta"]["fps"] != style["fps"]:
        err("redefines_style", f"meta.fps {shot['meta']['fps']} != bible {style['fps']}", "/meta/fps")
    if list(shot["render"]["resolution"]) != list(style["resolution"]):
        err("redefines_style", "render.resolution differs from the bible", "/render/resolution")
    engine_ok = {style["render_engine"], "BLENDER_WORKBENCH"}
    if breakdown_shot and breakdown_shot.get("render_engine"):
        engine_ok.add(breakdown_shot["render_engine"])
    if shot["render"]["engine"] not in engine_ok:
        err("redefines_style", f"render.engine {shot['render']['engine']} is not the bible's "
            f"{style['render_engine']} (per-shot override belongs in the breakdown)", "/render/engine")

    for i, cam in enumerate(shot["cameras"]):
        focals = [cam["focal"]] if cam["type"] == "PERSP" else []
        focals += [k["focal"] for k in (cam.get("move") or {}).get("keys", [])]
        focals += [k["value"] for k in cam.get("keyframes", []) if k["channel"] == "focal"]
        for f in focals:
            if all(abs(f - allowed) > 1e-6 for allowed in style["lens_set"]):
                err("focal_not_in_lens_set", f"camera '{cam['id']}' focal {f} is not in the bible "
                    f"lens_set {style['lens_set']}", pointer(["cameras", i, "focal"]))

    look = looks.get(seq["look"])
    if look is None or seq["location"] not in locs:
        err("unresolved_ref", "sequence.look / location are not in the bible", "/sequence")
        return ValidationResult(issues)
    if look["location_id"] != seq["location"]:
        err("look_location_mismatch", f"look '{look['id']}' belongs to '{look['location_id']}'",
            "/sequence/look")
    if look.get("hdri") is not None and shot["world"]["hdri"] != look["hdri"]:
        err("redefines_look", "world.hdri differs from the look's hdri; the look owns it", "/world/hdri")
    if shot["render"].get("world_lighting_preset") not in (None, look["lighting_preset"]):
        err("redefines_look", "render.world_lighting_preset is not the look's lighting_preset",
            "/render/world_lighting_preset")

    loc_assets = {(a["source"], a["ref"]) for a in locs[seq["location"]]["assets"]}
    have = {(a["source"], a["ref"]) for a in shot["assets"]}
    for missing in sorted(loc_assets - have):
        err("location_asset_missing", f"location '{seq['location']}' requires asset "
            f"{missing[0]}:{missing[1]}", "/assets")

    for i, member in enumerate(seq["cast"]):
        entry = cast.get(member["cast_id"])
        p = pointer(["sequence", "cast", i])
        if entry is None:
            err("unresolved_cast", f"'{member['cast_id']}' is not in the bible cast", p)
            continue
        rig = rigs.get(member["rig_id"])
        if rig is None:
            continue
        asset = assets.get(rig["asset_id"])
        if asset and (asset["source"], asset["ref"]) != (entry["asset"]["source"], entry["asset"]["ref"]):
            err("recast", f"'{member['cast_id']}' must use bible asset "
                f"{entry['asset']['source']}:{entry['asset']['ref']}, shot uses "
                f"{asset['source']}:{asset['ref']}", p)
        if rig["skeleton"] != entry["skeleton"] or rig["retarget_profile"] != entry["retarget_profile"]:
            err("redefines_cast", f"rig '{rig['id']}' skeleton/profile differ from the bible", p)
        if measured_heights and member["cast_id"] in measured_heights:
            h, want = measured_heights[member["cast_id"]], entry["scale_reference"]
            if abs(h - want) / want > SCALE_TOLERANCE:
                err("scale_drift", f"'{member['cast_id']}' compiles to {h:.3f}m, bible says {want}m", p)

    if breakdown_shot:
        dur = shot["meta"]["frame_end"] - shot["meta"]["frame_start"] + 1
        if dur != breakdown_shot["duration_frames"]:
            err("duration_mismatch", f"shot is {dur} frames, breakdown says "
                f"{breakdown_shot['duration_frames']}", "/meta")
        for state in ("enters_with", "exits_with"):
            if seq[state] != breakdown_shot[state]:
                err("continuity_redefined", f"sequence.{state} differs from the breakdown", f"/sequence/{state}")
    return ValidationResult(issues)
