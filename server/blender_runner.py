"""Runs the compiler. Headless subprocess is the production path; the live
bridge is for watching. Either way the payload is a fixed bootstrap that calls
compiler/build.py: nothing model-authored ever crosses into Blender.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
BUILD_PY = ROOT / "compiler" / "build.py"
INGEST_PY = ROOT / "compiler" / "ingest.py"

BLENDER_CANDIDATES = (
    os.environ.get("BLENDER"),
    "/Applications/Blender.app/Contents/MacOS/Blender",
    shutil.which("blender"),
)


class BlenderError(RuntimeError):
    pass


def find_blender() -> str:
    for c in BLENDER_CANDIDATES:
        if c and os.path.exists(c):
            return c
    raise BlenderError("Blender not found; set BLENDER=/path/to/blender")


def _run(script: Path, args: list[str], timeout: float, result_path: str | None) -> dict[str, Any]:
    cmd = [find_blender(), "-b", "--factory-startup", "--python", str(script), "--", *args]
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(ROOT))
    stdout = proc.stdout or ""
    marker = next((l for l in stdout.splitlines() if l.startswith("BLENDY_RESULT ")), None)
    if result_path and os.path.exists(result_path):
        with open(result_path, "r", encoding="utf-8") as fh:
            result = json.load(fh)
    elif marker:
        result = json.loads(marker[len("BLENDY_RESULT "):])
    else:
        tail = "\n".join((stdout + "\n" + (proc.stderr or "")).splitlines()[-30:])
        raise BlenderError(f"Blender exited {proc.returncode} without a result:\n{tail}")
    result.setdefault("seconds", round(time.time() - t0, 3))
    result["blender_log"] = "\n".join(stdout.splitlines()[-40:])
    return result


def build(spec_path: str, out_blend: str | None, mode: str = "build",
          resolved: dict[str, Any] | None = None, timeout: float = 600, **opts) -> dict[str, Any]:
    tmp = tempfile.mkdtemp(prefix="blendy_run_")
    result_path = os.path.join(tmp, "result.json")
    args = ["--spec", spec_path, "--mode", mode, "--result", result_path]
    if out_blend:
        args += ["--out", out_blend]
    if resolved is not None:
        rp = os.path.join(tmp, "resolved.json")
        with open(rp, "w", encoding="utf-8") as fh:
            json.dump(resolved, fh)
        args += ["--resolved", rp]
    for key, flag in (("quality", "--quality"), ("frames", "--frames"), ("frame", "--frame"),
                      ("out_dir", "--out-dir"), ("out_path", "--out-path"), ("engine", "--engine")):
        if opts.get(key) is not None:
            args += [flag, str(opts[key])]
    if opts.get("angles"):
        args += ["--angles", ",".join(opts["angles"])]
    try:
        return _run(BUILD_PY, args, timeout, result_path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def ingest(file: str, content_hash: str, source: str, ref: str, profile_out: str,
           klass: str | None = None, retarget_profile: str | None = None,
           views_dir: str | None = None, raycasts: list[dict[str, Any]] | None = None,
           timeout: float = 900) -> dict[str, Any]:
    args = ["--file", file, "--hash", content_hash, "--source", source, "--ref", ref,
            "--profile-out", profile_out]
    if klass:
        args += ["--class", klass]
    if retarget_profile:
        args += ["--retarget-profile", retarget_profile]
    if views_dir:
        args += ["--views-dir", views_dir]
    for r in raycasts or []:
        args += ["--raycast", json.dumps(r)]
    return _run(INGEST_PY, args, timeout, None)


# --- live bridge (optional) ------------------------------------------------------------
# Blender Lab's MCP add-on listens on localhost:9876 for null-delimited JSON
# {"type": "execute", "code": ...}. We send ONE fixed line that imports the
# compiler and calls build(). The code string is a constant, not generated.

_BRIDGE_CODE = (
    "import sys, json; sys.path.insert(0, {root!r}); "
    "import importlib, compiler.build as b; importlib.reload(b); "
    "r = b.build({spec!r}, {out!r}, {mode!r}, {resolved!r}); "
    "print('BLENDY_RESULT ' + r.to_json())"
)


def bridge_available(host: str = "localhost", port: int = 9876) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def build_via_bridge(spec_path: str, out_blend: str | None, mode: str = "build",
                     resolved_path: str | None = None, host: str = "localhost",
                     port: int = 9876, timeout: float = 600) -> dict[str, Any]:
    code = _BRIDGE_CODE.format(root=str(ROOT), spec=spec_path, out=out_blend, mode=mode,
                               resolved=resolved_path)
    payload = json.dumps({"type": "execute", "code": code, "strict_json": False}).encode() + b"\0"
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(payload)
        buf = b""
        while not buf.endswith(b"\0"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    response = json.loads(buf.rstrip(b"\0").decode() or "{}")
    stdout = str(response.get("stdout", ""))
    marker = next((l for l in stdout.splitlines() if l.startswith("BLENDY_RESULT ")), None)
    if not marker:
        raise BlenderError(f"bridge returned no result: {response}")
    return json.loads(marker[len("BLENDY_RESULT "):])
