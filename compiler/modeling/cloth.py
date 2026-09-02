"""Sheet builder and hair builder: the two forms a swept solid cannot make.

A cloak is a thin surface that falls around a body, and hair is thousands of
strands. Both were previously faked with solid blobs, which is why they read as
a bolster and a helmet. `sheet` makes a real quad sheet for the cloth solver to
drape; `hair` grows tapered strands off an emitter surface, deterministically
from a seed.
"""

from __future__ import annotations

import math
import random
from typing import Any

import bmesh
import bpy
from mathutils import Vector

from .loft import finish, loft_rings


def _interp(table, t: float, default: float) -> float:
    if not table:
        return default
    pts = sorted((float(a), float(b)) for a, b in table)
    if t <= pts[0][0]:
        return pts[0][1]
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if t <= t1:
            u = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return v0 + (v1 - v0) * u
    return pts[-1][1]


# --- sheet ------------------------------------------------------------------------

def build_sheet(name: str, p: dict[str, Any], smooth: bool | None,
                objects=None) -> bpy.types.Object:
    """A quad sheet hanging from z=0 down to -height, width across X.

    `shape` tapers the width down the drop ([t, scale] pairs, t=0 at the top),
    `curve` bends it around the body in Y, and `slack` adds fabric the solver can
    fold. Pin it and let the cloth modifier do the rest.
    """
    w, h = p["size"]
    nx, nz = p.get("resolution", [16, 20])
    shape, curve = p.get("shape", []), p.get("curve", [])
    slack = p.get("slack", 0.0)
    arc = math.radians(p.get("arc", 0.0))     # wrap the sheet round Z before it falls
    bm = bmesh.new()
    grid = []
    for j in range(nz + 1):
        t = j / nz
        scale = _interp(shape, t, 1.0)
        y0 = _interp(curve, t, 0.0)
        row = []
        for i in range(nx + 1):
            u = i / nx
            bow = (2.0 * u - 1.0) ** 2
            sag = slack * math.sin(math.pi * u) * math.sin(math.pi * t)
            if arc > 1e-6:
                # a cloak starts wrapped around the shoulders: lay the row on a circle
                # a true arc centred on the part origin, so the sheet wraps the
                # body instead of bulging away behind it
                radius = (w * scale) / arc
                a = (u - 0.5) * arc
                x = radius * math.sin(a)
                y = y0 + radius * math.cos(a) + bow * p.get("wrap", 0.0) + sag
            else:
                x = (u - 0.5) * w * scale
                y = y0 + bow * p.get("wrap", 0.0) + sag
            row.append(bm.verts.new((x, y, -t * h)))
        grid.append(row)
    for j in range(nz):
        for i in range(nx):
            bm.faces.new((grid[j][i], grid[j][i + 1], grid[j + 1][i + 1], grid[j + 1][i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    obj = bpy.data.objects.new(name, finish(name, bm, True if smooth is None else smooth,
                                            remove_doubles=0.0))
    obj["blendy_points"] = {"top_center": [0.0, _interp(curve, 0.0, 0.0), 0.0],
                            "bottom_center": [0.0, _interp(curve, 1.0, 0.0), -h],
                            "top_left": [w / 2, 0.0, 0.0], "top_right": [-w / 2, 0.0, 0.0]}
    return obj


# --- hair -------------------------------------------------------------------------

def _emitter_samples(obj: bpy.types.Object, count: int, region: dict[str, Any] | None,
                     rng: random.Random) -> list[tuple[Vector, Vector]]:
    """Area-weighted points on the emitter's surface, with their normals, in the
    emitter's local space. `region` clips by a normalised span on one axis."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    mesh = obj.evaluated_get(depsgraph).to_mesh()
    try:
        idx = {"x": 0, "y": 1, "z": 2}[(region or {}).get("axis", "z")]
        lo = min(v.co[idx] for v in mesh.vertices)
        hi = max(v.co[idx] for v in mesh.vertices)
        span = (hi - lo) or 1.0
        r0 = (region or {}).get("min", 0.0)
        r1 = (region or {}).get("max", 1.0)
        bias = (region or {}).get("normal_bias")
        want = Vector(bias["direction"]).normalized() if bias else None
        min_dot = bias.get("min_dot", 0.0) if bias else 0.0
        faces = []
        for poly in mesh.polygons:
            c = poly.center
            if region is not None and not (r0 <= (c[idx] - lo) / span <= r1):
                continue
            if want is not None and poly.normal.normalized().dot(want) < min_dot:
                continue          # keeps scalp hair off the face, beard off the scalp
            faces.append((poly.area, c.copy(), poly.normal.copy()))
        if not faces:
            faces = [(p.area, p.center.copy(), p.normal.copy()) for p in mesh.polygons]
        total = sum(f[0] for f in faces) or 1.0
        out = []
        for _ in range(count):
            r = rng.random() * total
            acc = 0.0
            for area, centre, normal in faces:
                acc += area
                if acc >= r:
                    jitter = Vector((rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1))) * 0.004
                    out.append((centre + jitter, normal.normalized()))
                    break
        return out
    finally:
        obj.evaluated_get(depsgraph).to_mesh_clear()


def build_hair(name: str, p: dict[str, Any], smooth: bool | None,
               objects=None) -> bpy.types.Object:
    """Tapered strands grown off an emitter part, bent by gravity and clumping.

    Deterministic for a given seed, so a rebuild is the same hair every time.
    """
    if not objects or p.get("emitter") not in (objects or {}):
        raise RuntimeError(f"hair '{name}': emitter part '{p.get('emitter')}' must exist and be built")
    emitter = objects[p["emitter"]]
    count = int(p.get("count", 120))
    length = p["length"]
    radius = p.get("radius", 0.004)
    segments = int(p.get("segments", 6))
    sides = int(p.get("sides", 5))
    clump, gravity = p.get("clump", 0.4), p.get("gravity", 0.6)
    curl, sway = p.get("curl", 0.0), p.get("sway", 0.15)
    taper = p.get("taper", 0.25)
    bias = Vector(p.get("direction", (0, 0, 0)))
    rng = random.Random(int(p.get("seed", 0)))

    seeds = _emitter_samples(emitter, count, p.get("region"), rng)
    clusters = max(1, int(count * (1.0 - clump) / 4) + 1)
    centres = [rng.choice(seeds)[0] if seeds else Vector() for _ in range(clusters)]

    bm = bmesh.new()
    for i, (origin, normal) in enumerate(seeds):
        target = centres[i % clusters]
        strand_len = length * rng.uniform(0.75, 1.15)
        r = radius * rng.uniform(0.8, 1.2)
        side = Vector((normal.y, -normal.x, 0.0))
        side = side.normalized() if side.length > 1e-6 else Vector((1, 0, 0))
        phase = rng.uniform(0, math.tau)
        path, pos = [], origin.copy()
        down = Vector((0, 0, -1))
        for s in range(segments + 1):
            t = s / segments
            step = strand_len / segments
            # A strand leaves along the normal and is turned by gravity within the
            # first centimetres. Without this decay every strand sticks straight out.
            w = math.exp(-3.2 * max(0.15, gravity) * t)
            heading = normal * w + down * (1.0 - w) * (0.6 + gravity) + bias * 0.6
            pull = (target - pos) * clump * 0.30 * t
            wobble = side * (math.sin(phase + t * math.pi * (1 + curl * 3)) * sway * strand_len * 0.18)
            path.append({"position": pos.copy(),
                         "size": [r * (1 - taper * t), r * (1 - taper * t)], "roundness": 1.0})
            heading = (heading + pull)
            heading = heading.normalized() if heading.length > 1e-9 else down
            pos = pos + heading * step + wobble * (1.0 / segments)
        path[-1]["size"] = [r * 0.15, r * 0.15]
        loft_rings(bm, path, sides, cap_start=True, cap_end=True)
    obj = bpy.data.objects.new(name, finish(name, bm, True if smooth is None else smooth,
                                            remove_doubles=0.0))
    obj["blendy_points"] = {"root": list(seeds[0][0]) if seeds else [0, 0, 0]}
    return obj
