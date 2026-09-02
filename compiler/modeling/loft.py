"""Loft core: cross-sections swept along a path, bridged into quads.

Everything organic that is not a blob is built from this. A station is a
position plus a cross-section (half-width, half-depth, roundness, twist); the
path is resampled smoothly, frames are parallel-transported so the surface does
not spin, and consecutive rings bridge into a quad grid that subdivides and
deforms cleanly. `push` is the one deformation primitive: a smooth radial
displacement the builders use for anatomy and the agent uses as a modifier.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

import bmesh
from mathutils import Matrix, Vector

Vec3 = Sequence[float]


# --- cross-sections -------------------------------------------------------------------

def superellipse(segments: int, rx: float, ry: float, roundness: float = 1.0
                 ) -> list[tuple[float, float]]:
    """|x/rx|^n + |y/ry|^n = 1, with n = 2 * roundness.

    roundness 1.0 is an ellipse, 0.5 a diamond, 2.0 a rounded rectangle. Torsos
    and jaws want ~1.3; limbs want 1.0.
    """
    n = max(0.2, 2.0 * roundness)
    e = 2.0 / n
    out = []
    for i in range(segments):
        a = 2.0 * math.pi * i / segments
        c, s = math.cos(a), math.sin(a)
        out.append((math.copysign(abs(c) ** e, c) * rx,
                    math.copysign(abs(s) ** e, s) * ry))
    return out


# --- path ---------------------------------------------------------------------------

def _catmull(p0, p1, p2, p3, t: float):
    t2, t3 = t * t, t * t * t
    return 0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2
                  + (-p0 + 3 * p1 - 3 * p2 + p3) * t3)


def resample(stations: list[dict[str, Any]], resolution: int) -> list[dict[str, Any]]:
    """Catmull-Rom through the station positions, linear on the section values."""
    if resolution <= 1 or len(stations) < 3:
        return stations
    pos = [Vector(s["position"]) for s in stations]
    pad = [pos[0] - (pos[1] - pos[0])] + pos + [pos[-1] + (pos[-1] - pos[-2])]
    out = []
    for i in range(len(stations) - 1):
        a, b = stations[i], stations[i + 1]
        steps = resolution if i < len(stations) - 2 else resolution + 1
        for k in range(steps):
            t = k / resolution
            out.append({"position": _catmull(pad[i], pad[i + 1], pad[i + 2], pad[i + 3], t),
                        "size": [a["size"][j] + (b["size"][j] - a["size"][j]) * t for j in (0, 1)],
                        "roundness": a.get("roundness", 1.0) + (b.get("roundness", 1.0) - a.get("roundness", 1.0)) * t,
                        "twist": a.get("twist", 0.0) + (b.get("twist", 0.0) - a.get("twist", 0.0)) * t})
    return out


def frames(points: list[Vector]) -> list[tuple[Vector, Vector, Vector]]:
    """Parallel-transported (x, y, tangent) frames: minimal rotation between
    steps, so a bent limb keeps a consistent seam instead of twisting."""
    n = len(points)
    tangents = []
    for i in range(n):
        if i == 0:
            t = points[1] - points[0]
        elif i == n - 1:
            t = points[-1] - points[-2]
        else:
            t = points[i + 1] - points[i - 1]
        tangents.append(t.normalized() if t.length > 1e-9 else Vector((0, 0, 1)))
    ref = Vector((1, 0, 0))
    if abs(tangents[0].dot(ref)) > 0.9:
        ref = Vector((0, 1, 0))
    x = (ref - tangents[0] * ref.dot(tangents[0])).normalized()
    out = []
    for i, t in enumerate(tangents):
        if i > 0:
            prev = tangents[i - 1]
            axis = prev.cross(t)
            if axis.length > 1e-9:
                ang = math.acos(max(-1.0, min(1.0, prev.dot(t))))
                x = (Matrix.Rotation(ang, 4, axis.normalized()) @ x)
            x = (x - t * x.dot(t))
            x = x.normalized() if x.length > 1e-9 else x
        out.append((x, t.cross(x).normalized(), t))
    return out


# --- lofting ----------------------------------------------------------------------------

def loft_rings(bm: bmesh.types.BMesh, stations: list[dict[str, Any]], segments: int,
               cap_start: bool = True, cap_end: bool = True) -> list[list]:
    """Add a lofted tube to `bm`. Returns the rings of vertices, so callers can
    attach or reshape afterwards."""
    pts = [Vector(s["position"]) for s in stations]
    fr = frames(pts)
    rings = []
    for st, (ax, ay, _t), origin in zip(stations, fr, pts):
        rx, ry = st["size"]
        twist = st.get("twist", 0.0)
        ct, stw = math.cos(twist), math.sin(twist)
        ring = []
        degenerate = abs(rx) < 1e-6 and abs(ry) < 1e-6
        if degenerate:
            v = bm.verts.new(origin)
            rings.append([v] * segments)
            continue
        for px, py in superellipse(segments, rx, ry, st.get("roundness", 1.0)):
            qx, qy = px * ct - py * stw, px * stw + py * ct
            ring.append(bm.verts.new(origin + ax * qx + ay * qy))
        rings.append(ring)
    bm.verts.ensure_lookup_table()

    for a, b in zip(rings, rings[1:]):
        for j in range(segments):
            quad = [a[j], a[(j + 1) % segments], b[(j + 1) % segments], b[j]]
            uniq = list(dict.fromkeys(quad))
            if len(uniq) >= 3:
                try:
                    bm.faces.new(uniq)
                except ValueError:
                    pass
    for ring, do_cap in ((rings[0], cap_start), (rings[-1], cap_end)):
        uniq = list(dict.fromkeys(ring))
        if do_cap and len(uniq) >= 3:
            try:
                bm.faces.new(uniq)
            except ValueError:
                pass
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return rings


# --- deformation -------------------------------------------------------------------------

def push(verts, center: Vec3, radius: float, direction: Vec3, strength: float,
         falloff: str = "smooth") -> int:
    """Move vertices near `center` along `direction`, smoothly falling to zero at
    `radius`. The one sculpting primitive: brows, sockets, cheeks, dents, swells."""
    c, d = Vector(center), Vector(direction)
    if d.length > 1e-9:
        d = d.normalized()
    r2 = radius * radius
    moved = 0
    for v in verts:
        delta = v.co - c
        dist2 = delta.length_squared
        if dist2 >= r2:
            continue
        t = 1.0 - math.sqrt(dist2) / radius
        w = t * t * (3.0 - 2.0 * t) if falloff == "smooth" else t
        v.co += d * (strength * w)
        moved += 1
    return moved


def push_radial(verts, center: Vec3, radius: float, strength: float, axis: str = "z") -> int:
    """Swell or pinch around an axis through `center`: waists, jowls, muscle."""
    c = Vector(center)
    idx = {"x": 0, "y": 1, "z": 2}[axis]
    r2 = radius * radius
    moved = 0
    for v in verts:
        delta = v.co - c
        if delta.length_squared >= r2:
            continue
        t = 1.0 - delta.length / radius
        w = t * t * (3.0 - 2.0 * t)
        out = delta.copy()
        out[idx] = 0.0
        if out.length > 1e-9:
            v.co += out.normalized() * (strength * w)
            moved += 1
    return moved


def finish(name: str, bm: bmesh.types.BMesh, smooth: bool, remove_doubles: float = 1e-5):
    """Clean up and hand back a mesh datablock."""
    import bpy
    if remove_doubles:
        bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=remove_doubles)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    for poly in mesh.polygons:
        poly.use_smooth = smooth
    return mesh
