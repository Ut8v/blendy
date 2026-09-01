"""Dialogue audio and lip sync. Audio goes in the sequencer; phoneme cues drive
the character's viseme shape keys as additive keyframes on the face only.

Phoneme files are Rhubarb output: {"mouthCues": [{"start", "end", "value"}]}.
"""

from __future__ import annotations

import json
import os
from typing import Any

import bpy

from ..refs import ROOT

PHONEME_DIR = ROOT / "audio" / "phonemes"
AUDIO_DIR = ROOT / "audio" / "dialogue"


def _strips(scene: bpy.types.Scene):
    if scene.sequence_editor is None:
        scene.sequence_editor_create()
    se = scene.sequence_editor
    return se.strips if hasattr(se, "strips") else se.sequences    # 4.4 rename


def add_audio(ctx, entry: dict[str, Any]) -> None:
    path = entry["path"] if os.path.isabs(entry["path"]) else str(ROOT / entry["path"])
    if not os.path.exists(path):
        raise RuntimeError(f"audio '{entry['id']}': file not found {path}")
    strips = _strips(ctx.scene)
    channel = 1 + sum(1 for s in strips if s.type == "SOUND")
    strip = strips.new_sound(entry["id"], path, channel, entry["frame_start"])
    strip.volume = entry.get("volume", 1.0)


def _viseme_keys(mesh_obj: bpy.types.Object, mapping: dict[str, str | None]) -> dict[str, Any]:
    if not mesh_obj.data.shape_keys:
        return {}
    blocks = mesh_obj.data.shape_keys.key_blocks
    return {code: blocks[name] for code, name in mapping.items() if name and name in blocks}


def apply_dialogue_line(ctx, line: dict[str, Any], cast_rig_id: str) -> None:
    """Keyframe visemes for one dialogue line on the rig's face mesh."""
    cue_path = PHONEME_DIR / f"{line['line_id']}.json"
    if not cue_path.exists():
        raise RuntimeError(f"dialogue line '{line['line_id']}': no phoneme file at {cue_path}; "
                           "run extract_phonemes first (audio comes before animation)")
    with open(cue_path, "r", encoding="utf-8") as fh:
        cues = json.load(fh)["mouthCues"]

    rig_spec = next(r for r in ctx.spec["rigs"] if r["id"] == cast_rig_id)
    asset_id = rig_spec["asset_id"]
    profile = (ctx.resolved.get(asset_id, {}).get("profile")
               or ctx.profiles.profile_for(next(a for a in ctx.spec["assets"] if a["id"] == asset_id)))
    mapping = (profile or {}).get("visemes")
    if not mapping:
        raise RuntimeError(f"rig '{cast_rig_id}': asset profile has no viseme mapping; "
                           "re-ingest with a face that has viseme shape keys")
    face = None
    for obj in ctx.meshes.get(asset_id, []):
        if obj.data.shape_keys:
            face = obj
            break
    if face is None:
        raise RuntimeError(f"rig '{cast_rig_id}': no mesh with shape keys to drive")
    keys = _viseme_keys(face, mapping)
    if not keys:
        raise RuntimeError(f"rig '{cast_rig_id}': none of the mapped viseme shape keys exist")

    fps = ctx.spec["meta"]["fps"]
    f0 = line["frame_start"]
    lead = 1   # one frame of anticipation reads better than a hard switch
    for cue in cues:
        start = f0 + int(round(cue["start"] * fps))
        end = f0 + int(round(cue["end"] * fps))
        active = keys.get(cue["value"])
        for code, kb in keys.items():
            on = 1.0 if kb == active else 0.0
            kb.value = on
            kb.keyframe_insert("value", frame=max(f0, start - lead))
            kb.keyframe_insert("value", frame=start)
            if end > start:
                kb.value = on
                kb.keyframe_insert("value", frame=end - lead if end - lead > start else end)
    for kb in keys.values():
        kb.value = 0.0


def dialogue_end_frame(lines: list[dict[str, Any]], fps: float) -> int:
    """Last frame any line is still speaking. Shot duration must not undercut it."""
    last = 0
    for line in lines:
        cue_path = PHONEME_DIR / f"{line['line_id']}.json"
        if not cue_path.exists():
            continue
        with open(cue_path, "r", encoding="utf-8") as fh:
            cues = json.load(fh)["mouthCues"]
        if cues:
            last = max(last, line["frame_start"] + int(round(cues[-1]["end"] * fps)))
    return last
