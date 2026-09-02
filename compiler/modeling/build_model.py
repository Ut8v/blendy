"""Build a model recipe into objects, write its profile, render its turntable.

    blender -b -P compiler/modeling/build_model.py -- --model models/haldin.json
        [--profile-out profiles/models/haldin.json] [--preview-dir preview/models/haldin]
        [--views front,side,back,three_quarter,head] [--quality fast|lookdev]

instantiate(ctx, model, prefix) is what the shot compiler calls for
{"source": "model"} assets: same code, same result, every time.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import bpy                                                   # noqa: E402
from mathutils import Euler, Matrix, Vector                  # noqa: E402

from compiler.issues import SpecValidationError              # noqa: E402
from compiler.modeling.builders import BUILDERS, metaball_to_mesh, mirror_x   # noqa: E402
from compiler.modeling.materials import build_material       # noqa: E402
from compiler.modeling.modifiers import apply_modifiers      # noqa: E402
from compiler.refs import PROFILE_VERSION                    # noqa: E402
from compiler.validate import load_json                      # noqa: E402
from compiler.validate_model import validate_model           # noqa: E402

MODELS_DIR = _ROOT / "models"
MODEL_PROFILES_DIR = _ROOT / "profiles" / "models"
MODEL_PREVIEW_DIR = _ROOT / "preview" / "models"
DEFAULT_VIEWS = ["front", "three_quarter", "side", "back", "head"]


def model_path(model_id: str) -> Path:
    return MODELS_DIR / f"{model_id}.json"


def _ordered(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Parents before children; mirror sources before their mirrors."""
    by_id = {p["id"]: p for p in parts}
    done: list[str] = []
    seen: set[str] = set()

    def visit(pid: str) -> None:
        if pid in seen:
            return
        p = by_id[pid]
        if p["parent"]:
            visit(p["parent"])
        if p.get("mirror_of"):
            visit(p["mirror_of"])
        seen.add(pid)
        done.append(pid)
    for p in parts:
        visit(p["id"])
    return [by_id[i] for i in done]


def instantiate(model: dict[str, Any], prefix: str, link) -> tuple[bpy.types.Object, dict[str, bpy.types.Object]]:
    """Build every part under one root empty. `link(obj)` puts an object in a
    collection. Returns (root, {part_id: object})."""
    root = bpy.data.objects.new(prefix, None)
    root.empty_display_type, root.empty_display_size = "PLAIN_AXES", 0.2
    link(root)
    materials = {name: build_material(f"{prefix}.{name}", spec) for name, spec in model["materials"].items()}
    objects: dict[str, bpy.types.Object] = {}
    for part in _ordered(model["parts"]):
        name = f"{prefix}.{part['id']}"
        if part.get("mirror_of"):
            obj = mirror_x(objects[part["mirror_of"]], name)
        else:
            obj = BUILDERS[part["op"]](name, part["params"], part.get("smooth"), objects)
        link(obj)
        if obj.type == "META":
            frozen = metaball_to_mesh(obj)
            bpy.data.objects.remove(obj, do_unlink=True)
            obj = frozen
            link(obj)
        obj.parent = objects[part["parent"]] if part["parent"] else root
        obj.matrix_parent_inverse = Matrix.Identity(4)
        t = part["transform"]
        obj.location = Vector(t["location"])
        obj.rotation_mode = "XYZ"
        obj.rotation_euler = Euler(t["rotation_euler"], "XYZ")
        obj.scale = Vector(t["scale"])
        if part["material"] and obj.type == "MESH":
            obj.data.materials.append(materials[part["material"]])
        obj["blendy_part"] = part["id"]
        objects[part["id"]] = obj
    bpy.context.view_layer.update()
    for part in model["parts"]:                      # `at` placement, after every part exists
        at = part.get("at")
        if not at:
            continue
        src = objects[at["part"]]
        points = src.get("blendy_points") or {}
        if at["point"] not in points:
            raise RuntimeError(f"part '{part['id']}': '{at['part']}' publishes no point "
                               f"'{at['point']}' (have: {', '.join(sorted(points)) or 'none'})")
        obj = objects[part["id"]]
        world = src.matrix_world @ Vector(points[at["point"]])
        space = obj.parent.matrix_world.inverted() if obj.parent else Matrix.Identity(4)
        obj.location = (space @ world) + Vector(at.get("offset", (0, 0, 0)))
    bpy.context.view_layer.update()
    for part in model["parts"]:
        if part["modifiers"] and objects[part["id"]].type == "MESH":
            apply_modifiers(objects[part["id"]], part["modifiers"], objects)
    bpy.context.view_layer.update()
    return root, objects


# --- landmarks and measurement --------------------------------------------------------

def _world_bounds(obj: bpy.types.Object) -> tuple[Vector, Vector]:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(depsgraph)
    pts = [ev.matrix_world @ Vector(c) for c in ev.bound_box]
    return Vector(map(min, *pts)), Vector(map(max, *pts))


def resolve_landmarks(model: dict[str, Any], root: bpy.types.Object,
                      objects: dict[str, bpy.types.Object]) -> dict[str, dict[str, Any]]:
    """Recipe landmarks -> socket entries in ROOT-local space (meters), so a shot
    can anchor to them and the model can be placed anywhere."""
    inv = root.matrix_world.inverted()
    out: dict[str, dict[str, Any]] = {}
    for name, lm in model["landmarks"].items():
        if "position" in lm:
            out[name] = {"kind": "socket", "position": list(lm["position"]), "normal": list(lm.get("normal", (0, 0, 1)))}
            continue
        obj = objects[lm["part"]]
        anchor = lm["anchor"]
        normal = Vector((0, 0, 1))
        if anchor.startswith("point:"):
            points = obj.get("blendy_points") or {}
            pname = anchor[6:]
            if pname not in points:
                raise RuntimeError(f"landmark '{name}': part '{lm['part']}' publishes no point "
                                   f"'{pname}' (have: {', '.join(sorted(points)) or 'none'})")
            pos = obj.matrix_world @ Vector(points[pname])
        elif anchor.startswith("joint:"):
            src = model_part(model, lm["part"])
            joint = src["params"]["joints"][anchor[6:]]
            pos = obj.matrix_world @ Vector(joint["position"])
        elif anchor == "origin":
            pos = obj.matrix_world.translation.copy()
        else:
            lo, hi = _world_bounds(obj)
            c = (lo + hi) / 2
            pos = {"center": c, "top": Vector((c.x, c.y, hi.z)), "bottom": Vector((c.x, c.y, lo.z)),
                   "front": Vector((c.x, lo.y, c.z)), "back": Vector((c.x, hi.y, c.z)),
                   "left": Vector((lo.x, c.y, c.z)), "right": Vector((hi.x, c.y, c.z))}[anchor]
            normal = {"top": Vector((0, 0, 1)), "bottom": Vector((0, 0, -1)), "front": Vector((0, -1, 0)),
                      "back": Vector((0, 1, 0)), "left": Vector((-1, 0, 0)), "right": Vector((1, 0, 0))}.get(anchor, normal)
        pos = pos + Vector(lm.get("offset", (0, 0, 0)))
        out[name] = {"kind": "socket", "position": list(inv @ pos), "normal": list(normal), "part": lm["part"]}
    return out


def model_part(model: dict[str, Any], pid: str) -> dict[str, Any]:
    p = next(x for x in model["parts"] if x["id"] == pid)
    return model_part(model, p["mirror_of"]) if p.get("mirror_of") else p


def measure(objects: dict[str, bpy.types.Object]) -> dict[str, Any]:
    los, his, polys = [], [], 0
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for obj in objects.values():
        if obj.type != "MESH":
            continue
        lo, hi = _world_bounds(obj)
        los.append(lo); his.append(hi)
        polys += len(obj.evaluated_get(depsgraph).data.polygons)
    if not los:
        return {"bbox_min": [0, 0, 0], "bbox_max": [0, 0, 0], "dimensions": [0, 0, 0], "poly_count": 0}
    lo = Vector((min(v.x for v in los), min(v.y for v in los), min(v.z for v in los)))
    hi = Vector((max(v.x for v in his), max(v.y for v in his), max(v.z for v in his)))
    return {"bbox_min": list(lo), "bbox_max": list(hi), "dimensions": list(hi - lo), "poly_count": polys}


def write_profile(model: dict[str, Any], root, objects, out_path: Path) -> dict[str, Any]:
    meas = measure(objects)
    profile = {"profile_version": PROFILE_VERSION, "model_id": model["id"], "source": "model",
               "ref": model["id"], "class": model["kind"], "measure": meas,
               "height": meas["dimensions"][2], "declared_height": model.get("height"),
               "landmarks": resolve_landmarks(model, root, objects),
               "parts": [p["id"] for p in model["parts"]],
               "flags": {"generated": False, "rig_ok": True, "has_armature": model.get("skeleton") is not None},
               "built_at": time.time()}
    os.makedirs(out_path.parent, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(profile, fh, indent=2)
    return profile


# --- CLI -----------------------------------------------------------------------------

def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    p = argparse.ArgumentParser(prog="build_model.py")
    p.add_argument("--model", required=True)
    p.add_argument("--profile-out", default=None)
    p.add_argument("--preview-dir", default=None)
    p.add_argument("--views", default=",".join(DEFAULT_VIEWS))
    p.add_argument("--quality", default="lookdev", choices=["fast", "lookdev"])
    p.add_argument("--out", default=None, help="save the built model as a .blend")
    a = p.parse_args(argv)
    try:
        model = load_json(a.model)
        result = validate_model(model)
        if not result.ok:
            raise SpecValidationError(result)
        from compiler import scene as sc
        from compiler.modeling import turntable
        scn = sc.reset()
        coll = bpy.data.collections.new("Model")
        scn.collection.children.link(coll)
        root, objects = instantiate(model, model["id"], coll.objects.link)
        prof_path = Path(a.profile_out) if a.profile_out else MODEL_PROFILES_DIR / f"{model['id']}.json"
        profile = write_profile(model, root, objects, prof_path)
        outputs = []
        if a.preview_dir:
            outputs = turntable.render(scn, model, objects, a.preview_dir, a.views.split(","), a.quality,
                                       landmarks=profile["landmarks"], root=root)
        if a.out:
            sc.save_blend(a.out)
        print("BLENDY_RESULT " + json.dumps({"ok": True, "profile": str(prof_path), "outputs": outputs,
                                             "height": profile["height"], "poly_count": profile["measure"]["poly_count"],
                                             "landmarks": sorted(profile["landmarks"]),
                                             "warnings": [str(w) for w in result.warnings]}))
        return 0
    except SpecValidationError as e:
        print("BLENDY_RESULT " + json.dumps({"ok": False, "stage": "validate", "error": e.result.format()}))
        return 1
    except Exception as e:  # noqa: BLE001
        import traceback
        print("BLENDY_RESULT " + json.dumps({"ok": False, "stage": "build", "error": f"{type(e).__name__}: {e}",
                                             "trace": traceback.format_exc()[-1500:]}))
        return 1


if __name__ == "__main__":
    code = main()
    if "--" in sys.argv:
        sys.exit(code)
