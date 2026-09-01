"""Spec, build, preview, checkpoint tools."""

from __future__ import annotations

import os
from typing import Any

from compiler.refs import ROOT

from .. import blender_runner
from ..ingest_driver import resolved_map
from ..state import get_state


def open_spec(name: str) -> dict[str, Any]:
    """Open a shot (by shot id like s01_002, scene name, or path) as the working spec."""
    s = get_state().open(name)
    return {"name": s.name, "path": str(s.spec_path), "valid": s.validate().ok}


def read_spec() -> dict[str, Any]:
    """The current working spec."""
    return get_state().require().spec


def validate_spec() -> dict[str, Any]:
    """Dry run: schema + semantic validation of the working spec. No side effects."""
    return get_state().require().validate().to_dict()


def patch_spec(operations: list[dict[str, Any]], agent: str | None = None) -> dict[str, Any]:
    """JSON-Patch the working spec. Paths may address array items by id:
    /assets/id=hero/transform/location. Rejected entirely if the result is invalid."""
    result, new = get_state().require().patch(operations, agent=agent)
    return {"applied": new is not None, **result.to_dict()}


def acquire_write_lock(agent: str) -> dict[str, Any]:
    """Take the single write lock for a specialist (blocking, animation, camera, lighting)."""
    get_state().require().acquire(agent)
    return {"writer": agent}


def release_write_lock(agent: str) -> dict[str, Any]:
    get_state().require().release(agent)
    return {"writer": None}


def _resolved() -> dict[str, Any]:
    st = get_state()
    st.resolved = resolved_map(st.require().spec, st.db)
    return st.resolved


def compile_scene() -> dict[str, Any]:
    """Build the working spec into a fresh .blend (headless Blender). Returns stage/entity on failure."""
    st = get_state()
    s = st.require()
    out = st.build_blend_path()
    res = blender_runner.build(str(s.spec_path), out, "build", resolved=_resolved())
    if res.get("ok"):
        s.blend_path, s.last_fingerprint = out, res.get("fingerprint")
    return _slim(res)


def render_preview(angles: list[str] | None = None, quality: str = "fast",
                   frame: int | None = None) -> dict[str, Any]:
    """Compile + render fixed preview angles (camera, top, three_quarter). fast=Workbench 480p,
    lookdev=EEVEE low samples. Read the returned images back before deciding anything."""
    st = get_state()
    s = st.require()
    res = blender_runner.build(str(s.spec_path), st.build_blend_path(), "preview",
                               resolved=_resolved(), quality=quality, angles=angles, frame=frame,
                               out_dir=str(ROOT / "preview" / s.name))
    if res.get("ok"):
        s.blend_path, s.last_fingerprint = st.build_blend_path(), res.get("fingerprint")
    return _slim(res)


def render_final(frames: str | None = None, engine: str | None = None) -> dict[str, Any]:
    """Final frames into renders/<shot>/. Resumable. Explicit invocation only."""
    from ..render_queue import render_shot
    st = get_state()
    s = st.require()
    if s.spec.get("sequence"):
        return render_shot(st.db, s.name, frames=frames, engine=engine)
    res = blender_runner.build(str(s.spec_path), None, "final", resolved=_resolved(),
                               frames=frames, engine=engine, out_dir=str(ROOT / "renders" / s.name))
    return _slim(res)


def checkpoint(label: str) -> dict[str, Any]:
    """Snapshot the current spec (and .blend if built) under a label."""
    return get_state().require().checkpoint(label)


def restore(label: str) -> dict[str, Any]:
    """Roll the working spec back to a checkpoint."""
    return get_state().require().restore(label)


def list_checkpoints() -> list[dict[str, Any]]:
    return get_state().require().list_checkpoints()


def _slim(res: dict[str, Any]) -> dict[str, Any]:
    keep = ("ok", "mode", "stage", "error", "entity_id", "outputs", "warnings", "blend_path",
            "fingerprint", "seconds", "extra")
    out = {k: res.get(k) for k in keep if k in res}
    if not res.get("ok") and res.get("blender_log"):
        out["blender_log_tail"] = "\n".join(res["blender_log"].splitlines()[-12:])
    return out


def read_image_path(path: str) -> str:
    """Resolve a preview path for read_image."""
    p = path if os.path.isabs(path) else str(ROOT / path)
    if not os.path.exists(p):
        raise FileNotFoundError(p)
    return p
