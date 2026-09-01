"""Incidents: the structured record of a failure. Written by the review agent,
read by the orchestrator. The shape is enforced; prose is rejected.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .db import Database

ROOT = Path(__file__).resolve().parent.parent
INCIDENT_DIR = ROOT / "incidents"
CATEGORIES = ("schema", "tool", "judgment")
AGENTS = ("ingest", "blocking", "animation", "camera", "lighting", "breakdown", "review", "human")


def validate_incident(inc: dict[str, Any]) -> list[str]:
    problems = []
    for key in ("shot", "agent", "expected", "observed"):
        if not isinstance(inc.get(key), str) or not inc[key].strip():
            problems.append(f"'{key}' is required and must be a non-empty string")
    if inc.get("agent") not in AGENTS:
        problems.append(f"agent must be one of {AGENTS}")
    if inc.get("category") not in (None, *CATEGORIES):
        problems.append(f"category must be one of {CATEGORIES} or null until triaged")
    for key in ("expected", "observed"):
        if isinstance(inc.get(key), str) and len(inc[key]) > 400:
            problems.append(f"'{key}' is over 400 chars; this is a record, not prose")
    res = inc.get("resolution")
    if res is not None and not (isinstance(res, dict) and {"kind", "ref"} <= set(res)):
        problems.append("resolution must be null or {kind, ref}")
    return problems


def write_incident(db: Database, inc: dict[str, Any], session: str | None = None) -> dict[str, Any]:
    problems = validate_incident(inc)
    if problems:
        raise ValueError("; ".join(problems))
    record = {"id": db.next_incident_id(), "shot": inc["shot"], "agent": inc["agent"],
              "category": inc.get("category"), "expected": inc["expected"].strip(),
              "observed": inc["observed"].strip(), "resolution": inc.get("resolution"),
              "session": session, "created": time.time(), "previews": inc.get("previews", [])}
    db.insert_incident(record)
    os.makedirs(INCIDENT_DIR, exist_ok=True)
    with open(INCIDENT_DIR / f"{record['id']}.json", "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
    return record


def triage(db: Database, incident_id: str, category: str, note: str | None = None) -> None:
    """Human or orchestrator confirms the category. Schema and tool buckets are
    fixed structurally; only judgment reaches a skill."""
    if category not in CATEGORIES:
        raise ValueError(f"category must be one of {CATEGORIES}")
    db.execute("UPDATE incidents SET category=? WHERE id=?", (category, incident_id))
    p = INCIDENT_DIR / f"{incident_id}.json"
    if p.exists():
        with open(p, "r", encoding="utf-8") as fh:
            rec = json.load(fh)
        rec["category"] = category
        if note:
            rec["triage_note"] = note
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, indent=2)


def resolve(db: Database, incident_id: str, kind: str, ref: str) -> None:
    db.execute("UPDATE incidents SET resolution=? WHERE id=?",
               (json.dumps({"kind": kind, "ref": ref}), incident_id))


def list_incidents(db: Database, shot: str | None = None, agent: str | None = None,
                   open_only: bool = False) -> list[dict[str, Any]]:
    where, params = [], []
    if shot:
        where.append("shot=?"); params.append(shot)
    if agent:
        where.append("agent=?"); params.append(agent)
    if open_only:
        where.append("resolution IS NULL")
    sql = "SELECT * FROM incidents" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY created"
    return db.query(sql, tuple(params))
