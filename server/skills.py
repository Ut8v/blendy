"""Skill files: partitioned core / learned (hard rule 10).

Everything above `<!-- LEARNED -->` is human-authored and immutable to every
agent. Only the section below it may change, it is capped, and every line must
cite the incidents that produced it. This module is the only writer.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "skills"
MARKER = "<!-- LEARNED -->"
MAX_LEARNED_LINES = 40
_LINE = re.compile(r"^- \[(INC-\d{4}(?:,\s*INC-\d{4})*)\]\s+\S.+$")


class SkillError(ValueError):
    pass


def skill_path(name: str) -> Path:
    return SKILL_DIR / f"{name}.md"


def split(text: str) -> tuple[str, str]:
    if MARKER not in text:
        raise SkillError(f"skill file has no {MARKER} marker; refusing to write")
    core, learned = text.split(MARKER, 1)
    return core + MARKER, learned


def read(name: str) -> dict[str, Any]:
    p = skill_path(name)
    if not p.exists():
        raise FileNotFoundError(f"no skill '{name}'")
    text = p.read_text(encoding="utf-8")
    core, learned = split(text)
    return {"name": name, "core": core, "learned": learned_lines(learned), "path": str(p)}


def learned_lines(section: str) -> list[str]:
    return [l for l in section.splitlines() if l.startswith("- ")]


def validate_learned(lines: list[str]) -> list[str]:
    problems = []
    if len(lines) > MAX_LEARNED_LINES:
        problems.append(f"{len(lines)} learned lines exceeds the cap of {MAX_LEARNED_LINES}; "
                        "consolidate or delete before adding")
    for i, l in enumerate(lines):
        if not _LINE.match(l):
            problems.append(f"line {i + 1} must look like '- [INC-0001, INC-0002] <pattern>': {l[:60]!r}")
    return problems


def write_learned(name: str, lines: list[str], expected_core: str | None = None) -> Path:
    """Replace the learned section. The core is compared byte-for-byte to what the
    caller read, so a stale or tampered core can never be written back."""
    p = skill_path(name)
    text = p.read_text(encoding="utf-8")
    core, _ = split(text)
    if expected_core is not None and expected_core != core:
        raise SkillError("core section changed on disk; re-read before writing")
    problems = validate_learned(lines)
    if problems:
        raise SkillError("; ".join(problems))
    header = ("\n<!-- Orchestrator-writable. Max 40 lines. Every line carries incident refs. -->\n\n")
    body = "\n".join(lines) + ("\n" if lines else "")
    p.write_text(core + header + body, encoding="utf-8")
    return p


def diff_learned(name: str, new_lines: list[str]) -> str:
    import difflib
    old = read(name)["learned"]
    return "".join(difflib.unified_diff(
        [l + "\n" for l in old], [l + "\n" for l in new_lines],
        fromfile=f"skills/{name}.md (learned)", tofile=f"skills/{name}.md (proposed)"))


def check_all() -> dict[str, list[str]]:
    """CI: every skill has a marker and a valid learned section."""
    report = {}
    for p in sorted(SKILL_DIR.glob("*.md")):
        try:
            _, learned = split(p.read_text(encoding="utf-8"))
            report[p.stem] = validate_learned(learned_lines(learned))
        except SkillError as e:
            report[p.stem] = [str(e)]
    return report
