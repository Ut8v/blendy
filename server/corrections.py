"""Corrections log. Narrow scope, supersession not accumulation, hard budget,
narrow retrieval. Prefer fixing the schema, a validator message, or a profile;
write a correction only when the fix is genuinely a matter of taste.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any

from .db import Database

MAX_ENTRIES = 60
_SCOPE_MIN_TOKENS = 3      # "for animation" is not a scope; "mixamo characters in interiors" is
_BROAD = {"animation", "lighting", "camera", "scenes", "shots", "everything", "all", "general"}


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def validate_entry(entry: dict[str, Any]) -> list[str]:
    problems = []
    for key in ("scope", "situation", "wrong", "right"):
        if not isinstance(entry.get(key), str) or not entry[key].strip():
            problems.append(f"'{key}' is required")
    scope = _tokens(entry.get("scope", ""))
    if len(scope) < _SCOPE_MIN_TOKENS or scope <= _BROAD | {"for", "in", "the", "on"}:
        problems.append("scope is too broad; name the asset class, skeleton, shot type or "
                        "location it applies to")
    return problems


def add(db: Database, entry: dict[str, Any]) -> dict[str, Any]:
    problems = validate_entry(entry)
    if problems:
        raise ValueError("; ".join(problems))
    scope_t, sit_t = _tokens(entry["scope"]), _tokens(entry["situation"])
    supersedes = list(entry.get("supersedes", []))
    for old in db.query("SELECT id, scope, situation FROM corrections WHERE active=1"):
        if _tokens(old["scope"]) == scope_t and len(_tokens(old["situation"]) & sit_t) >= max(2, len(sit_t) // 2):
            supersedes.append(old["id"])          # same scope+situation: replace, never coexist
    rec = {"id": f"COR-{uuid.uuid4().hex[:8]}", "scope": entry["scope"].strip(),
           "situation": entry["situation"].strip(), "wrong": entry["wrong"].strip(),
           "right": entry["right"].strip(), "supersedes": sorted(set(supersedes)),
           "created": time.time()}
    with db.tx() as c:
        for sid in rec["supersedes"]:
            c.execute("UPDATE corrections SET active=0 WHERE id=?", (sid,))
        c.execute("""INSERT INTO corrections(id, scope, situation, wrong, right, supersedes, created)
                     VALUES (?,?,?,?,?,?,?)""",
                  (rec["id"], rec["scope"], rec["situation"], rec["wrong"], rec["right"],
                   json.dumps(rec["supersedes"]), rec["created"]))
    prune(db)
    return rec


def prune(db: Database) -> list[str]:
    """Over budget: drop never-retrieved entries oldest first, then least-hit."""
    active = db.query("SELECT id, hits, created FROM corrections WHERE active=1 "
                      "ORDER BY hits ASC, created ASC")
    excess = len(active) - MAX_ENTRIES
    dropped = []
    for row in active[:max(0, excess)]:
        db.execute("UPDATE corrections SET active=0 WHERE id=?", (row["id"],))
        dropped.append(row["id"])
    return dropped


def retrieve(db: Database, scope_terms: list[str], situation: str, limit: int = 3
             ) -> list[dict[str, Any]]:
    """Filter by scope first, then rank within it by situation overlap. Never
    returns the whole log."""
    want = {t.lower() for t in scope_terms}
    sit = _tokens(situation)
    scored = []
    for row in db.query("SELECT * FROM corrections WHERE active=1"):
        scope = _tokens(row["scope"])
        if not want & scope:
            continue
        score = len(sit & _tokens(row["situation"])) + 0.5 * len(want & scope)
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda s: (-s[0], s[1]["created"]))
    out = [r for _, r in scored[:limit]]
    for r in out:
        db.execute("UPDATE corrections SET hits=hits+1, last_hit=? WHERE id=?", (time.time(), r["id"]))
    return out
