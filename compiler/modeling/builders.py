"""Builder ops: recipe params -> a Blender object. bmesh where possible, so no
operator context is needed. Every builder is deterministic for its params.

Sizes are full dimensions in metres; the origin is the part's local origin.
"""

from __future__ import annotations

import math
from typing import Any

import bmesh
import bpy
from mathutils import Matrix, Vector


def _mesh_object(name: str, bm: bmesh.types.BMesh, smooth: bool) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    for poly in mesh.polygons:
        poly.use_smooth = smooth
    return bpy.data.objects.new(name, mesh)


# --- primitive -----------------------------------------------------------------------

def build_primitive(name: str, p: dict[str, Any], smooth: bool | None) -> bpy.types.Object:
    shape, size = p["shape"], Vector(p["size"])
    seg = int(p.get("segments", 32))
    bm = bmesh.new()
    if shape == "cube":
        bmesh.ops.create_cube(bm, size=1.0)
    elif shape == "sphere":
        bmesh.ops.create_uvsphere(bm, u_segments=seg, v_segments=max(3, seg // 2), radius=0.5)
    elif shape == "cylinder":
        bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=seg, radius1=0.5, radius2=0.5, depth=1.0)
    elif shape == "cone":
        bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=seg, radius1=0.5, radius2=0.0, depth=1.0)
    elif shape == "torus":
        # major diameter = size.x, minor diameter = size.z; scaled after
        _torus(bm, 0.5, 0.5 * (size.z / size.x if size.x else 0.25), seg, max(6, seg // 2))
        size = Vector((size.x, size.y, size.x))
    elif shape == "plane":
        bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=0.5)
        size = Vector((size.x, size.y, 1.0))
    bmesh.ops.scale(bm, vec=size, verts=bm.verts)
    return _mesh_object(name, bm, smooth if smooth is not None else shape in ("sphere", "cylinder", "cone", "torus"))


def _torus(bm, major: float, minor: float, seg_major: int, seg_minor: int) -> None:
    verts = []
    for i in range(seg_major):
        a = 2 * math.pi * i / seg_major
        ring = []
        for j in range(seg_minor):
            b = 2 * math.pi * j / seg_minor
            r = major + minor * math.cos(b)
            ring.append(bm.verts.new((r * math.cos(a), r * math.sin(a), minor * math.sin(b))))
        verts.append(ring)
    for i in range(seg_major):
        for j in range(seg_minor):
            a, b = verts[i][j], verts[i][(j + 1) % seg_minor]
            c, d = verts[(i + 1) % seg_major][(j + 1) % seg_minor], verts[(i + 1) % seg_major][j]
            bm.faces.new((a, b, c, d))


# --- skin: joint graph -> Skin modifier ------------------------------------------------

def build_skin(name: str, p: dict[str, Any], smooth: bool | None) -> bpy.types.Object:
    """Vertices at joints, edges between them; the Skin modifier wraps them with
    the per-joint radius and Subdivision smooths the result. Bodies and limbs."""
    joints, edges = p["joints"], p["edges"]
    bm = bmesh.new()
    layer = bm.verts.layers.skin.verify()
    vmap = {}
    for jname in sorted(joints):
        j = joints[jname]
        v = bm.verts.new(j["position"])
        r = j["radius"]
        rx, ry = (r, r) if isinstance(r, (int, float)) else (r[0], r[1])
        v[layer].radius = (rx, ry)
        v[layer].use_loose = bool(j.get("loose", False))
        vmap[jname] = v
    bm.verts.ensure_lookup_table()
    for a, b in edges:
        bm.edges.new((vmap[a], vmap[b]))
    root = p.get("root") or sorted(joints)[0]
    vmap[root][layer].use_root = True
    obj = _mesh_object(name, bm, True)
    skin = obj.modifiers.new("skin", "SKIN")
    skin.use_smooth_shade = p.get("smooth_shading", True)
    levels = int(p.get("subdivision", 2))
    if levels > 0:
        sub = obj.modifiers.new("subdivision", "SUBSURF")
        sub.levels = sub.render_levels = levels
    return obj


# --- revolve: profile lathed around Z -----------------------------------------------

def build_revolve(name: str, p: dict[str, Any], smooth: bool | None) -> bpy.types.Object:
    profile, seg, cap = p["profile"], int(p.get("segments", 32)), p.get("cap", True)
    bm = bmesh.new()
    rings = []
    for r, z in profile:
        if r <= 1e-6:
            rings.append([bm.verts.new((0.0, 0.0, z))] * seg)
            continue
        rings.append([bm.verts.new((r * math.cos(2 * math.pi * i / seg), r * math.sin(2 * math.pi * i / seg), z))
                      for i in range(seg)])
    for ra, rb in zip(rings, rings[1:]):
        for i in range(seg):
            quad = [ra[i], ra[(i + 1) % seg], rb[(i + 1) % seg], rb[i]]
            uniq = []
            for v in quad:
                if v not in uniq:
                    uniq.append(v)
            if len(uniq) >= 3:
                try:
                    bm.faces.new(uniq)
                except ValueError:
                    pass
    if cap:
        for ring in (rings[0], rings[-1]):
            if len(set(ring)) >= 3:
                try:
                    bm.faces.new(list(dict.fromkeys(ring)))
                except ValueError:
                    pass
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return _mesh_object(name, bm, smooth if smooth is not None else True)


# --- extrude: outline pushed along Z -------------------------------------------------

def build_extrude(name: str, p: dict[str, Any], smooth: bool | None) -> bpy.types.Object:
    outline, depth, taper = p["outline"], p["depth"], p.get("taper", 1.0)
    bm = bmesh.new()
    bottom = [bm.verts.new((x, y, 0.0)) for x, y in outline]
    top = [bm.verts.new((x * taper, y * taper, depth)) for x, y in outline]
    bm.faces.new(bottom)
    bm.faces.new(top)
    n = len(outline)
    for i in range(n):
        bm.faces.new((bottom[i], bottom[(i + 1) % n], top[(i + 1) % n], top[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return _mesh_object(name, bm, smooth if smooth is not None else False)


# --- tube: polyline / bezier with radius -------------------------------------------

def build_tube(name: str, p: dict[str, Any], smooth: bool | None) -> bpy.types.Object:
    """A curve object with bevel; converted to mesh so modifiers and landmarks
    behave like every other part."""
    pts, radius = p["points"], p["radius"]
    radii = radius if isinstance(radius, list) else [radius] * len(pts)
    curve = bpy.data.curves.new(name + "_curve", "CURVE")
    curve.dimensions = "3D"
    curve.bevel_depth = 1.0
    curve.bevel_resolution = max(1, int(p.get("segments", 12)) // 4)
    curve.resolution_u = int(p.get("resolution", 12))
    curve.use_fill_caps = bool(p.get("caps", True))
    if p.get("bezier", True):
        spline = curve.splines.new("BEZIER")
        spline.bezier_points.add(len(pts) - 1)
        for bp, pt, r in zip(spline.bezier_points, pts, radii):
            bp.co = Vector(pt)
            bp.handle_left_type = bp.handle_right_type = "AUTO"
            bp.radius = r
    else:
        spline = curve.splines.new("POLY")
        spline.points.add(len(pts) - 1)
        for sp, pt, r in zip(spline.points, pts, radii):
            sp.co = (pt[0], pt[1], pt[2], 1.0)
            sp.radius = r
    tmp = bpy.data.objects.new(name + "_curve", curve)
    bpy.context.scene.collection.objects.link(tmp)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(tmp.evaluated_get(depsgraph))
    mesh.name = name
    bpy.data.objects.remove(tmp, do_unlink=True)
    bpy.data.curves.remove(curve)
    for poly in mesh.polygons:
        poly.use_smooth = smooth if smooth is not None else True
    return bpy.data.objects.new(name, mesh)


# --- metaball ------------------------------------------------------------------------

def build_metaball(name: str, p: dict[str, Any], smooth: bool | None) -> bpy.types.Object:
    mb = bpy.data.metaballs.new(name)
    mb.resolution = p.get("resolution", 0.05)
    mb.render_resolution = mb.resolution
    mb.threshold = p.get("threshold", 0.6)
    for b in p["blobs"]:
        el = mb.elements.new()
        el.co = Vector(b["position"])
        el.radius = b["radius"]
        el.use_negative = bool(b.get("negative", False))
        shape = b.get("shape", "ball")
        if shape == "ellipsoid":
            el.type = "ELLIPSOID"
            sx, sy, sz = b.get("size", (1, 1, 1))
            el.size_x, el.size_y, el.size_z = sx, sy, sz
        elif shape == "capsule":
            el.type = "CAPSULE"
            el.size_x = b.get("size", (1, 1, 1))[0]
    return bpy.data.objects.new(name, mb)


def metaball_to_mesh(obj: bpy.types.Object) -> bpy.types.Object:
    """Metaballs fuse across objects in Blender; freeze to a mesh so parts stay independent."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph))
    new = bpy.data.objects.new(obj.name, mesh)
    for poly in mesh.polygons:
        poly.use_smooth = True
    return new


def build_hair(name: str, p: dict[str, Any], smooth: bool | None) -> bpy.types.Object:
    raise RuntimeError("hair builder lands with milestone 23; use tube bundles for strands meanwhile")


from .anatomy import build_hand, build_head, build_loft   # noqa: E402  (circular-safe: anatomy imports loft only)

BUILDERS = {"primitive": build_primitive, "skin": build_skin, "revolve": build_revolve,
            "extrude": build_extrude, "tube": build_tube, "metaball": build_metaball,
            "hair": build_hair, "loft": build_loft, "head": build_head, "hand": build_hand}


def mirror_x(obj: bpy.types.Object, name: str) -> bpy.types.Object:
    """A mirrored copy for the other side: same mesh data flipped in X, normals fixed."""
    mesh = obj.data.copy()
    mesh.name = name
    mesh.transform(Matrix.Scale(-1.0, 4, Vector((1, 0, 0))))
    mesh.flip_normals()
    new = bpy.data.objects.new(name, mesh)
    for mod in obj.modifiers:
        m = new.modifiers.new(mod.name, mod.type)
        for prop in mod.bl_rna.properties:
            if not prop.is_readonly and prop.identifier not in ("name", "type"):
                try:
                    setattr(m, prop.identifier, getattr(mod, prop.identifier))
                except (AttributeError, TypeError):
                    pass
    return new
