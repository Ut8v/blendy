"""Ingest: runs ONCE per asset inside Blender and writes the profile that every
later build looks up (hard rule 8).

    blender -b -P compiler/ingest.py -- --file <asset> --hash <h> --source X --ref Y
        --profile-out <json> [--class character|prop|environment|set_dressing]
        [--skeleton mixamo --retarget-profile mixamo_default] [--views-dir <dir>]
        [--raycast '{"view":"front","u":0.5,"v":0.4,"name":"seat"}' ...]

Stages: normalize, measure, classify, landmarks (characters: from the skeleton
profile; props: sockets from raycasts the agent requested after looking at the
six views), validate (every landmark must sit on or near the surface).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import bmesh                                   # noqa: E402
import bpy                                     # noqa: E402
from mathutils import Matrix, Vector           # noqa: E402

from compiler import scene as sc               # noqa: E402
from compiler.refs import CHARACTER_CORE_LANDMARKS, PROFILE_VERSION  # noqa: E402
from compiler.resolvers.assets import group_under_root, import_file  # noqa: E402
from compiler.resolvers.rigs import character_height, character_landmarks, load_skeleton_profile  # noqa: E402

VIEWS = {  # name -> (direction the camera looks along, up)
    "front": ((0, 1, 0), (0, 0, 1)), "back": ((0, -1, 0), (0, 0, 1)),
    "left": ((1, 0, 0), (0, 0, 1)), "right": ((-1, 0, 0), (0, 0, 1)),
    "top": ((0, 0, -1), (0, 1, 0)), "bottom": ((0, 0, 1), (0, -1, 0)),
}
SURFACE_TOLERANCE = 0.03      # meters; landmarks farther than this from the mesh are rejected
GENERATED = {"meshy", "tripo"}


# --- 1. normalize ------------------------------------------------------------------

def normalize(root, meshes, source: str, skeleton_profile: dict | None) -> dict[str, Any]:
    """Scale to meters, put the origin at ground contact under the bounds center.
    The FBX/glTF importers already convert to Z-up. Returns the correction."""
    scale = 1.0
    if skeleton_profile and skeleton_profile.get("import", {}).get("scale"):
        # Importers apply FBX unit scale already; only correct if it is clearly off.
        h = _height(meshes)
        if h > 20:            # centimetres came through as metres
            scale = skeleton_profile["import"]["scale"]
    else:
        h = _height(meshes)
        if h > 200:           # a prop taller than 200m is in cm or mm
            scale = 0.01 if h < 20000 else 0.001
    lo, hi = _bounds(meshes)
    center = (lo + hi) / 2
    location = (-center.x * scale, -center.y * scale, -lo.z * scale)
    return {"scale": scale, "rotation_euler": [0.0, 0.0, 0.0], "location": list(location)}


def _bounds(meshes):
    lo, hi = Vector((1e9,) * 3), Vector((-1e9,) * 3)
    for obj in meshes:
        for c in obj.bound_box:
            p = obj.matrix_world @ Vector(c)
            lo, hi = Vector(map(min, lo, p)), Vector(map(max, hi, p))
    return lo, hi


def _height(meshes) -> float:
    lo, hi = _bounds(meshes)
    return float(hi.z - lo.z)


# --- 2. measure ----------------------------------------------------------------------

def measure(meshes, norm: dict[str, Any]) -> dict[str, Any]:
    lo, hi = _bounds(meshes)
    s = norm["scale"]
    lo, hi = lo * s + Vector(norm["location"]), hi * s + Vector(norm["location"])
    polys = sum(len(m.data.polygons) for m in meshes)
    manifold = True
    has_uv = any(bool(m.data.uv_layers) for m in meshes)
    for m in meshes:
        bm = bmesh.new()
        bm.from_mesh(m.data)
        if any(not e.is_manifold for e in bm.edges):
            manifold = False
        bm.free()
        if not manifold:
            break
    return {"bbox_min": list(lo), "bbox_max": list(hi), "dimensions": list(hi - lo),
            "origin_offset": [0.0, 0.0, 0.0], "poly_count": polys, "manifold": manifold,
            "has_uv": has_uv, "mesh_objects": len(meshes)}


# --- 3. classify -----------------------------------------------------------------------

def classify(measure_: dict[str, Any], has_armature: bool, override: str | None) -> str:
    if override:
        return override
    if has_armature:
        return "character"
    dims = measure_["dimensions"]
    footprint = max(dims[0], dims[1])
    if footprint > 8.0:
        return "environment"
    if footprint > 1.5 or dims[2] > 1.5:
        return "set_dressing"
    return "prop"


# --- 4a. six orthographic views -----------------------------------------------------

def render_views(scene, meshes, out_dir: str, size: int = 512) -> dict[str, dict[str, Any]]:
    """Plain-background orthographic views. Each entry records the view's frame so a
    2D (u, v) in the image can be turned back into a ray (raycast_socket)."""
    os.makedirs(out_dir, exist_ok=True)
    lo, hi = _bounds(meshes)
    center, extent = (lo + hi) / 2, max((hi - lo).length, 0.1)
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "SINGLE"
    scene.display.shading.show_cavity = True
    scene.render.resolution_x = scene.render.resolution_y = size
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    if scene.world is None:
        scene.world = bpy.data.worlds.new("IngestWorld")
    scene.world.color = (0.9, 0.9, 0.9)
    cam_data = bpy.data.cameras.new("INGEST_CAM")
    cam_data.type, cam_data.ortho_scale = "ORTHO", extent * 1.2
    cam_data.clip_end = extent * 10 + 10
    cam = bpy.data.objects.new("INGEST_CAM", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    views = {}
    for name, (direction, up) in VIEWS.items():
        d, u = Vector(direction), Vector(up)
        cam.location = center - d * extent * 2
        cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler("XYZ") if name not in ("top", "bottom") \
            else (-d).to_track_quat("Z", "Y").to_euler("XYZ")
        if name in ("top", "bottom"):
            cam.rotation_euler = (0.0, 0.0, 0.0) if name == "top" else (math.pi, 0.0, 0.0)
        path = os.path.join(out_dir, f"{name}.png")
        scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        views[name] = {"image": path, "origin": list(cam.location), "direction": list(d),
                       "matrix": [list(row) for row in cam.matrix_world],
                       "ortho_scale": cam_data.ortho_scale, "size": size}
    bpy.data.objects.remove(cam, do_unlink=True)
    return views


# --- 4b. sockets from raycasts ---------------------------------------------------------

def raycast_socket(scene, view: dict[str, Any], u: float, v: float) -> dict[str, Any] | None:
    """(u, v) in [0,1] image coordinates (origin top-left) -> surface point + normal,
    or None when the ray misses. A miss is a failed landmark, not a landmark in midair."""
    m = Matrix(view["matrix"])
    half = view["ortho_scale"] / 2
    local = Vector(((u - 0.5) * 2 * half, (0.5 - v) * 2 * half, 0.0))
    origin = m @ local
    direction = (m.to_3x3() @ Vector((0, 0, -1))).normalized()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    hit, location, normal, _, obj, _ = scene.ray_cast(depsgraph, origin, direction)
    if not hit:
        return None
    return {"position": list(location), "normal": list(normal), "object": obj.name}


def near_surface(scene, point: Vector, tol: float = SURFACE_TOLERANCE) -> bool:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    best = None
    for obj in scene.objects:
        if obj.type != "MESH":
            continue
        ev = obj.evaluated_get(depsgraph)
        ok, loc, _, _ = ev.closest_point_on_mesh(ev.matrix_world.inverted() @ point)
        if ok:
            d = (ev.matrix_world @ loc - point).length
            best = d if best is None else min(best, d)
    return best is not None and best <= tol


# --- entry -------------------------------------------------------------------------

def ingest(args) -> dict[str, Any]:
    scene = sc.reset()
    objects = import_file(args.file, "ingest")
    for obj in objects:
        if obj.name not in scene.collection.objects:
            try:
                scene.collection.objects.link(obj)
            except RuntimeError:
                pass
    root = group_under_root("ingest_root", objects)
    if root not in objects:
        scene.collection.objects.link(root)
    meshes = [o for o in objects if o.type == "MESH"]
    arm = next((o for o in objects if o.type == "ARMATURE"), None)
    if not meshes:
        raise RuntimeError("asset contains no mesh objects")

    skel = load_skeleton_profile(args.retarget_profile) if args.retarget_profile else None
    norm = normalize(root, meshes, args.source, skel)
    root.matrix_world = Matrix.LocRotScale(Vector(norm["location"]), None,
                                           Vector((norm["scale"],) * 3)) @ root.matrix_world
    bpy.context.view_layer.update()
    meas = measure(meshes, {"scale": 1.0, "location": [0, 0, 0]})
    klass = classify(meas, arm is not None, args.klass)

    profile: dict[str, Any] = {
        "profile_version": PROFILE_VERSION, "hash": args.hash, "source": args.source,
        "ref": args.ref, "class": klass, "normalize": norm, "measure": meas,
        "flags": {"generated": args.source in GENERATED, "rig_ok": args.source not in GENERATED,
                  "has_armature": arm is not None},
        "landmarks": {}, "views": {}, "rejected_landmarks": {},
    }
    if klass == "character":
        if arm is None or skel is None:
            raise RuntimeError("character ingest needs an armature and --retarget-profile "
                               "(auto-rig first: Mixamo / Auto-Rig Pro)")
        profile["skeleton"] = skel["skeleton"]
        profile["landmarks"] = character_landmarks(arm, skel, ground_z=0.0)
        profile["height"] = character_height(arm, meshes)
        if skel.get("visemes"):
            face = next((m for m in meshes if m.data.shape_keys), None)
            if face is not None:
                have = set(face.data.shape_keys.key_blocks.keys())
                profile["visemes"] = {k: v for k, v in skel["visemes"].items()
                                      if k != "note" and (v is None or v in have)}
        missing = [n for n in CHARACTER_CORE_LANDMARKS if n not in profile["landmarks"]]
        if missing:
            raise RuntimeError(f"character profile incomplete, cannot map: {', '.join(missing)}")
    if args.views_dir:
        profile["views"] = render_views(scene, meshes, args.views_dir)
    for req in args.raycast or []:
        r = json.loads(req)
        view = profile["views"].get(r["view"]) if profile["views"] else None
        if view is None:
            raise RuntimeError(f"raycast '{r['name']}': no view '{r['view']}' rendered")
        hit = raycast_socket(scene, view, r["u"], r["v"])
        if hit is None:
            profile["rejected_landmarks"][r["name"]] = "ray missed geometry"
            continue
        p = Vector(hit["position"])
        if not near_surface(scene, p):
            profile["rejected_landmarks"][r["name"]] = "hit is not on the surface"
            continue
        local = root.matrix_world.inverted() @ p
        n_local = (root.matrix_world.to_3x3().inverted() @ Vector(hit["normal"])).normalized()
        profile["landmarks"][r["name"]] = {"kind": "socket", "position": list(local),
                                           "normal": list(n_local), "view": r["view"]}
    return profile


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    p = argparse.ArgumentParser(prog="ingest.py")
    p.add_argument("--file", required=True)
    p.add_argument("--hash", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--ref", required=True)
    p.add_argument("--profile-out", required=True)
    p.add_argument("--class", dest="klass", default=None)
    p.add_argument("--retarget-profile", default=None)
    p.add_argument("--views-dir", default=None)
    p.add_argument("--raycast", action="append", default=[])
    args = p.parse_args(argv)
    try:
        profile = ingest(args)
        os.makedirs(os.path.dirname(args.profile_out) or ".", exist_ok=True)
        with open(args.profile_out, "w", encoding="utf-8") as fh:
            json.dump(profile, fh, indent=2)
        print("BLENDY_RESULT " + json.dumps({"ok": True, "class": profile["class"],
                                             "landmarks": sorted(profile["landmarks"])}))
        return 0
    except Exception as e:  # noqa: BLE001
        print("BLENDY_RESULT " + json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}))
        return 1


if __name__ == "__main__":
    code = main()
    if "--" in sys.argv:
        sys.exit(code)
