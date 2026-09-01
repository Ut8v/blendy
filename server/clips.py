"""Motion clip library under /clips/<skeleton>/. Files are Mixamo exports or
video-derived mocap. list_clips(skeleton) is the agent's vocabulary of motion.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CLIP_DIR = ROOT / "clips"
EXTS = (".fbx", ".glb", ".gltf", ".blend")


def list_clips(skeleton: str) -> list[dict[str, Any]]:
    d = CLIP_DIR / skeleton
    if not d.exists():
        return []
    meta_path = d / "clips.json"
    meta = {}
    if meta_path.exists():
        with open(meta_path, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
    out = []
    for p in sorted(d.iterdir()):
        if p.suffix.lower() in EXTS:
            m = meta.get(p.stem, {})
            out.append({"clip": f"{skeleton}/{p.stem}", "file": str(p), "skeleton": skeleton,
                        "tags": m.get("tags", []), "loopable": m.get("loopable"),
                        "frames": m.get("frames"), "description": m.get("description", "")})
    return out


def find_clip(skeleton: str, name: str) -> dict[str, Any] | None:
    for c in list_clips(skeleton):
        if c["clip"] == name or c["clip"].split("/", 1)[1] == name:
            return c
    return None
