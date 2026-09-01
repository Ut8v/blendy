"""Asset discovery, acquisition, profiles, landmarks, clips, dialogue."""

from __future__ import annotations

from typing import Any

from compiler.refs import ProfileIndex

from .. import clips, ingest_driver, lipsync, polyhaven
from ..state import get_state


def search_assets(query: str, source: str = "polyhaven", kind: str = "model",
                  limit: int = 20) -> list[dict[str, Any]]:
    """Find assets. Poly Haven kinds: model, hdri. Returns refs usable in resolve_asset."""
    if source == "polyhaven":
        return polyhaven.search(query, kind=kind, limit=limit)
    if source == "local":
        from pathlib import Path
        root = ingest_driver.ROOT / "assets"
        hits = [str(p.relative_to(root)) for p in root.rglob("*")
                if p.suffix.lower() in (".fbx", ".glb", ".gltf", ".obj", ".blend", ".hdr", ".exr")
                and query.lower() in str(p).lower()]
        return [{"source": "local", "ref": h} for h in sorted(hits)[:limit]]
    raise ValueError(f"search not implemented for source {source!r}")


def resolve_asset(source: str, ref: str, asset_class: str | None = None,
                  retarget_profile: str | None = None, force_ingest: bool = False) -> dict[str, Any]:
    """Acquire + cache + ingest once. Characters need retarget_profile (e.g. mixamo_default)
    and must already be rigged. Returns the profile: class, dimensions, landmarks, flags."""
    st = get_state()
    r = ingest_driver.resolve_asset(source, ref, st.db, klass=asset_class,
                                    retarget_profile=retarget_profile, force_ingest=force_ingest)
    st.reload_profiles()
    prof = r["profile"] or {}
    return {"hash": r["hash"], "path": r["path"], "license": r["license"], "class": prof.get("class"),
            "dimensions": prof.get("measure", {}).get("dimensions"), "height": prof.get("height"),
            "landmarks": sorted(prof.get("landmarks", {})), "flags": prof.get("flags"),
            "views": {k: v["image"] for k, v in prof.get("views", {}).items()}}


def get_asset_profile(asset_id: str) -> dict[str, Any]:
    """Full ingest profile for an asset in the working spec."""
    s = get_state().require()
    asset = next((a for a in s.spec["assets"] if a["id"] == asset_id), None)
    if asset is None:
        raise KeyError(f"no asset '{asset_id}' in the spec")
    prof = ProfileIndex.load().profile_for(asset)
    if prof is None:
        raise RuntimeError(f"'{asset_id}' ({asset['source']}:{asset['ref']}) is not ingested; resolve_asset first")
    return prof


def list_landmarks(asset_id: str) -> dict[str, Any]:
    """The landmark vocabulary of an asset: what "@<asset_id>.<name>" may name."""
    prof = get_asset_profile(asset_id)
    return {name: {k: v for k, v in entry.items() if k in ("kind", "bone", "end", "normal")}
            for name, entry in prof.get("landmarks", {}).items()}


def add_sockets(source: str, ref: str, sockets: list[dict[str, Any]]) -> dict[str, Any]:
    """Prop landmarks from the six ingest views: [{view, u, v, name}], u/v in [0,1] from the
    top-left. Each is raycast onto the mesh; misses are rejected and reported."""
    st = get_state()
    prof = ingest_driver.add_sockets(source, ref, sockets, st.db)
    st.reload_profiles()
    return {"landmarks": sorted(prof.get("landmarks", {})),
            "rejected": prof.get("rejected_landmarks", {})}


def list_clips(skeleton: str = "mixamo") -> list[dict[str, Any]]:
    """Motion clips available for a skeleton type."""
    return clips.list_clips(skeleton)


def dialogue_status(line_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """Audio and phoneme availability per dialogue line, with frame counts."""
    s = get_state().require()
    ids = line_ids or [d["line_id"] for d in s.spec.get("sequence", {}).get("dialogue", [])]
    return lipsync.status(ids, s.spec["meta"]["fps"])


def extract_phonemes(line_id: str, text: str | None = None) -> dict[str, Any]:
    """Run Rhubarb on audio/dialogue/<line_id>.wav -> audio/phonemes/<line_id>.json."""
    data = lipsync.extract_phonemes(line_id, text)
    return {"line_id": line_id, "cues": len(data.get("mouthCues", [])),
            "seconds": data.get("mouthCues", [{}])[-1].get("end") if data.get("mouthCues") else 0}
