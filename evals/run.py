"""Eval runner. Frozen shots in evals/shots/, accepted references in
evals/references/<shot>/<angle>.png. Every run is archived in evals/results/
and the database, and compared against the last passing run: land only on no
regression, anywhere.

    ./.venv/bin/python -m evals.run [--accept]      # --accept: promote this run's previews to references
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.scoring import METRICS, regressed, score   # noqa: E402
from server import blender_runner                     # noqa: E402
from server.db import Database                        # noqa: E402
from server.ingest_driver import resolved_map         # noqa: E402

SHOTS_DIR = ROOT / "evals" / "shots"
REF_DIR = ROOT / "evals" / "references"
RESULTS_DIR = ROOT / "evals" / "results"
ANGLES = ["camera", "top", "three_quarter"]


def eval_shots() -> list[Path]:
    return sorted(SHOTS_DIR.glob("*.json"))


def last_passing(db: Database) -> dict[str, dict[str, float]]:
    run = db.one("SELECT id FROM eval_runs WHERE passed=1 ORDER BY started DESC LIMIT 1")
    if not run:
        return {}
    out: dict[str, dict[str, float]] = {}
    for r in db.query("SELECT shot, metric, value FROM eval_results WHERE run=?", (run["id"],)):
        out.setdefault(r["shot"], {})[r["metric"]] = r["value"]
    return out


def run_evals(db: Database, skill_diff: str | None = None, accept: bool = False,
              commit_ref: str | None = None) -> dict[str, Any]:
    run_id = f"run_{time.strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:6]}"
    out_dir = RESULTS_DIR / run_id
    os.makedirs(out_dir, exist_ok=True)
    db.execute("INSERT INTO eval_runs(id, commit_ref, skill_diff, started) VALUES (?,?,?,?)",
               (run_id, commit_ref, skill_diff, time.time()))
    baseline = last_passing(db)
    record: dict[str, Any] = {"id": run_id, "shots": {}, "regressions": [], "errors": {}}

    for spec_path in eval_shots():
        shot = spec_path.stem
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        try:
            res = blender_runner.build(str(spec_path), None, "preview", resolved=resolved_map(spec, db),
                                       quality="fast", angles=ANGLES, out_dir=str(out_dir / shot))
        except Exception as e:  # noqa: BLE001
            record["errors"][shot] = str(e)
            continue
        if not res.get("ok"):
            record["errors"][shot] = res.get("error")
            continue
        metrics: dict[str, float] = {m: 0.0 for m in METRICS}
        n = 0
        for img in res["outputs"]:
            angle = os.path.basename(img).split("_")[0]
            ref = REF_DIR / shot / f"{angle}.png"
            if accept:
                os.makedirs(ref.parent, exist_ok=True)
                shutil.copyfile(img, ref)
            if not ref.exists():
                continue
            s = score(img, ref)
            for m in METRICS:
                metrics[m] += s[m]
            n += 1
        if n:
            metrics = {m: v / n for m, v in metrics.items()}
            with db.tx() as c:
                for m, v in metrics.items():
                    c.execute("INSERT INTO eval_results(run, shot, metric, value) VALUES (?,?,?,?)",
                              (run_id, shot, m, v))
            record["shots"][shot] = metrics
            for m in regressed(metrics, baseline.get(shot, {})):
                record["regressions"].append({"shot": shot, "metric": m,
                                              "was": baseline[shot][m], "now": metrics[m]})
    passed = not record["errors"] and not record["regressions"]
    record["passed"] = passed
    db.execute("UPDATE eval_runs SET finished=?, passed=? WHERE id=?", (time.time(), int(passed), run_id))
    with open(out_dir / "run.json", "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
    return record


if __name__ == "__main__":
    rec = run_evals(Database(), accept="--accept" in sys.argv)
    print(json.dumps(rec, indent=2))
    sys.exit(0 if rec["passed"] else 1)
