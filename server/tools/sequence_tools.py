"""Bible, breakdown, script, continuity."""

from __future__ import annotations

import json
import os
from typing import Any

from compiler.refs import ROOT
from compiler.validate import load_json
from compiler.validate_sequence import (validate_bible, validate_breakdown,
                                        validate_shot_inheritance)

from .. import script as script_mod
from ..state import get_state

BIBLE = ROOT / "sequence" / "bible.json"
BREAKDOWN = ROOT / "sequence" / "breakdown.json"
SHOTS = ROOT / "sequence" / "shots"
SCRIPTS = ROOT / "script"


def read_bible() -> dict[str, Any]:
    """Cast, locations, looks, style. Shots inherit these and may not override them."""
    if not BIBLE.exists():
        raise FileNotFoundError("sequence/bible.json does not exist yet; draft it from the script")
    return load_json(BIBLE)


def write_bible(bible: dict[str, Any]) -> dict[str, Any]:
    """Write the bible after validation. A human approves it before any breakdown."""
    result = validate_bible(bible)
    if result.ok:
        os.makedirs(BIBLE.parent, exist_ok=True)
        BIBLE.write_text(json.dumps(bible, indent=2) + "\n", encoding="utf-8")
    return {"written": result.ok, **result.to_dict()}


def read_breakdown() -> dict[str, Any]:
    """The shot list."""
    if not BREAKDOWN.exists():
        raise FileNotFoundError("sequence/breakdown.json does not exist yet")
    return load_json(BREAKDOWN)


def read_script(name: str | None = None) -> dict[str, Any]:
    """Parse a script in /script into scenes, beats (s01_b001) and lines (s01_l001)."""
    files = sorted(SCRIPTS.glob("*.fountain")) + sorted(SCRIPTS.glob("*.txt"))
    if name:
        files = [f for f in files if f.stem == name]
    if not files:
        raise FileNotFoundError("no script found in /script")
    return script_mod.parse_file(files[0]).to_dict()


def validate_breakdown_tool(breakdown: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate a breakdown (given, or the one on disk) against the bible and script."""
    bible = read_bible()
    bd = breakdown if breakdown is not None else read_breakdown()
    lines = None
    files = sorted(SCRIPTS.glob("*.fountain")) + sorted(SCRIPTS.glob("*.txt"))
    if files:
        lines = script_mod.parse_file(files[0]).line_ids()
    return validate_breakdown(bd, bible, lines).to_dict()


def write_breakdown(breakdown: dict[str, Any]) -> dict[str, Any]:
    """Write a proposed breakdown with approved=false. A human sets approved=true by hand."""
    breakdown = {**breakdown, "approved": False}
    result = validate_breakdown_tool(breakdown)
    if result["ok"]:
        BREAKDOWN.write_text(json.dumps(breakdown, indent=2) + "\n", encoding="utf-8")
    return {"written": result["ok"], **result}


def ingest_list(breakdown: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Every asset the breakdown needs, so ingest can fan out before shots are built."""
    bible = read_bible()
    bd = breakdown if breakdown is not None else read_breakdown()
    cast = {c["id"]: c for c in bible["cast"]}
    locs = {l["id"]: l for l in bible["locations"]}
    looks = {l["id"]: l for l in bible["looks"]}
    seen: dict[str, dict[str, Any]] = {}
    for shot in bd["shots"]:
        for cid in shot["cast"]:
            c = cast[cid]
            key = f"{c['asset']['source']}:{c['asset']['ref']}"
            seen.setdefault(key, {**c["asset"], "class": "character", "retarget_profile": c["retarget_profile"],
                                  "for": cid})
        for a in locs[shot["location"]]["assets"]:
            seen.setdefault(f"{a['source']}:{a['ref']}", {**a, "class": None, "for": shot["location"]})
        hdri = looks[shot["look"]].get("hdri")
        if hdri:
            seen.setdefault(f"{hdri['source']}:{hdri['ref']}#hdri", {**hdri, "class": "hdri", "for": shot["look"]})
    return [v for k, v in sorted(seen.items()) if v["source"] != "primitive"]


def read_shot(shot_id: str) -> dict[str, Any]:
    """One shot spec with its bible entries resolved alongside, and inheritance checked."""
    p = SHOTS / f"{shot_id}.json"
    if not p.exists():
        raise FileNotFoundError(f"no shot '{shot_id}'")
    spec = load_json(p)
    bible = read_bible()
    bd_shot = next((s for s in read_breakdown()["shots"] if s["id"] == shot_id), None) if BREAKDOWN.exists() else None
    seq = spec.get("sequence", {})
    resolved = {
        "location": next((l for l in bible["locations"] if l["id"] == seq.get("location")), None),
        "look": next((l for l in bible["looks"] if l["id"] == seq.get("look")), None),
        "cast": [next((c for c in bible["cast"] if c["id"] == m["cast_id"]), None) for m in seq.get("cast", [])],
        "style": bible["style"], "breakdown": bd_shot}
    return {"spec": spec, "resolved": resolved,
            "inheritance": validate_shot_inheritance(spec, bible, bd_shot).to_dict()}


def validate_continuity(scene_id: str | None = None) -> dict[str, Any]:
    """Boundary check across a scene's shots, from the shot specs on disk (not just the breakdown)."""
    from compiler.validate_sequence import continuity_issues
    bd = read_breakdown()
    order = [s for s in bd["shots"] if scene_id is None or s["scene_id"] == scene_id]
    shots = []
    for entry in order:
        p = SHOTS / f"{entry['id']}.json"
        if p.exists():
            seq = load_json(p).get("sequence", {})
            shots.append({**entry, "enters_with": seq.get("enters_with", entry["enters_with"]),
                          "exits_with": seq.get("exits_with", entry["exits_with"])})
        else:
            shots.append(entry)
    issues = continuity_issues(shots)
    return {"ok": not issues, "checked": [s["id"] for s in shots],
            "issues": [vars(i) for i in issues]}


def new_shot_from_breakdown(shot_id: str) -> dict[str, Any]:
    """Scaffold sequence/shots/<id>.json from the breakdown + bible + look preset, ready for blocking."""
    from ..presets import load_preset
    bible, bd = read_bible(), read_breakdown()
    if not bd.get("approved"):
        raise RuntimeError("breakdown is not approved; a human must set approved=true first")
    entry = next((s for s in bd["shots"] if s["id"] == shot_id), None)
    if entry is None:
        raise KeyError(f"no shot '{shot_id}' in the breakdown")
    look = next(l for l in bible["looks"] if l["id"] == entry["look"])
    loc = next(l for l in bible["locations"] if l["id"] == entry["location"])
    style = bible["style"]
    try:
        preset = load_preset("lighting", look["lighting_preset"])
        world, lights = preset["world"], preset["lights"]
    except FileNotFoundError:
        world, lights = {"hdri": look.get("hdri"), "strength": 1.0, "background_color": [0.2, 0.2, 0.22]}, []
    t = lambda loc_: {"location": list(loc_), "rotation_euler": [0, 0, 0], "scale": [1, 1, 1]}
    spec: dict[str, Any] = {
        "version": "1.1",
        "meta": {"name": shot_id, "fps": style["fps"], "frame_start": 1,
                 "frame_end": entry["duration_frames"], "seed": 1},
        "world": world, "assets": [], "rigs": [], "animation": [], "cameras": [
            {"id": "cam_main", "type": "PERSP", "transform": t((0, -5, 1.6)), "focal": style["lens_set"][0],
             "dof": None, "track_target": None, "keyframes": []}],
        "lights": lights,
        "render": {"engine": entry.get("render_engine", style["render_engine"]),
                   "samples": style.get("samples", 64), "resolution": style["resolution"],
                   "output": f"renders/{shot_id}", "camera": "cam_main", "film_transparent": False,
                   "file_format": "PNG", "world_lighting_preset": look["lighting_preset"]},
        "sequence": {"shot_id": shot_id, "scene_id": entry["scene_id"], "location": entry["location"],
                     "look": entry["look"], "cast": [], "dialogue": [],
                     "enters_with": entry["enters_with"], "exits_with": entry["exits_with"]}}
    for i, a in enumerate(loc["assets"]):
        spec["assets"].append({"id": f"{loc['id']}_{i}", "source": a["source"], "ref": a["ref"],
                               "transform": t((0, 0, loc["ground_plane"]))})
    marks = loc.get("blocking_marks", {})
    for cid in entry["cast"]:
        c = next(x for x in bible["cast"] if x["id"] == cid)
        pos = marks.get(entry["enters_with"]["positions"].get(cid, ""), (0, 0, loc["ground_plane"]))
        spec["assets"].append({"id": f"{cid}_mesh", "source": c["asset"]["source"], "ref": c["asset"]["ref"],
                               "transform": t(pos)})
        spec["rigs"].append({"id": cid, "asset_id": f"{cid}_mesh", "skeleton": c["skeleton"],
                             "retarget_profile": c["retarget_profile"]})
        spec["sequence"]["cast"].append({"cast_id": cid, "rig_id": cid})
    for line_id in entry["dialogue"]:
        speaker = next((c for c in entry["cast"]), None)
        spec["sequence"]["dialogue"].append({"line_id": line_id, "cast_id": speaker, "frame_start": 1})
    os.makedirs(SHOTS, exist_ok=True)
    p = SHOTS / f"{shot_id}.json"
    if p.exists():
        raise FileExistsError(f"{p} exists; open it instead of scaffolding over it")
    p.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    get_state().open(shot_id)
    return {"path": str(p), "shot_id": shot_id, "cast": entry["cast"],
            "note": "dialogue frame_start values are placeholders; set them from the audio"}
