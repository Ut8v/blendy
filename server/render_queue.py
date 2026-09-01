"""Render orchestration: per-shot state in the database, parallel dispatch of
headless Blender processes, resume from whatever frames already exist.

Shots are independently buildable (hard rule 14), so N workers each own a
whole shot at a time and never coordinate.
"""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from compiler.frames import frames_done
from compiler.validate import load_json

from . import blender_runner
from .db import Database
from .ingest_driver import resolved_map
from .session import spec_hash

ROOT = Path(__file__).resolve().parent.parent
SHOT_DIR = ROOT / "sequence" / "shots"
RENDER_DIR = ROOT / "renders"


def shot_spec_path(shot_id: str) -> Path:
    return SHOT_DIR / f"{shot_id}.json"


def refresh_state(db: Database, shot_id: str) -> dict[str, Any]:
    """Reconcile the database with disk: spec hash and frames present."""
    spec = load_json(shot_spec_path(shot_id))
    h = spec_hash(spec)
    total = spec["meta"]["frame_end"] - spec["meta"]["frame_start"] + 1
    done = len(frames_done(str(RENDER_DIR / shot_id)))
    row = db.one("SELECT * FROM shot_state WHERE shot_id=?", (shot_id,))
    state = "pending"
    if row and row["spec_hash"] and row["spec_hash"] != h:
        state = "stale"                      # spec changed since the last render
    elif done >= total:
        state = "done"
    elif row and row["render_state"] in ("rendering", "failed"):
        state = row["render_state"]
    db.set_shot_state(shot_id, spec_hash=row["spec_hash"] if row else None,
                      render_state=state, frames_done=done, frames_total=total)
    return db.one("SELECT * FROM shot_state WHERE shot_id=?", (shot_id,))


def render_shot(db: Database, shot_id: str, frames: str | None = None,
                engine: str | None = None) -> dict[str, Any]:
    spec_path = shot_spec_path(shot_id)
    spec = load_json(spec_path)
    h = spec_hash(spec)
    out_dir = str(RENDER_DIR / shot_id)
    if db.one("SELECT 1 FROM shot_state WHERE shot_id=? AND spec_hash!=?", (shot_id, h)):
        # Stale frames would silently mix two versions of the shot. Move them aside.
        stale = f"{out_dir}_stale_{int(time.time())}"
        if os.path.isdir(out_dir) and frames_done(out_dir):
            os.rename(out_dir, stale)
    db.set_shot_state(shot_id, spec_hash=h, render_state="rendering", error=None,
                      frames_total=spec["meta"]["frame_end"] - spec["meta"]["frame_start"] + 1)
    try:
        resolved = resolved_map(spec, db)
        res = blender_runner.build(str(spec_path), None, "final", resolved=resolved,
                                   frames=frames, out_dir=out_dir, engine=engine, timeout=6 * 3600)
    except Exception as e:  # noqa: BLE001
        db.set_shot_state(shot_id, render_state="failed", error=str(e), last_render=time.time())
        raise
    extra = res.get("extra", {})
    ok = bool(res.get("ok"))
    db.set_shot_state(shot_id, render_state="done" if ok and extra.get("complete") else
                      ("failed" if not ok else "rendering"),
                      frames_done=extra.get("frames_done", 0), last_render=time.time(),
                      error=None if ok else res.get("error"))
    return {"shot": shot_id, "ok": ok, "stage": res.get("stage"), "error": res.get("error"),
            "entity_id": res.get("entity_id"), **extra}


def pending_shots(db: Database, shot_ids: list[str] | None = None) -> list[str]:
    ids = shot_ids or sorted(p.stem for p in SHOT_DIR.glob("*.json"))
    out = []
    for sid in ids:
        st = refresh_state(db, sid)
        if st["render_state"] != "done":
            out.append(sid)
    return out


def render_all(db: Database, shot_ids: list[str] | None = None, workers: int = 2,
               engine: str | None = None) -> list[dict[str, Any]]:
    todo = pending_shots(db, shot_ids)
    results = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(render_shot, db, sid, None, engine): sid for sid in todo}
        for fut in as_completed(futures):
            sid = futures[fut]
            try:
                results.append(fut.result())
            except Exception as e:  # noqa: BLE001
                results.append({"shot": sid, "ok": False, "error": str(e)})
    return sorted(results, key=lambda r: r["shot"])


def report(db: Database) -> str:
    rows = db.shot_states()
    lines = [f"{r['shot_id']:<10} {r['render_state']:<10} {r['frames_done']:>5}/{r['frames_total']:<5} "
             f"{(r['error'] or '')[:60]}" for r in rows]
    return "\n".join(lines) or "no shots tracked"


if __name__ == "__main__":   # ./.venv/bin/python -m server.render_queue [shot ...]
    import sys
    database = Database()
    ids = sys.argv[1:] or None
    print(json.dumps(render_all(database, ids), indent=2))
    print(report(database))
