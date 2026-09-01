"""Director mode, server side: takes, decimation, apply and promote.

The browser records camera keys relative to a landmark (target, distance,
azimuth, elevation, focal). Raw tracks are kept in director/takes/ as reference;
only decimated sparse keys go into the spec (hard rule 15).
"""

from __future__ import annotations

import json
import math
import os
import time
import uuid
from pathlib import Path
from typing import Any

from compiler.coords import snap_focal

from .db import Database
from .presets import save_preset

ROOT = Path(__file__).resolve().parent.parent
TAKES_DIR = ROOT / "director" / "takes"
PROXY_DIR = ROOT / "director" / "proxies"

KEY_FIELDS = ("distance", "azimuth", "elevation", "roll", "focal")
DEFAULT_TOLERANCE = {"distance": 0.03, "azimuth": 1.0, "elevation": 1.0, "roll": 0.5, "focal": 0.5}
TARGET_SPACING = (8, 12)   # frames between keys on a smooth move


def _take_path(take_id: str) -> Path:
    return TAKES_DIR / f"{take_id}.json"


def save_take(shot_id: str, mode: str, samples: list[dict[str, Any]], fps: float,
              lens_set: list[float], db: Database | None = None) -> dict[str, Any]:
    """Persist a raw track. samples: [{t or frame, target, distance, azimuth, elevation,
    roll, focal}]. Live mode gives times in seconds; keyframe mode gives frames."""
    if mode not in ("live", "keyframe"):
        raise ValueError("mode must be live or keyframe")
    take_id = f"take_{time.strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:6]}"
    norm = []
    for s in samples:
        frame = s["frame"] if "frame" in s else int(round(s["t"] * fps)) + 1
        norm.append({"frame": frame, "target": s["target"], "distance": float(s["distance"]),
                     "azimuth": float(s["azimuth"]), "elevation": float(s["elevation"]),
                     "roll": float(s.get("roll", 0.0)),
                     "focal": snap_focal(float(s["focal"]), lens_set),
                     "snapped": isinstance(s["target"], str)})
    norm.sort(key=lambda k: k["frame"])
    take = {"id": take_id, "shot_id": shot_id, "mode": mode, "fps": fps,
            "recorded_at": time.time(), "samples": norm, "promoted_to": None}
    os.makedirs(TAKES_DIR, exist_ok=True)
    with open(_take_path(take_id), "w", encoding="utf-8") as fh:
        json.dump(take, fh, indent=2)
    if db is not None:
        db.execute("INSERT INTO takes(id, shot_id, mode, raw_path, recorded_at) VALUES (?,?,?,?,?)",
                   (take_id, shot_id, mode, str(_take_path(take_id)), take["recorded_at"]))
    return take


def load_take(take_id: str) -> dict[str, Any]:
    p = _take_path(take_id)
    if not p.exists():
        raise FileNotFoundError(f"no take '{take_id}'")
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def list_takes(shot_id: str) -> list[dict[str, Any]]:
    out = []
    if TAKES_DIR.exists():
        for p in sorted(TAKES_DIR.glob("take_*.json")):
            with open(p, "r", encoding="utf-8") as fh:
                t = json.load(fh)
            if t["shot_id"] == shot_id:
                out.append({"id": t["id"], "mode": t["mode"], "samples": len(t["samples"]),
                            "frames": [t["samples"][0]["frame"], t["samples"][-1]["frame"]]
                            if t["samples"] else None, "promoted_to": t.get("promoted_to")})
    return out


# --- decimation ------------------------------------------------------------------------

def _interp(a: dict, b: dict, frame: int) -> dict[str, float]:
    if b["frame"] == a["frame"]:
        return {k: a[k] for k in KEY_FIELDS}
    u = (frame - a["frame"]) / (b["frame"] - a["frame"])
    out = {}
    for k in KEY_FIELDS:
        if k == "azimuth":   # shortest arc
            d = ((b[k] - a[k] + 180) % 360) - 180
            out[k] = a[k] + d * u
        else:
            out[k] = a[k] + (b[k] - a[k]) * u
    return out


def _error(sample: dict, approx: dict[str, float], tol: dict[str, float]) -> float:
    worst = 0.0
    for k in KEY_FIELDS:
        d = abs(sample[k] - approx[k])
        if k == "azimuth":
            d = abs(((d + 180) % 360) - 180)
        worst = max(worst, d / tol[k])
    return worst


def decimate(samples: list[dict[str, Any]], tolerance: dict[str, float] | None = None
             ) -> list[dict[str, Any]]:
    """Ramer-Douglas-Peucker over the multi-channel track, in normalized units,
    so keys land where direction changes and nowhere else. Then enforce a minimum
    spacing so a jittery track cannot survive as dense keys."""
    if len(samples) <= 2:
        return list(samples)
    tol = {**DEFAULT_TOLERANCE, **(tolerance or {})}
    keep = {0, len(samples) - 1}
    stack = [(0, len(samples) - 1)]
    while stack:
        i, j = stack.pop()
        if j - i < 2:
            continue
        a, b = samples[i], samples[j]
        worst_idx, worst = -1, 0.0
        for k in range(i + 1, j):
            e = _error(samples[k], _interp(a, b, samples[k]["frame"]), tol)
            if e > worst:
                worst_idx, worst = k, e
        if worst > 1.0:
            keep.add(worst_idx)
            stack += [(i, worst_idx), (worst_idx, j)]
    ordered = [samples[i] for i in sorted(keep)]
    # minimum spacing: keep the first of any cluster closer than TARGET_SPACING[0]
    out = [ordered[0]]
    for s in ordered[1:-1]:
        if s["frame"] - out[-1]["frame"] >= TARGET_SPACING[0]:
            out.append(s)
    if ordered[-1]["frame"] != out[-1]["frame"]:
        out.append(ordered[-1])
    return out


def take_to_move(take: dict[str, Any], tolerance: dict[str, float] | None = None
                 ) -> dict[str, Any]:
    keys = decimate(take["samples"], tolerance) if take["mode"] == "live" else take["samples"]
    return {"preset": None, "keys": [
        {"frame": k["frame"], "target": k["target"], "distance": round(k["distance"], 4),
         "azimuth": round(k["azimuth"], 3), "elevation": round(k["elevation"], 3),
         "roll": round(k.get("roll", 0.0), 3), "focal": k["focal"], "interpolation": "BEZIER"}
        for k in keys]}


def apply_take_operations(take_id: str, camera_id: str = "cam_main",
                          tolerance: dict[str, float] | None = None) -> list[dict[str, Any]]:
    """JSON Patch ops that put the decimated move on the camera."""
    take = load_take(take_id)
    move = take_to_move(take, tolerance)
    base = f"/cameras/id={camera_id}"
    # "add" on an object key upserts (RFC 6902); move/keyframes may be absent on the camera.
    return [{"op": "add", "path": f"{base}/move", "value": move},
            {"op": "add", "path": f"{base}/track_target", "value": None},
            {"op": "add", "path": f"{base}/keyframes", "value": []}]


def promote_take(take_id: str, name: str, description: str, shot_types: list[str],
                 register: str | None, db: Database | None = None) -> Path:
    take = load_take(take_id)
    move = take_to_move(take)
    if any(not isinstance(k["target"], str) for k in move["keys"]):
        raise ValueError("take has unsnapped keys; only landmark-anchored moves are promotable")
    for k in move["keys"]:
        k["target"] = "@{subject}." + k["target"].split(".", 1)[1]
    path = save_preset("camera", name, {"description": description, "shot_types": shot_types,
                                        "register": register, "type": "PERSP", "dof": None,
                                        "move": move, "tags": ["director"]})
    take["promoted_to"] = name
    with open(_take_path(take_id), "w", encoding="utf-8") as fh:
        json.dump(take, fh, indent=2)
    if db is not None:
        db.execute("UPDATE takes SET promoted_to=? WHERE id=?", (name, take_id))
    return path
