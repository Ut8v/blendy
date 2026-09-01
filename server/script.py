"""Source script parser. Human-authored scripts in /script, never machine-edited.

Accepts a Fountain-style plain text screenplay:

    INT. KITCHEN - MORNING            scene heading  -> scene s01, s02, ...
    Action lines in sentence case.    action         -> beat  s01_b001
    MAYA                              character cue
    I told you not to come back.      dialogue       -> line  s01_l001 (speaker MAYA)
    (softly)                          parenthetical, attached to the next dialogue

Every beat and line gets a stable id the breakdown references. Ids are assigned
in document order and never re-used, so editing the script re-numbers only
what follows the edit.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_HEADING = re.compile(r"^\s*(INT\.|EXT\.|INT\./EXT\.|EST\.)\s*(.+?)(?:\s+-\s+(.+))?\s*$", re.I)
_CUE = re.compile(r"^\s*([A-Z][A-Z0-9 .'\-]{0,40}?)(\s*\(.+\))?\s*$")
_PAREN = re.compile(r"^\s*\(.+\)\s*$")


@dataclass
class Line:
    id: str
    kind: str                     # "action" | "dialogue"
    text: str
    scene_id: str
    speaker: str | None = None
    parenthetical: str | None = None


@dataclass
class Scene:
    id: str
    heading: str
    location: str
    time_of_day: str | None
    interior: bool
    lines: list[Line] = field(default_factory=list)


@dataclass
class Script:
    title: str
    scenes: list[Scene]

    def line_ids(self) -> set[str]:
        return {l.id for s in self.scenes for l in s.lines}

    def characters(self) -> list[str]:
        seen: dict[str, None] = {}
        for s in self.scenes:
            for l in s.lines:
                if l.speaker:
                    seen.setdefault(l.speaker, None)
        return list(seen)

    def locations(self) -> list[str]:
        seen: dict[str, None] = {}
        for s in self.scenes:
            seen.setdefault(s.location, None)
        return list(seen)

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "scenes": [asdict(s) for s in self.scenes]}


def parse(text: str, title: str = "untitled") -> Script:
    scenes: list[Scene] = []
    current: Scene | None = None
    pending_speaker: str | None = None
    pending_paren: str | None = None
    n_beat = n_line = 0

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            pending_speaker = pending_paren = None
            continue
        if line.lstrip().startswith("#") or line.lstrip().startswith("Title:"):
            if line.lstrip().startswith("Title:"):
                title = line.split(":", 1)[1].strip() or title
            continue

        m = _HEADING.match(line)
        if m:
            sid = f"s{len(scenes) + 1:02d}"
            current = Scene(sid, line.strip(), m.group(2).strip(), (m.group(3) or "").strip() or None,
                            m.group(1).upper().startswith("INT"))
            scenes.append(current)
            n_beat = n_line = 0
            pending_speaker = pending_paren = None
            continue
        if current is None:
            current = Scene("s01", "UNTITLED", "unknown", None, True)
            scenes.append(current)

        if pending_speaker and _PAREN.match(line):
            pending_paren = line.strip()[1:-1]
            continue
        if pending_speaker:
            n_line += 1
            current.lines.append(Line(f"{current.id}_l{n_line:03d}", "dialogue", line.strip(),
                                      current.id, pending_speaker, pending_paren))
            pending_paren = None
            continue
        cue = _CUE.match(line)
        if cue and line.strip() == line.strip().upper() and len(line.strip()) > 1 \
                and not line.strip().endswith("."):
            pending_speaker = cue.group(1).strip()
            pending_paren = cue.group(2).strip()[1:-1] if cue.group(2) else None
            continue
        n_beat += 1
        current.lines.append(Line(f"{current.id}_b{n_beat:03d}", "action", line.strip(), current.id))
    return Script(title, scenes)


def parse_file(path: Path | str) -> Script:
    p = Path(path)
    with open(p, "r", encoding="utf-8") as fh:
        return parse(fh.read(), title=p.stem)


def normalize_name(name: str) -> str:
    """Script name -> bible id form: 'MAYA' -> 'maya', "OLD MAN" -> 'old_man'."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
