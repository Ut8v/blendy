"""Orchestrator, proposal mode. Watches incidents, finds recurring patterns,
and PROPOSES skill edits gated by the eval set. It applies nothing.

Triage order for every pattern (CLAUDE.md): 1 schema/validator, 2 tool,
3 judgment. Only bucket 3 may become a skill edit, and the proposal must carry
a written justification for why 1 and 2 do not apply.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from . import skills
from .db import Database

ROOT = Path(__file__).resolve().parent.parent
PROPOSAL_DIR = ROOT / "incidents" / "proposals"
MIN_DISTINCT_SHOTS = 3


def patterns(db: Database) -> list[dict[str, Any]]:
    """Recurring, untriaged-or-judgment patterns across >= 3 distinct shots."""
    out = []
    for row in db.recurring_patterns(MIN_DISTINCT_SHOTS):
        ids = row["ids"].split(",")
        incidents = db.query(f"SELECT * FROM incidents WHERE id IN ({','.join('?' * len(ids))})",
                             tuple(ids))
        out.append({"agent": row["agent"], "category": row["category"], "shots": row["shots"],
                    "sessions": row["sessions"], "incident_ids": ids,
                    "observed": [i["observed"] for i in incidents],
                    "expected": [i["expected"] for i in incidents]})
    return out


def propose(db: Database, agent: str, incident_ids: list[str], pattern: str,
            justification: dict[str, str], new_learned_lines: list[str] | None = None
            ) -> dict[str, Any]:
    """Write a proposal file. justification must explain, in writing, why the
    pattern is not a schema/validator fix (bucket 1) or a tool fix (bucket 2)."""
    for bucket in ("not_schema", "not_tool", "why_judgment"):
        if not justification.get(bucket, "").strip():
            raise ValueError(f"justification['{bucket}'] is required; unjustified skill edits are rejected")
    incidents = db.query(f"SELECT id, shot, session, category FROM incidents WHERE id IN "
                         f"({','.join('?' * len(incident_ids))})", tuple(incident_ids))
    shots = {i["shot"] for i in incidents}
    sessions = {i["session"] or i["id"] for i in incidents}
    if len(shots) < MIN_DISTINCT_SHOTS or len(sessions) < MIN_DISTINCT_SHOTS:
        raise ValueError(f"need incidents from >= {MIN_DISTINCT_SHOTS} distinct shots and sessions; "
                         f"have {len(shots)} shots, {len(sessions)} sessions")
    if any(i["category"] not in (None, "judgment") for i in incidents):
        raise ValueError("some incidents are triaged as schema/tool; fix those structurally instead")

    current = skills.read(agent)
    lines = list(current["learned"])
    refs = ", ".join(sorted(set(incident_ids)))
    new_line = f"- [{refs}] {pattern.strip()}"
    if new_learned_lines is None:
        new_learned_lines = lines + [new_line]
    problems = skills.validate_learned(new_learned_lines)
    if problems:
        raise ValueError("proposed learned section invalid: " + "; ".join(problems))

    proposal = {"id": f"PROP-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}", "agent": agent,
                "incident_ids": sorted(set(incident_ids)), "pattern": pattern.strip(),
                "justification": justification, "learned_before": lines,
                "learned_after": new_learned_lines,
                "diff": skills.diff_learned(agent, new_learned_lines), "core_hash": hash(current["core"]),
                "status": "proposed", "eval_run": None, "created": time.time()}
    os.makedirs(PROPOSAL_DIR, exist_ok=True)
    with open(PROPOSAL_DIR / f"{proposal['id']}.json", "w", encoding="utf-8") as fh:
        json.dump(proposal, fh, indent=2)
    return proposal


def load_proposal(proposal_id: str) -> dict[str, Any]:
    p = PROPOSAL_DIR / f"{proposal_id}.json"
    if not p.exists():
        raise FileNotFoundError(f"no proposal '{proposal_id}'")
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _save(proposal: dict[str, Any]) -> None:
    with open(PROPOSAL_DIR / f"{proposal['id']}.json", "w", encoding="utf-8") as fh:
        json.dump(proposal, fh, indent=2)


def evaluate(db: Database, proposal_id: str, runner) -> dict[str, Any]:
    """Apply the edit to the skill file TEMPORARILY, run the eval set through
    `runner(skill_diff) -> run record`, restore the file, record the verdict.
    Lands only on no regression, and even then a human applies it."""
    proposal = load_proposal(proposal_id)
    agent = proposal["agent"]
    before = skills.read(agent)
    try:
        skills.write_learned(agent, proposal["learned_after"], expected_core=before["core"])
        run = runner(proposal["diff"])
    finally:
        skills.write_learned(agent, before["learned"])
    proposal["eval_run"] = run.get("id")
    proposal["status"] = "eval_passed" if run.get("passed") else "eval_failed"
    proposal["regressions"] = run.get("regressions", [])
    _save(proposal)
    return proposal


def apply(proposal_id: str, human: str) -> dict[str, Any]:
    """The human lands it. Refused unless the eval passed."""
    proposal = load_proposal(proposal_id)
    if proposal["status"] != "eval_passed":
        raise ValueError(f"proposal is '{proposal['status']}', only eval_passed proposals land")
    current = skills.read(proposal["agent"])
    if current["learned"] != proposal["learned_before"]:
        raise ValueError("skill changed since the proposal; re-propose against the current file")
    skills.write_learned(proposal["agent"], proposal["learned_after"], expected_core=current["core"])
    proposal.update({"status": "applied", "applied_by": human, "applied_at": time.time()})
    _save(proposal)
    return proposal


def list_proposals(status: str | None = None) -> list[dict[str, Any]]:
    out = []
    if PROPOSAL_DIR.exists():
        for p in sorted(PROPOSAL_DIR.glob("PROP-*.json")):
            with open(p, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            if status is None or d["status"] == status:
                out.append({k: d[k] for k in ("id", "agent", "pattern", "status", "incident_ids")})
    return out
