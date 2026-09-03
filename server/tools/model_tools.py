"""Model recipe tools: the modeling agent's loop is patch_model -> preview_model -> read_image."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from compiler.issues import Issue, ValidationResult
from compiler.refs import MODEL_PROFILES_DIR, MODELS_DIR, ROOT
from compiler.validate import load_json
from compiler.validate_model import validate_model

from ..blender_runner import find_blender
from ..patch import PatchError, apply_patch
from ..state import get_state

BUILD_MODEL_PY = ROOT / "compiler" / "modeling" / "build_model.py"
PREVIEW_DIR = ROOT / "preview" / "models"
_T = {"location": [0, 0, 0], "rotation_euler": [0, 0, 0], "scale": [1, 1, 1]}


def _path(model_id: str) -> Path:
    return MODELS_DIR / f"{model_id}.json"


def _load(model_id: str) -> dict[str, Any]:
    p = _path(model_id)
    if not p.exists():
        have = ", ".join(m["id"] for m in list_models()) or "none"
        raise FileNotFoundError(f"no model '{model_id}' (have: {have}); new_model creates one")
    return load_json(p)


def _save(model: dict[str, Any]) -> None:
    os.makedirs(MODELS_DIR, exist_ok=True)
    tmp = _path(model["id"]).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, _path(model["id"]))


def list_models() -> list[dict[str, Any]]:
    """Model recipes on disk, with whether each has been built (has a profile)."""
    out = []
    for p in sorted(MODELS_DIR.glob("*.json")) if MODELS_DIR.exists() else []:
        m = load_json(p)
        prof = MODEL_PROFILES_DIR / f"{p.stem}.json"
        out.append({"id": m["id"], "kind": m["kind"], "parts": len(m["parts"]), "built": prof.exists(),
                    "height": load_json(prof).get("height") if prof.exists() else None})
    return out


def new_model(model_id: str, kind: str, reference: str | None = None, height: float | None = None,
              description: str = "") -> dict[str, Any]:
    """Create an empty recipe. kind: character | prop | set. reference: image path shown beside the turntable."""
    if _path(model_id).exists():
        raise FileExistsError(f"model '{model_id}' exists; read_model / patch_model it")
    model = {"version": "1.0", "id": model_id, "kind": kind, "description": description,
             "reference": reference, "height": height, "parts": [], "materials": {}, "landmarks": {}, "skeleton": None}
    _save(model)
    return {"id": model_id, "path": str(_path(model_id))}


def read_model(model_id: str) -> dict[str, Any]:
    """The full recipe."""
    return _load(model_id)


def validate_model_tool(model_id: str) -> dict[str, Any]:
    """Schema + semantic validation of a recipe. No side effects."""
    return validate_model(_load(model_id)).to_dict()


def patch_model(model_id: str, operations: list[dict[str, Any]]) -> dict[str, Any]:
    """JSON-Patch a recipe (parts by id: /parts/id=torso/params/joints/hip/radius).
    Rejected entirely if the result is invalid; the reasons name the part."""
    model = _load(model_id)
    try:
        candidate = apply_patch(model, operations)
    except PatchError as e:
        return {"applied": False, **ValidationResult([Issue("error", "patch", "bad_operation", str(e), e.op.get("path", ""))]).to_dict()}
    result = validate_model(candidate)
    if result.ok:
        _save(candidate)
    return {"applied": result.ok, **result.to_dict()}


def preview_model(model_id: str, views: list[str] | None = None, quality: str = "lookdev") -> dict[str, Any]:
    """Build the recipe in Blender, write its profile, render the turntable
    (front, three_quarter, side, back, head + compare.png beside the reference).
    Read the images back before the next patch."""
    model = _load(model_id)
    result = validate_model(model)
    if not result.ok:
        return {"ok": False, "stage": "validate", **result.to_dict()}
    out_dir = PREVIEW_DIR / model_id
    views = views or ["front", "three_quarter", "side", "back", "head"]
    cmd = [find_blender(), "-b", "--factory-startup", "--python", str(BUILD_MODEL_PY), "--",
           "--model", str(_path(model_id)), "--profile-out", str(MODEL_PROFILES_DIR / f"{model_id}.json"),
           "--preview-dir", str(out_dir), "--views", ",".join(views), "--quality", quality]
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, cwd=str(ROOT))
    marker = next((l for l in (proc.stdout or "").splitlines() if l.startswith("BLENDY_RESULT ")), None)
    if not marker:
        tail = "\n".join(((proc.stdout or "") + (proc.stderr or "")).splitlines()[-20:])
        return {"ok": False, "stage": "blender", "error": tail}
    res = json.loads(marker[len("BLENDY_RESULT "):])
    res["seconds"] = round(time.time() - t0, 2)
    if res.get("ok"):
        get_state().reload_profiles()
    return res


def model_profile(model_id: str) -> dict[str, Any]:
    """Measured dimensions, height, poly count and landmark vocabulary of a built model."""
    p = MODEL_PROFILES_DIR / f"{model_id}.json"
    if not p.exists():
        raise FileNotFoundError(f"model '{model_id}' is not built yet; preview_model first")
    return load_json(p)


def checkpoint_model(model_id: str, label: str) -> dict[str, Any]:
    """Snapshot a recipe under models/_checkpoints/<id>/<stamp>_<label>.json."""
    d = MODELS_DIR / "_checkpoints" / model_id
    os.makedirs(d, exist_ok=True)
    dest = d / f"{time.strftime('%Y%m%d-%H%M%S')}_{label}.json"
    shutil.copyfile(_path(model_id), dest)
    return {"path": str(dest)}


def restore_model(model_id: str, label: str) -> dict[str, Any]:
    d = MODELS_DIR / "_checkpoints" / model_id
    matches = sorted(d.glob(f"*_{label}.json")) if d.exists() else []
    if not matches:
        raise FileNotFoundError(f"no checkpoint '{label}' for model '{model_id}'")
    shutil.copyfile(matches[-1], _path(model_id))
    return {"restored": str(matches[-1])}


def mark_reference(model_id: str, points: dict[str, list[float]]) -> dict[str, Any]:
    """Record where the reference image's landmarks are, so the model can be
    measured against it.

    Look at the reference and give normalized coordinates: [0,0] is the top
    left of the image, [1,1] the bottom right. Required: head_top, chin, eye,
    shoulder_l, shoulder_r, ground. Optional and worth adding when the image
    shows them clearly: hip, ear_l, ear_r, hand_l, hand_r, knee.

    Mark shoulder_l/r where the arm meets the torso, NOT the outer edge of the
    silhouette: the model's own shoulder landmark is the joint, and marking the
    deltoid instead makes every model look too narrow. Left and right are the
    character's own, so shoulder_l is on the viewer's right.

    Rejected if the points cannot be measured, e.g. a chin above the crown.
    """
    model = _load(model_id)
    model["reference_points"] = {k: list(v) for k, v in points.items()}
    result = validate_model(model)
    if not result.ok:
        return {"ok": False, "errors": [i.as_dict() for i in result.issues if i.level == "error"]}
    _save(model)
    return {"ok": True, "points": model["reference_points"],
            "next": "compare_to_reference to see where the model disagrees"}


def compare_to_reference(model_id: str) -> dict[str, Any]:
    """Proportions of the built model against the marked reference, as a table.

    Every row is a scale-invariant ratio, so framing and size cannot confound
    it. 'strong' means fix that before anything else; 'ok' is inside the noise
    of marking points by eye and is not worth a turn.

    Needs mark_reference first, and a built model (preview_model writes the
    profile this reads).
    """
    from compiler.proportions import compare, measure, model_points, summarize

    model = _load(model_id)
    marked = model.get("reference_points")
    if not marked:
        raise ValueError(f"'{model_id}' has no reference_points; call mark_reference first")
    profile_path = MODEL_PROFILES_DIR / f"{model_id}.json"
    if not profile_path.exists():
        raise FileNotFoundError(f"model '{model_id}' is not built yet; preview_model first")

    built = model_points(load_json(profile_path))
    rows = compare(marked, built)
    return {"model_id": model_id, "reference": model.get("reference"),
            "rows": rows, "table": summarize(rows),
            "model_measures": measure(built)}
