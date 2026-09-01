"""resolve_asset(source, ref): acquire, cache by content hash, ingest once,
index the profile. The compiler is handed the resulting path + profile.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from compiler.refs import PROFILE_VERSION, ProfileIndex, profile_path

from . import blender_runner, polyhaven
from .db import Database

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "assets" / "cache"
MANIFEST = ROOT / "assets" / "manifest.json"
PROFILES_DIR = ROOT / "profiles" / "assets"
VIEWS_DIR = ROOT / "preview" / "ingest"
IMAGE_EXTS = {".hdr", ".exr", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}


def content_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()[:24]


def load_manifest() -> dict[str, Any]:
    if MANIFEST.exists():
        with open(MANIFEST, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return {"version": 1, "assets": {}}


def save_manifest(m: dict[str, Any]) -> None:
    os.makedirs(MANIFEST.parent, exist_ok=True)
    tmp = MANIFEST.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(m, fh, indent=2, sort_keys=True)
    os.replace(tmp, MANIFEST)


def acquire(source: str, ref: str, staging: str) -> tuple[str, dict[str, Any]]:
    """Fetch the raw file into staging. Returns (path, provenance)."""
    if source == "local":
        path = ref if os.path.isabs(ref) else str(ROOT / "assets" / ref)
        if not os.path.exists(path):
            raise FileNotFoundError(f"local asset '{ref}' not found at {path}")
        return path, {"license": "project", "url": None}
    if source == "polyhaven":
        kind = "hdri" if ref.endswith("#hdri") else "model"
        slug = ref.split("#", 1)[0]
        if kind == "hdri":
            url, fname = polyhaven.pick_file(slug, "hdri")
            return polyhaven.download(url, staging, fname), {"license": polyhaven.CDN_LICENSE,
                                                             "url": f"https://polyhaven.com/a/{slug}"}
        return polyhaven.download_model_bundle(slug, staging), {"license": polyhaven.CDN_LICENSE,
                                                                "url": f"https://polyhaven.com/a/{slug}"}
    if source in ("sketchfab", "meshy", "tripo"):
        raise NotImplementedError(f"{source}: needs an API key and a client; drop the file under "
                                  "assets/ and use source 'local' meanwhile")
    raise ValueError(f"unknown source {source!r}")


def cache_path(h: str, original: str) -> Path:
    return CACHE_DIR / h / os.path.basename(original)


def resolve_asset(source: str, ref: str, db: Database | None = None, klass: str | None = None,
                  retarget_profile: str | None = None, render_views: bool = True,
                  force_ingest: bool = False) -> dict[str, Any]:
    """Never re-download; never re-ingest at the current profile version."""
    manifest = load_manifest()
    key = ProfileIndex.key(source, ref)
    entry = manifest["assets"].get(key)
    if entry is None:
        staging = str(CACHE_DIR / "_staging" / f"{int(time.time() * 1000)}")
        os.makedirs(staging, exist_ok=True)
        raw, prov = acquire(source, ref, staging)
        h = content_hash(raw)
        dest = cache_path(h, raw)
        if not dest.exists():
            if source == "local":
                os.makedirs(dest.parent, exist_ok=True)
                shutil.copyfile(raw, dest)
            else:
                shutil.move(os.path.dirname(raw) if source == "polyhaven" and raw.endswith(".gltf")
                            else raw, dest.parent if raw.endswith(".gltf") else dest)
                if raw.endswith(".gltf"):
                    dest = cache_path(h, raw)
        shutil.rmtree(staging, ignore_errors=True)
        entry = {"hash": h, "path": str(dest), "license": prov["license"], "url": prov["url"],
                 "acquired": time.time(), "generated": source in ("meshy", "tripo")}
        manifest["assets"][key] = entry
        save_manifest(manifest)
    h, path = entry["hash"], entry["path"]
    if db is not None:
        db.upsert_asset(h, source, ref, path, entry.get("license"), entry.get("url"))

    pp = profile_path(h, PROFILES_DIR)
    profile = None
    if pp.exists() and not force_ingest:
        with open(pp, "r", encoding="utf-8") as fh:
            profile = json.load(fh)
        if profile.get("profile_version") != PROFILE_VERSION:
            profile = None          # pipeline changed: re-ingest, never mix generations
    if profile is None and os.path.splitext(path)[1].lower() in IMAGE_EXTS:
        # HDRIs and textures have no geometry; their profile is provenance only.
        profile = {"profile_version": PROFILE_VERSION, "hash": h, "source": source, "ref": ref,
                   "class": "image", "landmarks": {}, "flags": {"generated": False}}
        os.makedirs(pp.parent, exist_ok=True)
        with open(pp, "w", encoding="utf-8") as fh:
            json.dump(profile, fh, indent=2)
    if profile is None:
        views = str(VIEWS_DIR / h) if render_views else None
        res = blender_runner.ingest(path, h, source, ref, str(pp), klass=klass,
                                    retarget_profile=retarget_profile, views_dir=views)
        if not res.get("ok"):
            raise RuntimeError(f"ingest failed for {source}:{ref}: {res.get('error')}")
        with open(pp, "r", encoding="utf-8") as fh:
            profile = json.load(fh)
        if db is not None:
            db.index_profile(profile)
    return {"hash": h, "path": path, "profile": profile, "license": entry.get("license"),
            "url": entry.get("url")}


def add_sockets(source: str, ref: str, raycasts: list[dict[str, Any]],
                db: Database | None = None) -> dict[str, Any]:
    """Prop landmarks: the agent looked at the six views and names (view, u, v)
    points; ingest raycasts them onto the mesh and rejects misses."""
    manifest = load_manifest()
    entry = manifest["assets"].get(ProfileIndex.key(source, ref))
    if entry is None:
        raise FileNotFoundError(f"{source}:{ref} is not in the cache; resolve_asset first")
    pp = profile_path(entry["hash"], PROFILES_DIR)
    res = blender_runner.ingest(entry["path"], entry["hash"], source, ref, str(pp),
                                views_dir=str(VIEWS_DIR / entry["hash"]), raycasts=raycasts)
    if not res.get("ok"):
        raise RuntimeError(res.get("error"))
    with open(pp, "r", encoding="utf-8") as fh:
        profile = json.load(fh)
    if db is not None:
        db.index_profile(profile)
    return profile


def resolved_map(spec: dict[str, Any], db: Database | None = None) -> dict[str, Any]:
    """What the compiler receives: asset_id -> {path, profile}. Primitives and
    unresolved locals are left to the compiler."""
    out: dict[str, Any] = {}
    for asset in spec["assets"]:
        if asset["source"] == "primitive":
            continue
        try:
            r = resolve_asset(asset["source"], asset["ref"], db, render_views=False)
            out[asset["id"]] = {"path": r["path"], "profile": r["profile"]}
        except (FileNotFoundError, NotImplementedError) as e:
            out[asset["id"]] = {"path": None, "profile": None, "error": str(e)}
    hdri = spec["world"].get("hdri")
    if hdri:
        ref = hdri["ref"] if hdri["source"] != "polyhaven" else hdri["ref"] + "#hdri"
        r = resolve_asset(hdri["source"], ref, db, render_views=False) if hdri["source"] == "polyhaven" \
            else {"path": str(ROOT / "assets" / hdri["ref"])}
        out["__world__"] = {"path": r["path"]}
    return out
