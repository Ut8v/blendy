"""Compiler entry point. Runs INSIDE Blender.

    blender -b -P compiler/build.py -- --spec <path> --out <blend> --mode build
                                       [--resolved <json>] [--result <json>]
                                       [--quality fast|lookdev] [--angles a,b,c]
                                       [--frames 1-48] [--engine X]

build(spec_path, output_blend_path, mode) -> BuildResult

Order is fixed (CLAUDE.md, The compiler). Validation runs first and nothing
touches the scene until it passes. Every failure names the entity id and stage.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from compiler.issues import SpecValidationError            # noqa: E402
from compiler.refs import ProfileIndex                     # noqa: E402
from compiler.validate import load_json, require_valid     # noqa: E402


@dataclass
class BuildResult:
    ok: bool
    mode: str
    stage: str
    blend_path: str | None = None
    outputs: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    entity_id: str | None = None
    fingerprint: str | None = None
    seconds: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


class BuildError(RuntimeError):
    def __init__(self, stage: str, message: str, entity_id: str | None = None):
        super().__init__(message)
        self.stage, self.entity_id = stage, entity_id


def _stage(name: str, entity_id: str | None, fn, *args):
    try:
        return fn(*args)
    except BuildError:
        raise
    except Exception as e:  # noqa: BLE001 - re-wrapped with stage + id for the agent
        raise BuildError(name, f"{type(e).__name__}: {e}", entity_id) from e


def compile_scene(spec: dict[str, Any], resolved: dict[str, Any], profiles: ProfileIndex,
                  engine_override: str | None = None):
    """Steps 2..9. Returns the BuildContext."""
    from compiler import scene as sc
    from compiler.resolvers import animation, assets, cameras, landmarks, lights, lipsync, rigs

    ctx = sc.BuildContext(spec, resolved, profiles)
    ctx.scene = _stage("reset", None, sc.reset)
    _stage("reset", None, sc.make_collections, ctx)
    _stage("timing", None, sc.set_timing, ctx.scene, spec["meta"])
    hdri_path = resolved.get("__world__", {}).get("path")
    _stage("world", None, sc.build_world, ctx, hdri_path)

    for asset in spec["assets"]:
        _stage("assets", asset["id"], assets.build_asset, ctx, asset)
    for rig in spec["rigs"]:
        _stage("rigs", rig["id"], rigs.bind_rig, ctx, rig)
    for anim in spec["animation"]:
        _stage("animation", anim["id"], animation.apply_clip, ctx, anim)
    for cam in spec["cameras"]:
        _stage("cameras", cam["id"], cameras.build_camera, ctx, cam)
    for light in spec["lights"]:
        _stage("lights", light["id"], lights.build_light, ctx, light)

    # Landmarks are live only now: assets exist, rigs are bound, clips are on.
    _stage("landmarks", None, landmarks.place_anchored, ctx)
    for cam in spec["cameras"]:
        _stage("cameras", cam["id"], cameras.finish_camera, ctx, cam)
    for light in spec["lights"]:
        _stage("lights", light["id"], lights.finish_light, ctx, light)

    for entry in spec.get("audio", []):
        _stage("audio", entry["id"], lipsync.add_audio, ctx, entry)
    seq = spec.get("sequence")
    if seq and seq.get("dialogue"):
        cast_rig = {c["cast_id"]: c["rig_id"] for c in seq["cast"]}
        for line in seq["dialogue"]:
            _stage("lipsync", line["line_id"], lipsync.apply_dialogue_line, ctx, line,
                   cast_rig[line["cast_id"]])

    _stage("render_settings", None, sc.apply_render_settings, ctx.scene, spec["render"],
           engine_override)
    ctx.scene.camera = ctx.objects[spec["render"]["camera"]]
    ctx.scene.frame_set(spec["meta"]["frame_start"])
    return ctx


def build(spec_path: str, output_blend_path: str | None, mode: str = "build",
          resolved_path: str | None = None, **opts) -> BuildResult:
    t0 = time.time()
    result = BuildResult(ok=False, mode=mode, stage="validate")
    try:
        spec = load_json(spec_path)
        profiles = ProfileIndex.load()
        try:
            validation = require_valid(spec, profiles=profiles)
        except SpecValidationError as e:
            first = e.result.errors[0]
            result.error, result.entity_id = e.result.format(), first.entity_id
            return result
        result.warnings.extend(str(w) for w in validation.warnings)
        resolved = load_json(resolved_path) if resolved_path else {}

        result.stage = "compile"
        ctx = compile_scene(spec, resolved, profiles, opts.get("engine"))
        result.warnings.extend(ctx.warnings)

        from compiler import fingerprint
        result.fingerprint = fingerprint.scene_fingerprint(ctx.scene)

        if output_blend_path:
            result.stage = "save"
            from compiler.scene import save_blend
            save_blend(output_blend_path)
            result.blend_path = output_blend_path

        if mode == "preview":
            result.stage = "preview"
            from compiler import preview
            result.outputs = preview.render_preview(
                ctx, quality=opts.get("quality", "fast"),
                angles=opts.get("angles") or preview.DEFAULT_ANGLES,
                out_dir=opts.get("out_dir"), frame=opts.get("frame"))
        elif mode == "final":
            result.stage = "final"
            from compiler import render
            result.outputs, result.extra = render.render_frames(
                ctx, frames=opts.get("frames"), out_dir=opts.get("out_dir"))
        elif mode == "proxy":
            result.stage = "proxy"
            from compiler import proxy
            result.outputs = [proxy.export_proxy(ctx, opts["out_path"])]
        elif mode not in ("build", "fingerprint"):
            raise BuildError("mode", f"unknown mode '{mode}'")

        result.ok, result.stage = True, "done"
    except BuildError as e:
        result.error, result.stage, result.entity_id = str(e), e.stage, e.entity_id
    except Exception as e:  # noqa: BLE001
        result.error = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
    finally:
        result.seconds = round(time.time() - t0, 3)
    return result


# --- CLI ----------------------------------------------------------------------------

def _parse(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="build.py")
    p.add_argument("--spec", required=True)
    p.add_argument("--out", default=None, help="output .blend (fresh path, never one that is open)")
    p.add_argument("--mode", default="build",
                   choices=["build", "preview", "final", "proxy", "fingerprint"])
    p.add_argument("--resolved", default=None, help="asset_id -> {path, profile} JSON from the server")
    p.add_argument("--result", default=None, help="write BuildResult JSON here")
    p.add_argument("--quality", default="fast", choices=["fast", "lookdev"])
    p.add_argument("--angles", default=None, help="comma list: camera,top,three_quarter")
    p.add_argument("--frames", default=None, help="final: a-b, inclusive")
    p.add_argument("--frame", type=int, default=None, help="preview: frame to render")
    p.add_argument("--out-dir", default=None)
    p.add_argument("--out-path", default=None)
    p.add_argument("--engine", default=None)
    return p.parse_args(argv)


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    args = _parse(argv)
    res = build(args.spec, args.out, args.mode, args.resolved,
                quality=args.quality,
                angles=args.angles.split(",") if args.angles else None,
                frames=args.frames, frame=args.frame, out_dir=args.out_dir,
                out_path=args.out_path, engine=args.engine)
    text = res.to_json()
    if args.result:
        os.makedirs(os.path.dirname(args.result) or ".", exist_ok=True)
        with open(args.result, "w", encoding="utf-8") as fh:
            fh.write(text)
    print("BLENDY_RESULT " + json.dumps({"ok": res.ok, "stage": res.stage, "error": res.error,
                                         "entity_id": res.entity_id}))
    return 0 if res.ok else 1


if __name__ == "__main__":
    code = main()
    if "--" in sys.argv:          # headless invocation: propagate the exit code
        sys.exit(code)
