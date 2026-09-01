"""Director mode + presets tools."""

from __future__ import annotations

from typing import Any

from compiler.refs import ROOT

from .. import blender_runner, director, presets
from ..ingest_driver import resolved_map
from ..state import get_state


def export_proxy() -> dict[str, Any]:
    """Greybox glTF of the working shot for the browser director, cache-keyed on the spec hash."""
    from ..session import spec_hash
    st = get_state()
    s = st.require()
    out = director.PROXY_DIR / f"{s.name}.glb"
    stamp = director.PROXY_DIR / f"{s.name}.hash"
    h = spec_hash(s.spec)
    if out.exists() and stamp.exists() and stamp.read_text() == h:
        return {"path": str(out), "cached": True}
    res = blender_runner.build(str(s.spec_path), None, "proxy", resolved=resolved_map(s.spec, st.db),
                               out_path=str(out))
    if not res.get("ok"):
        return {"ok": False, "stage": res.get("stage"), "error": res.get("error"), "entity_id": res.get("entity_id")}
    out.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(h)
    return {"path": str(out), "cached": False,
            "serve": "./.venv/bin/python -m server.studio"}


def list_takes() -> list[dict[str, Any]]:
    """Recorded camera takes for the working shot."""
    return director.list_takes(get_state().require().name)


def apply_take(take_id: str, camera_id: str = "cam_main") -> dict[str, Any]:
    """Decimate a take into sparse landmark-anchored keys and patch it onto the camera."""
    s = get_state().require()
    ops = director.apply_take_operations(take_id, camera_id)
    result, new = s.patch(ops)
    keys = ops[0]["value"]["keys"]
    return {"applied": new is not None, "keys": len(keys),
            "unsnapped": [k["frame"] for k in keys if not isinstance(k["target"], str)], **result.to_dict()}


def promote_take(take_id: str, name: str, description: str, shot_types: list[str],
                 register: str | None = None) -> dict[str, Any]:
    """Take -> named camera preset in profiles/presets/camera/. Your taste as data."""
    p = director.promote_take(take_id, name, description, shot_types, register, get_state().db)
    return {"preset": name, "path": str(p)}


def list_presets(kind: str | None = None) -> list[dict[str, Any]]:
    """Reusable validated spec fragments: lighting, camera, material, framing."""
    return presets.list_presets(kind)


def apply_preset(kind: str, name: str, target_ids: dict[str, str] | None = None) -> dict[str, Any]:
    """Patch a preset into the working spec. camera presets need target_ids={subject, camera};
    material presets need target_ids={asset}."""
    s = get_state().require()
    ops = presets.apply_operations(kind, name, target_ids, s.spec)
    result, new = s.patch(ops)
    return {"applied": new is not None, **result.to_dict()}


def promote_preset(kind: str, name: str, description: str, entity_id: str | None = None,
                   shot_types: list[str] | None = None, register: str | None = None) -> dict[str, Any]:
    """Promote part of the working (accepted) spec into a preset."""
    s = get_state().require()
    if kind == "lighting":
        p = presets.promote_lighting(s.spec, name, description)
    elif kind == "camera":
        p = presets.promote_camera(s.spec, entity_id or "cam_main", name, description, shot_types or [], register)
    elif kind == "material":
        p = presets.promote_material(s.spec, entity_id, name, description)
    else:
        raise ValueError(f"cannot promote kind {kind!r}")
    return {"preset": name, "path": str(p)}
