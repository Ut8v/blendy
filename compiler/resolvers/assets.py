"""Asset resolver: primitives via bmesh, files via the importers, both normalized
from the asset profile. The compiler never downloads; the server hands it paths.
"""

from __future__ import annotations

import math
import os
from typing import Any

import bmesh
import bpy
from mathutils import Euler, Matrix, Vector

from ..refs import PRIMITIVE_NAMES, ROOT

LOCAL_ASSET_DIR = ROOT / "assets"


# --- primitives ----------------------------------------------------------------------

def make_primitive(name: str, kind: str) -> bpy.types.Object:
    """Unit primitives (radius/half-size 1) so scale in the spec is the size in meters."""
    if kind == "empty":
        obj = bpy.data.objects.new(name, None)
        obj.empty_display_type, obj.empty_display_size = "PLAIN_AXES", 0.5
        return obj
    if kind not in PRIMITIVE_NAMES:
        raise RuntimeError(f"unknown primitive '{kind}'; known: {', '.join(PRIMITIVE_NAMES)}")
    bm = bmesh.new()
    if kind == "plane":
        bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=1.0)
    elif kind == "cube":
        bmesh.ops.create_cube(bm, size=2.0)
    elif kind == "uv_sphere":
        bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, radius=1.0)
    elif kind == "ico_sphere":
        bmesh.ops.create_icosphere(bm, subdivisions=3, radius=1.0)
    elif kind == "cylinder":
        bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=32,
                              radius1=1.0, radius2=1.0, depth=2.0)
    elif kind == "cone":
        bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=32,
                              radius1=1.0, radius2=0.0, depth=2.0)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    for poly in mesh.polygons:
        poly.use_smooth = kind in ("uv_sphere", "ico_sphere", "cylinder", "cone")
    return bpy.data.objects.new(name, mesh)


# --- file import -----------------------------------------------------------------------

def _import_with_override(op, **kwargs):
    """Run an importer with an explicit context so it does not depend on the UI."""
    with bpy.context.temp_override(**_override()):
        result = op(**kwargs)
    if "FINISHED" not in result:
        raise RuntimeError(f"importer returned {result}")


def _override() -> dict[str, Any]:
    wm = bpy.context.window_manager
    window = wm.windows[0] if wm.windows else None
    ov: dict[str, Any] = {"scene": bpy.context.scene}
    if window is not None:
        ov["window"] = window
        ov["screen"] = window.screen
        ov["area"] = window.screen.areas[0] if window.screen.areas else None
    return {k: v for k, v in ov.items() if v is not None}


def import_file(path: str, name_prefix: str) -> list[bpy.types.Object]:
    """Import an FBX / glTF / OBJ / .blend collection and return the new objects,
    all prefixed so two copies of one asset never collide."""
    before = set(bpy.data.objects)
    ext = os.path.splitext(path)[1].lower()
    if ext == ".fbx":
        _import_with_override(bpy.ops.import_scene.fbx, filepath=path,
                              automatic_bone_orientation=False, ignore_leaf_bones=False,
                              use_anim=False)
    elif ext in (".glb", ".gltf"):
        _import_with_override(bpy.ops.import_scene.gltf, filepath=path)
    elif ext == ".obj":
        _import_with_override(bpy.ops.wm.obj_import, filepath=path)
    elif ext == ".usd" or ext == ".usdc" or ext == ".usdz":
        _import_with_override(bpy.ops.wm.usd_import, filepath=path)
    elif ext == ".blend":
        with bpy.data.libraries.load(path, link=False) as (src, dst):
            dst.objects = list(src.objects)
        for obj in dst.objects:
            bpy.context.scene.collection.objects.link(obj)
    else:
        raise RuntimeError(f"cannot import '{path}': unsupported extension {ext}")
    new = [o for o in bpy.data.objects if o not in before]
    for obj in new:
        obj.name = f"{name_prefix}.{obj.name}"
    return new


def group_under_root(name: str, objects: list[bpy.types.Object]) -> bpy.types.Object:
    """Parent the import's top-level objects to one empty so the asset has a single
    transform, and return that empty. An armature already acting as root is reused."""
    tops = [o for o in objects if o.parent is None or o.parent not in objects]
    if len(tops) == 1 and tops[0].type in ("ARMATURE", "EMPTY", "MESH"):
        root = tops[0]
        root.name = name
        return root
    root = bpy.data.objects.new(name, None)
    root.empty_display_type, root.empty_display_size = "PLAIN_AXES", 0.25
    for obj in tops:
        obj.parent = root
    return root


# --- normalization and transform -----------------------------------------------------

def normalization_matrix(profile: dict[str, Any] | None) -> Matrix:
    """The ingest-time correction that makes the asset Z-up, meters, origin at
    ground contact. Applied identically on every build (hard rule 8)."""
    if not profile:
        return Matrix.Identity(4)
    n = profile.get("normalize", {})
    loc = Vector(n.get("location", (0, 0, 0)))
    rot = Euler(n.get("rotation_euler", (0, 0, 0)), "XYZ")
    scale = n.get("scale", 1.0)
    scale_v = Vector((scale, scale, scale)) if isinstance(scale, (int, float)) else Vector(scale)
    return Matrix.LocRotScale(loc, rot, scale_v)


def apply_transform(obj: bpy.types.Object, transform: dict[str, Any],
                    location_override: Vector | None = None) -> None:
    loc = location_override if location_override is not None else Vector(transform["location"])
    obj.location = loc
    obj.rotation_mode = "XYZ"
    obj.rotation_euler = Euler(transform["rotation_euler"], "XYZ")
    obj.scale = Vector(transform["scale"])


# --- entry point -----------------------------------------------------------------------

def resolve_local_path(ref: str) -> str:
    path = ref if os.path.isabs(ref) else str(LOCAL_ASSET_DIR / ref)
    if not os.path.exists(path):
        raise RuntimeError(f"local asset '{ref}' not found at {path}")
    return path


def build_asset(ctx, asset: dict[str, Any]) -> bpy.types.Object:
    """Create the asset's objects, normalize, place. Landmark-anchored placement is
    finished later by the landmarks resolver, once every asset exists."""
    aid = asset["id"]
    resolved = ctx.resolved.get(aid, {})
    profile = resolved.get("profile") or ctx.profiles.profile_for(asset)

    if asset["source"] == "primitive":
        root = make_primitive(aid, asset["ref"])
        objects = [root]
        ctx.link(root, "Assets")
    else:
        path = resolved.get("path")
        if not path and asset["source"] == "local":
            path = resolve_local_path(asset["ref"])
        if not path:
            raise RuntimeError(f"asset '{aid}' ({asset['source']}:{asset['ref']}) is not "
                               "resolved to a file; run resolve_asset first")
        objects = import_file(path, aid)
        for obj in objects:
            for coll in list(obj.users_collection):
                coll.objects.unlink(obj)
            ctx.link(obj, "Assets")
        root = group_under_root(aid, objects)
        if root not in objects:
            ctx.link(root, "Assets")
            objects.append(root)
        # Bake the ingest normalization into a parent so the spec transform stays clean.
        norm = normalization_matrix(profile)
        if norm != Matrix.Identity(4):
            holder = bpy.data.objects.new(f"{aid}.norm", None)
            holder.empty_display_size = 0.1
            ctx.link(holder, "Helpers")
            holder.matrix_world = norm
            root.parent = holder
            root = holder

    from .materials import apply_overrides
    if asset.get("material_overrides"):
        apply_overrides(aid, objects, asset["material_overrides"], ctx.warn)

    t = asset["transform"]
    if isinstance(t["location"], str) or t.get("anchor"):
        apply_transform(root, t, location_override=Vector((0, 0, 0)))   # placed later
    else:
        apply_transform(root, t)

    ctx.register(aid, root)
    ctx.meshes[aid] = [o for o in objects if o.type == "MESH"]
    arm = next((o for o in objects if o.type == "ARMATURE"), None)
    if arm is not None:
        ctx.armatures[f"asset:{aid}"] = arm
    return root
