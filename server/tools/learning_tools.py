"""Incidents, skills (read only for agents; proposals for the orchestrator), evals, corrections."""

from __future__ import annotations

from typing import Any

from .. import corrections, incidents, orchestrator, skills
from ..state import get_state


def write_incident(shot: str, agent: str, expected: str, observed: str,
                   category: str | None = None, previews: list[str] | None = None,
                   session: str | None = None) -> dict[str, Any]:
    """Review agent: record one structured failure. category is a proposal (schema|tool|judgment)."""
    rec = incidents.write_incident(get_state().db, {"shot": shot, "agent": agent, "expected": expected,
                                                    "observed": observed, "category": category,
                                                    "previews": previews or []}, session=session)
    return {"id": rec["id"], "category": rec["category"]}


def list_incidents(shot: str | None = None, agent: str | None = None,
                   open_only: bool = False) -> list[dict[str, Any]]:
    return incidents.list_incidents(get_state().db, shot, agent, open_only)


def triage_incident(incident_id: str, category: str, note: str | None = None) -> dict[str, Any]:
    """Confirm an incident's bucket: schema (fix the validator), tool (fix the surface), judgment."""
    incidents.triage(get_state().db, incident_id, category, note)
    return {"id": incident_id, "category": category}


def resolve_incident(incident_id: str, kind: str, ref: str) -> dict[str, Any]:
    """Mark resolved: kind is validator|tool|skill|profile|preset, ref names the fix."""
    incidents.resolve(get_state().db, incident_id, kind, ref)
    return {"id": incident_id, "resolution": {"kind": kind, "ref": ref}}


def incident_patterns() -> list[dict[str, Any]]:
    """Recurring unresolved patterns across >= 3 distinct shots and sessions (a GROUP BY)."""
    return orchestrator.patterns(get_state().db)


def read_skill(name: str) -> dict[str, Any]:
    """A skill file split into immutable core and capped learned lines."""
    return skills.read(name)


def propose_skill_edit(agent: str, incident_ids: list[str], pattern: str,
                       not_schema: str, not_tool: str, why_judgment: str) -> dict[str, Any]:
    """Orchestrator: propose one learned line. Requires written triage. Applies nothing."""
    prop = orchestrator.propose(get_state().db, agent, incident_ids, pattern,
                                {"not_schema": not_schema, "not_tool": not_tool, "why_judgment": why_judgment})
    return {"id": prop["id"], "status": prop["status"], "diff": prop["diff"]}


def list_proposals(status: str | None = None) -> list[dict[str, Any]]:
    return orchestrator.list_proposals(status)


def evaluate_proposal(proposal_id: str) -> dict[str, Any]:
    """Run the frozen eval set with the proposed edit applied temporarily. Lands only on no regression,
    and only a human applies it afterwards (apply_proposal)."""
    from evals.run import run_evals
    db = get_state().db
    prop = orchestrator.evaluate(db, proposal_id, lambda diff: run_evals(db, skill_diff=diff))
    return {"id": prop["id"], "status": prop["status"], "regressions": prop.get("regressions", [])}


def apply_proposal(proposal_id: str, human: str) -> dict[str, Any]:
    """Human lands an eval-passed proposal. Pass your name; this is an audit trail."""
    prop = orchestrator.apply(proposal_id, human)
    return {"id": prop["id"], "status": prop["status"]}


def run_evals(accept: bool = False) -> dict[str, Any]:
    """Run the eval set now. accept=true promotes this run's previews to the references."""
    from evals.run import run_evals as _run
    return _run(get_state().db, accept=accept)


def add_correction(scope: str, situation: str, wrong: str, right: str) -> dict[str, Any]:
    """Last resort. Prefer a validator, tool, profile or preset fix. Scope must be narrow."""
    return corrections.add(get_state().db, {"scope": scope, "situation": situation,
                                            "wrong": wrong, "right": right})


def retrieve_corrections(scope_terms: list[str], situation: str, limit: int = 3) -> list[dict[str, Any]]:
    """Narrow retrieval: filter by scope terms, rank within. Never the whole log."""
    return corrections.retrieve(get_state().db, scope_terms, situation, limit)


def render_status() -> str:
    """Per-shot render state table."""
    from ..render_queue import report
    return report(get_state().db)


def render_queue_run(shot_ids: list[str] | None = None, workers: int = 2) -> list[dict[str, Any]]:
    """Render every pending shot (or the given ones) with N parallel Blender processes. Resumable."""
    from ..render_queue import render_all
    return render_all(get_state().db, shot_ids, workers)
