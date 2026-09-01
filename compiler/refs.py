"""Landmark references and asset profiles. Pure Python, no bpy.

A landmark reference is "@<asset_id>.<landmark>". It is resolved against the
asset's profile, which was written once at ingest (hard rule 8). This module
knows how to parse refs and how to find the profile for a spec asset; it never
computes positions. The compiler does that, inside Blender, after rigs bind.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PROFILES_DIR = ROOT / "profiles" / "assets"
MANIFEST_PATH = ROOT / "assets" / "manifest.json"

PROFILE_VERSION = 1

_REF_RE = re.compile(r"^@([a-z][a-z0-9_]*)\.([a-z][a-z0-9_]*)$")

# Every character profile must map all of these (CLAUDE.md, landmarks on characters).
CHARACTER_CORE_LANDMARKS = (
    "eye_L", "eye_R", "eye_midpoint", "ear_L", "ear_R", "head_top", "chin", "neck",
    "shoulder_L", "shoulder_R", "hand_L", "hand_R", "hip", "foot_L", "foot_R",
    "ground_contact",
)

# Primitives are blocking stand-ins. They get a fixed socket vocabulary in local
# unit space so a blockout can say "@hero_block.top" before any asset exists.
_UNIT_BOX = {
    "origin": ([0, 0, 0], [0, 0, 1]),
    "center": ([0, 0, 0], [0, 0, 1]),
    "top": ([0, 0, 1], [0, 0, 1]),
    "bottom": ([0, 0, -1], [0, 0, -1]),
    "front": ([0, -1, 0], [0, -1, 0]),
    "back": ([0, 1, 0], [0, 1, 0]),
    "left": ([-1, 0, 0], [-1, 0, 0]),
    "right": ([1, 0, 0], [1, 0, 0]),
}
PRIMITIVE_LANDMARKS: dict[str, dict[str, tuple[list, list]]] = {
    "cube": _UNIT_BOX,
    "uv_sphere": _UNIT_BOX,
    "ico_sphere": _UNIT_BOX,
    "cylinder": _UNIT_BOX,
    "cone": {**_UNIT_BOX, "top": ([0, 0, 1], [0, 0, 1])},
    "plane": {"origin": ([0, 0, 0], [0, 0, 1]), "center": ([0, 0, 0], [0, 0, 1]),
              "top": ([0, 0, 0], [0, 0, 1]), "front": ([0, -1, 0], [0, 0, 1]),
              "back": ([0, 1, 0], [0, 0, 1]), "left": ([-1, 0, 0], [0, 0, 1]),
              "right": ([1, 0, 0], [0, 0, 1])},
    "empty": {"origin": ([0, 0, 0], [0, 0, 1])},
}
PRIMITIVE_NAMES = tuple(PRIMITIVE_LANDMARKS)


@dataclass(frozen=True)
class LandmarkRef:
    asset_id: str
    landmark: str

    def __str__(self) -> str:
        return f"@{self.asset_id}.{self.landmark}"


def is_ref(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("@")


def parse_ref(value: str) -> LandmarkRef:
    m = _REF_RE.match(value)
    if not m:
        raise ValueError(f"malformed landmark reference {value!r}; expected @<asset_id>.<landmark>")
    return LandmarkRef(m.group(1), m.group(2))


def primitive_landmarks(ref: str) -> dict[str, dict[str, Any]]:
    """Profile-shaped landmark table for a primitive, or {} if unknown."""
    table = PRIMITIVE_LANDMARKS.get(ref, {})
    return {name: {"kind": "socket", "position": list(p), "normal": list(n)}
            for name, (p, n) in table.items()}


class ProfileIndex:
    """Finds the ingest profile for a spec asset entry.

    Profiles are keyed by content hash. The manifest maps (source, ref) to that
    hash. Primitives never go through ingest and get their fixed vocabulary here.
    """

    def __init__(self, manifest: dict[str, Any] | None = None,
                 profiles: dict[str, dict[str, Any]] | None = None):
        self._manifest = manifest if manifest is not None else {"assets": {}}
        self._profiles = profiles if profiles is not None else {}

    @classmethod
    def load(cls, manifest_path: Path = MANIFEST_PATH,
             profiles_dir: Path = PROFILES_DIR) -> "ProfileIndex":
        manifest = {"assets": {}}
        if manifest_path.exists():
            with open(manifest_path, "r", encoding="utf-8") as fh:
                manifest = json.load(fh)
        profiles: dict[str, dict[str, Any]] = {}
        if profiles_dir.exists():
            for p in sorted(profiles_dir.glob("*.json")):
                with open(p, "r", encoding="utf-8") as fh:
                    profiles[p.stem] = json.load(fh)
        return cls(manifest, profiles)

    @staticmethod
    def key(source: str, ref: str) -> str:
        return f"{source}:{ref}"

    def hash_for(self, source: str, ref: str) -> str | None:
        entry = self._manifest.get("assets", {}).get(self.key(source, ref))
        return entry.get("hash") if entry else None

    def profile_for(self, asset: dict[str, Any]) -> dict[str, Any] | None:
        """Profile dict for a spec asset entry, or None if it was never ingested."""
        source, ref = asset["source"], asset["ref"]
        if source == "primitive":
            return {"profile_version": PROFILE_VERSION, "class": "prop",
                    "landmarks": primitive_landmarks(ref), "flags": {"generated": False}}
        h = self.hash_for(source, ref)
        if h is None:
            return None
        return self._profiles.get(h)

    def landmarks_for(self, asset: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
        profile = self.profile_for(asset)
        if profile is None:
            return None
        return profile.get("landmarks", {})


def profile_path(content_hash: str, profiles_dir: Path = PROFILES_DIR) -> Path:
    return profiles_dir / f"{content_hash}.json"
