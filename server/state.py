"""Shared server state: the database, the active shot session, resolved-asset
cache. One process, one active spec, one writer at a time.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from compiler.refs import ProfileIndex

from .db import Database
from .session import Session

ROOT = Path(__file__).resolve().parent.parent
SCENES_DIR = ROOT / "spec" / "scenes"
SHOTS_DIR = ROOT / "sequence" / "shots"
BUILD_DIR = ROOT / "checkpoints" / "_build"


class State:
    def __init__(self, db_path: str | None = None):
        self.db = Database(db_path) if db_path else Database()
        self.session: Session | None = None
        self.resolved: dict[str, Any] = {}

    def reload_profiles(self) -> ProfileIndex:
        profiles = ProfileIndex.load()
        if self.session:
            self.session.profiles = profiles
        return profiles

    def open(self, name_or_path: str) -> Session:
        """Open a shot spec by shot id, scene name, or path."""
        p = Path(name_or_path)
        if not p.exists():
            for candidate in (SHOTS_DIR / f"{name_or_path}.json", SCENES_DIR / f"{name_or_path}.json"):
                if candidate.exists():
                    p = candidate
                    break
        if not p.exists():
            have = sorted(x.stem for x in list(SHOTS_DIR.glob("*.json")) + list(SCENES_DIR.glob("*.json")))
            raise FileNotFoundError(f"no spec '{name_or_path}' (have: {', '.join(have) or 'none'})")
        self.session = Session(p, profiles=ProfileIndex.load())
        self.resolved = {}
        return self.session

    def require(self) -> Session:
        if self.session is None:
            raise RuntimeError("no spec open; call open_spec(name) first")
        return self.session

    def build_blend_path(self) -> str:
        s = self.require()
        os.makedirs(BUILD_DIR, exist_ok=True)
        return str(BUILD_DIR / f"{s.name}.blend")


STATE: State | None = None


def get_state() -> State:
    global STATE
    if STATE is None:
        STATE = State()
    return STATE
