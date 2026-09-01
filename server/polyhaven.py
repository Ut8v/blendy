"""Poly Haven client. Free, CC0, well structured. stdlib only (urllib), because
this runs in the server process, not Blender.

API: https://api.polyhaven.com
  /assets?t=hdris|textures|models   listing
  /files/<slug>                     download urls per resolution/format
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any

API = "https://api.polyhaven.com"
CDN_LICENSE = "CC0-1.0"
USER_AGENT = "blendy/0.1 (+asset pipeline)"

KIND_FOR = {"hdri": "hdris", "model": "models", "texture": "textures"}


def _get_json(url: str, timeout: float = 30) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def search(query: str, kind: str = "model", limit: int = 20) -> list[dict[str, Any]]:
    """Substring match over slug, name, tags and categories. Poly Haven has no
    server-side search, so the listing is fetched and filtered here."""
    listing = _get_json(f"{API}/assets?t={KIND_FOR.get(kind, kind)}")
    q = query.lower().split()
    out = []
    for slug, meta in listing.items():
        hay = " ".join([slug, meta.get("name", ""), " ".join(meta.get("tags", [])),
                        " ".join(meta.get("categories", []))]).lower()
        if all(term in hay for term in q):
            out.append({"source": "polyhaven", "ref": slug, "name": meta.get("name"),
                        "kind": kind, "tags": meta.get("tags", [])[:8],
                        "url": f"https://polyhaven.com/a/{slug}", "license": CDN_LICENSE})
    out.sort(key=lambda a: a["ref"])
    return out[:limit]


def pick_file(slug: str, kind: str, resolution: str = "2k") -> tuple[str, str]:
    """Return (download url, filename). Models prefer glTF; HDRIs prefer .hdr."""
    files = _get_json(f"{API}/files/{slug}")
    if kind == "hdri":
        by_res = files.get("hdri", {})
        res = resolution if resolution in by_res else sorted(by_res)[0]
        entry = by_res[res].get("hdr") or by_res[res].get("exr")
        return entry["url"], f"{slug}_{res}.{entry['url'].rsplit('.', 1)[-1]}"
    if kind == "model":
        gltf = files.get("gltf", {})
        res = resolution if resolution in gltf else sorted(gltf)[0]
        entry = gltf[res]["gltf"]
        return entry["url"], f"{slug}_{res}.gltf"
    raise ValueError(f"unsupported poly haven kind {kind!r}")


def download(url: str, dest_dir: str, filename: str) -> str:
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, filename)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=300) as resp, open(path, "wb") as fh:
        while chunk := resp.read(1 << 20):
            fh.write(chunk)
    return path


def download_model_bundle(slug: str, dest_dir: str, resolution: str = "2k") -> str:
    """glTF references textures by relative path; fetch the whole include set."""
    files = _get_json(f"{API}/files/{slug}")
    gltf = files["gltf"]
    res = resolution if resolution in gltf else sorted(gltf)[0]
    entry = gltf[res]["gltf"]
    main = download(entry["url"], dest_dir, os.path.basename(urllib.parse.urlparse(entry["url"]).path))
    for rel, inc in entry.get("include", {}).items():
        sub = os.path.join(dest_dir, os.path.dirname(rel))
        download(inc["url"], sub, os.path.basename(rel))
    return main
