"""Anatomy builders: loft, head, hand.

These exist because a stick figure wrapped in tubes cannot read as a person.
Each is parametric: the agent tunes numbers and looks, never vertices. Head and
hand publish named points (`blendy_points`, local space) so a recipe can anchor
landmarks with `point:eye_l` instead of guessing coordinates.

Conventions: a character faces -Y, so their LEFT is +X, matching Blender's `.L`.
Head origin is the chin bottom on the neck axis; hand origin is the wrist with
fingers along +Z and the palm facing -Y.
"""

from __future__ import annotations

import math
from typing import Any

import bmesh
import bpy
from mathutils import Vector

from .loft import finish, loft_rings, push, push_radial, resample

# t (chin=0, crown=1) -> half-width, FRONT depth, BACK depth, roundness, all as
# fractions of the head's half measurements. Front and back are independent
# because that is the whole difference between a skull and an egg: the face
# plane stays forward down to the chin while the cranium bulges backward.
_SKULL = [
    (0.00, 0.26, 0.52, 0.10, 1.10), (0.05, 0.46, 0.70, 0.30, 1.15),
    (0.12, 0.66, 0.85, 0.52, 1.20), (0.22, 0.82, 0.94, 0.74, 1.25),
    (0.33, 0.91, 0.99, 0.88, 1.25), (0.45, 0.97, 1.00, 0.96, 1.20),
    (0.55, 1.00, 0.99, 1.02, 1.15), (0.66, 0.97, 0.95, 1.06, 1.10),
    (0.76, 0.92, 0.89, 1.06, 1.05), (0.86, 0.80, 0.79, 1.00, 1.00),
    (0.94, 0.60, 0.60, 0.84, 1.00), (1.00, 0.20, 0.28, 0.40, 1.00),
]


def _skull(t: float) -> tuple[float, float, float, float]:
    for a, b in zip(_SKULL, _SKULL[1:]):
        if t <= b[0] or b[0] == 1.0:
            u = 0.0 if b[0] == a[0] else max(0.0, min(1.0, (t - a[0]) / (b[0] - a[0])))
            return tuple(a[i] + (b[i] - a[i]) * u for i in (1, 2, 3, 4))
    return _SKULL[-1][1:]


# --- loft ---------------------------------------------------------------------------

def build_loft(name: str, p: dict[str, Any], smooth: bool | None,
                   objects=None) -> bpy.types.Object:
    """A path of cross-sections. Torsos, necks, sleeves, boots, limbs, straps."""
    stations = [{"position": s["position"], "size": s["size"],
                 "roundness": s.get("roundness", 1.0), "twist": s.get("twist", 0.0)}
                for s in p["path"]]
    stations = resample(stations, int(p.get("resolution", 3)))
    bm = bmesh.new()
    loft_rings(bm, stations, int(p.get("segments", 24)),
               p.get("caps", True), p.get("caps", True))
    for op in p.get("shape", []):
        if "direction" in op:
            push(bm.verts, op["center"], op["radius"], op["direction"], op["strength"])
        else:
            push_radial(bm.verts, op["center"], op["radius"], op["strength"], op.get("axis", "z"))
    obj = bpy.data.objects.new(name, finish(name, bm, True if smooth is None else smooth))
    ends = [Vector(stations[0]["position"]), Vector(stations[-1]["position"])]
    obj["blendy_points"] = {"start": list(ends[0]), "end": list(ends[1])}
    return obj


# --- head ------------------------------------------------------------------------------

def build_head(name: str, p: dict[str, Any], smooth: bool | None,
                   objects=None) -> bpy.types.Object:
    h = p.get("height", 0.235)
    w = p.get("width", 0.155)
    d = p.get("depth", 0.205)
    segments, rings_n = int(p.get("segments", 32)), int(p.get("rings", 26))
    hw, hd = w / 2.0, d / 2.0

    stations = []
    for i in range(rings_n + 1):
        wf, _fr, _bk, rd = _skull(i / rings_n)
        stations.append({"position": Vector((0.0, 0.0, (i / rings_n) * h)),
                         "size": [hw * wf, hd], "roundness": rd, "twist": 0.0})
    bm = bmesh.new()
    rings = loft_rings(bm, stations, segments, cap_start=True, cap_end=True)
    for i, ring in enumerate(rings):
        _wf, front, back, _rd = _skull(i / rings_n)
        for v in dict.fromkeys(ring):
            v.co.y *= front if v.co.y < 0 else back
    verts = bm.verts
    face_y = -hd * _skull(0.505)[1]      # the face plane, at the eye line
    s = w                        # every feature scales with head width

    brow, socket = p.get("brow", 0.5), p.get("socket", 0.5)
    cheek, jaw = p.get("cheek", 0.5), p.get("jaw", 0.5)
    chin, temple, age = p.get("chin", 0.5), p.get("temple", 0.3), p.get("age", 0.4)

    # Feature radii are deliberately small. A brow ridge is a 3 cm form, not an
    # 8 cm one: spread it wider and it just becomes the skull again.
    push(verts, (0, face_y, 0.650 * h), 0.30 * w, (0, -1, 0.15), 0.105 * s * brow)
    push(verts, (0, face_y * 0.96, 0.600 * h), 0.20 * w, (0, 1, 0), 0.030 * s * brow)   # glabella notch
    for sx in (1, -1):
        push(verts, (sx * 0.31 * w, face_y * 0.95, 0.525 * h), 0.185 * w, (0, 1, 0), 0.115 * s * socket)
        push(verts, (sx * 0.31 * w, face_y * 0.92, 0.470 * h), 0.13 * w, (0, -1, 0), 0.040 * s * socket)   # lower lid
        push(verts, (sx * 0.42 * w, face_y * 0.72, 0.380 * h), 0.21 * w, (sx * 0.55, -0.84, 0), 0.095 * s * cheek)
        push(verts, (sx * 0.38 * w, face_y * 0.66, 0.270 * h), 0.19 * w, (-sx * 0.5, 0.86, 0), 0.055 * s * age)
        push(verts, (sx * 0.45 * w, 0.06 * hd, 0.150 * h), 0.22 * w, (sx * 0.94, 0.34, 0), 0.095 * s * jaw)
        push(verts, (sx * 0.49 * w, -0.18 * hd, 0.640 * h), 0.19 * w, (-sx, 0, 0), 0.045 * s * temple)
        push(verts, (sx * 0.26 * w, face_y * 0.88, 0.190 * h), 0.15 * w, (0, 1, 0), 0.030 * s * age)
    push(verts, (0, face_y * 0.86, 0.070 * h), 0.17 * w, (0, -1, 0), 0.090 * s * chin)
    push(verts, (0, face_y * 0.90, 0.235 * h), 0.11 * w, (0, -1, 0), 0.022 * s)        # upper lip
    push(verts, (0, face_y * 0.90, 0.195 * h), 0.10 * w, (0, -1, 0), 0.018 * s)        # lower lip
    push(verts, (0, face_y * 0.88, 0.215 * h), 0.07 * w, (0, 1, 0), 0.022 * s)         # mouth line
    push(verts, (0, face_y * 0.82, 0.135 * h), 0.13 * w, (0, 1, 0), 0.020 * s)         # under the lip
    push(verts, (0, hd * 1.02, 0.640 * h), 0.34 * w, (0, 1, 0), 0.022 * s)             # occiput

    # Eyelids. Without a rim standing proud of the socket the eyeball reads as a
    # ball stuck on a face, which is the single loudest tell of an unfinished head.
    lids = p.get("lids", 1.0)
    socket_y = face_y * 0.95 + 0.115 * s * socket
    for sx in (1, -1):
        eye = (sx * 0.31 * w, socket_y, 0.525 * h)
        # The lid must come forward past the socket floor, otherwise it is just a
        # shallower dish and the eyeball still sits on the surface.
        push(verts, (eye[0], eye[1], eye[2] + 0.031 * h), 0.078 * w, (0, -1, -0.30), 0.135 * s * lids)
        push(verts, (eye[0], eye[1], eye[2] - 0.021 * h), 0.075 * w, (0, -1, 0.35), 0.095 * s * lids)
        push(verts, (eye[0], eye[1] + 0.006 * s, eye[2] + 0.052 * h), 0.065 * w, (0, 1, 0), 0.030 * s * lids)
        push(verts, (sx * 0.44 * w, face_y * 0.86, 0.545 * h), 0.055 * w, (0, 1, 0), 0.016 * s * age)

    # Age. A seventy-year-old is furrows and folds, not smooth skin.
    if age > 0:
        push(verts, (0, face_y * 0.99, 0.700 * h), 0.20 * w, (0, 1, 0), 0.028 * s * age)
        push(verts, (0, face_y * 0.99, 0.760 * h), 0.18 * w, (0, 1, 0), 0.024 * s * age)
        for sx in (1, -1):
            push(verts, (sx * 0.055 * w, face_y * 0.98, 0.625 * h), 0.040 * w, (0, 1, 0), 0.038 * s * age)
            push(verts, (sx * 0.19 * w, face_y * 0.84, 0.285 * h), 0.065 * w, (0, 1, 0), 0.050 * s * age)
            push(verts, (sx * 0.45 * w, face_y * 0.64, 0.515 * h), 0.048 * w, (0, 1, 0), 0.030 * s * age)
    eye_r = p.get("eye_radius", 0.012) * (w / 0.155)
    if lids > 0 and eye_r > 0:
        for sx in (1, -1):
            _add_eyelids(bm, (sx * 0.31 * w, face_y * 0.95 + 0.138 * s * socket, 0.525 * h),
                         eye_r, lids)

    spec = p.get("nose", {})
    nose = spec or {}
    if spec is not None and nose.get("length", 0.028) > 0:
        _add_nose(bm, nose, h, w, hd, segments)
    if p.get("ears", 1.0) > 0:
        _add_ears(bm, p.get("ears", 1.0), h, w, hd, segments)

    obj = bpy.data.objects.new(name, finish(name, bm, True if smooth is None else smooth))
    brow_y = face_y - 0.075 * s * brow
    obj["blendy_points"] = {
        "eye_l": [0.31 * w, face_y * 0.95 + 0.138 * s * socket, 0.525 * h],
        "eye_r": [-0.31 * w, face_y * 0.95 + 0.138 * s * socket, 0.525 * h],
        "eye_midpoint": [0.0, face_y * 0.95 + 0.138 * s * socket, 0.525 * h],
        "ear_l": [0.46 * w, 0.05 * hd, 0.482 * h], "ear_r": [-0.46 * w, 0.05 * hd, 0.482 * h],
        "chin": [0.0, face_y * 0.86 - 0.070 * s * chin, 0.070 * h],
        "head_top": [0.0, 0.0, h], "nose_tip": [0.0, face_y - nose.get("length", 0.028) * (w / 0.155), 0.378 * h],
        "mouth": [0.0, face_y * 0.90, 0.215 * h],
        "jaw_l": [0.45 * w + 0.070 * s * jaw, 0.06 * hd, 0.150 * h],
        "jaw_r": [-0.45 * w - 0.070 * s * jaw, 0.06 * hd, 0.150 * h],
        "neck": [0.0, 0.0, 0.0], "brow": [0.0, brow_y, 0.650 * h],
    }
    return obj


def _add_eyelids(bm, center, r: float, lids: float) -> None:
    """Lid shells hugging the eyeball.

    A push can raise a rim on the skull but never wrap forward over a sphere that
    protrudes from it, which is why a pushed-only eye still reads as a ball stuck
    on a face. These are spherical bands at just over the eyeball's radius, so
    they cover it by construction whatever the skull does.
    """
    cx, cy, cz = center
    R = r * (1.04 + 0.05 * lids)
    seg, rings = 24, 16
    grid = []
    for i in range(rings + 1):
        phi = -math.pi / 2 + math.pi * i / rings
        row = []
        for j in range(seg):
            th = 2 * math.pi * j / seg
            row.append(bm.verts.new((cx + R * math.cos(phi) * math.sin(th),
                                     cy - R * math.cos(phi) * math.cos(th),
                                     cz + R * math.sin(phi))))
        grid.append(row)
    # Upper lid hoods the eye; the lower lid is a thinner rim beneath it. Both are
    # cut to the front arc so nothing pokes out of the side of the head.
    bands = ((0.16 - 0.12 * lids, 1.30), (-0.95, -0.30 + 0.06 * lids))
    used = set()
    for i in range(rings):
        phi_a = -math.pi / 2 + math.pi * i / rings
        phi_b = -math.pi / 2 + math.pi * (i + 1) / rings
        mid_phi = (phi_a + phi_b) / 2
        if not any(lo <= mid_phi <= hi for lo, hi in bands):
            continue
        for j in range(seg):
            th = 2 * math.pi * (j + 0.5) / seg
            front = math.cos(th)
            if front < 0.30:
                continue
            quad = [grid[i][j], grid[i][(j + 1) % seg], grid[i + 1][(j + 1) % seg], grid[i + 1][j]]
            try:
                bm.faces.new(quad)
                used.update(quad)
            except ValueError:
                pass
    for row in grid:
        for v in row:
            if v not in used:
                bm.verts.remove(v)


def _add_nose(bm, nose: dict[str, Any], h: float, w: float, hd: float, segments: int) -> None:
    """`length` is how far the nose projects from the face plane, not its height."""
    proj = nose.get("length", 0.028) * (w / 0.155)
    nw = nose.get("width", 0.036) * (w / 0.155)
    bridge, hook = nose.get("bridge", 0.5), nose.get("hook", 0.2)
    y = -hd * _skull(0.50)[1]
    path = [
        {"position": Vector((0, y + 0.004, 0.560 * h)), "size": [nw * 0.26, nw * 0.20], "roundness": 1.0},
        {"position": Vector((0, y - proj * 0.30 * bridge, 0.490 * h)), "size": [nw * 0.30, nw * 0.26], "roundness": 1.0},
        {"position": Vector((0, y - proj * 0.72, 0.425 * h)), "size": [nw * 0.38, nw * 0.34], "roundness": 1.05},
        {"position": Vector((0, y - proj * (1.0 + 0.15 * hook), 0.378 * h)), "size": [nw * 0.50, nw * 0.42], "roundness": 1.15},
        {"position": Vector((0, y - proj * 0.62, 0.350 * h)), "size": [nw * 0.54, nw * 0.26], "roundness": 1.25},
        {"position": Vector((0, y + 0.004, 0.342 * h)), "size": [nw * 0.44, nw * 0.16], "roundness": 1.25},
    ]
    loft_rings(bm, resample(path, 3), max(10, segments // 2), cap_start=True, cap_end=True)


def _add_ears(bm, size: float, h: float, w: float, hd: float, segments: int) -> None:
    """An ear is ~6 cm tall, 3 cm deep and protrudes ~1.5 cm. Lofted outward along X,
    so the cross-section is (depth, height)."""
    ear = size * (w / 0.155)
    depth, tall, out = 0.015 * ear, 0.032 * ear, 0.016 * ear
    for sx in (1, -1):
        x = sx * w * 0.45
        path = [
            {"position": Vector((x, 0.04 * hd, 0.482 * h)), "size": [depth * 0.95, tall * 0.95], "roundness": 0.9},
            {"position": Vector((x + sx * out * 0.55, 0.05 * hd, 0.487 * h)), "size": [depth, tall], "roundness": 0.9},
            {"position": Vector((x + sx * out, 0.06 * hd, 0.478 * h)), "size": [depth * 0.5, tall * 0.72], "roundness": 0.9},
        ]
        loft_rings(bm, resample(path, 2), max(10, segments // 2), cap_start=True, cap_end=True)


# --- hand ---------------------------------------------------------------------------------

def build_hand(name: str, p: dict[str, Any], smooth: bool | None,
                   objects=None) -> bpy.types.Object:
    """Wrist at the origin, fingers along +Z, palm facing -Y. `side` mirrors the thumb."""
    length = p.get("length", 0.19)
    width = p.get("width", 0.088)
    thick = p.get("thickness", 0.032)
    curl, spread = p.get("curl", 0.25), p.get("spread", 0.12)
    thumb, side = p.get("thumb", 1.0), p.get("side", "l")
    segments = int(p.get("segments", 12))
    sx = 1.0 if side == "l" else -1.0
    hw, ht = width / 2.0, thick / 2.0
    bm = bmesh.new()

    palm = [
        {"position": Vector((0, 0, 0)), "size": [hw * 0.72, ht * 1.05], "roundness": 1.3},
        {"position": Vector((0, 0, 0.10 * length)), "size": [hw * 0.86, ht * 1.10], "roundness": 1.4},
        {"position": Vector((0, -0.01 * length, 0.28 * length)), "size": [hw, ht], "roundness": 1.5},
        {"position": Vector((0, -0.02 * length, 0.42 * length)), "size": [hw * 0.98, ht * 0.86], "roundness": 1.5},
    ]
    loft_rings(bm, resample(palm, 3), segments, True, True)

    knuckle = 0.42 * length
    finger_len = [0.40, 0.44, 0.41, 0.34]
    for i, fl in enumerate(finger_len):
        fx = (i - 1.5) / 1.5 * hw * 0.78
        lean = -sx * (i - 1.5) / 1.5 * spread * length * 0.35
        r = width * (0.105 if i in (1, 2) else 0.095)
        bend = curl * fl * length
        path = [
            {"position": Vector((fx, -0.01 * length, knuckle)), "size": [r, r * 0.92], "roundness": 1.2},
            {"position": Vector((fx + lean * 0.4, -0.02 * length - bend * 0.18, knuckle + fl * length * 0.42)),
             "size": [r * 0.90, r * 0.84], "roundness": 1.1},
            {"position": Vector((fx + lean * 0.8, -0.02 * length - bend * 0.52, knuckle + fl * length * 0.78)),
             "size": [r * 0.78, r * 0.74], "roundness": 1.1},
            {"position": Vector((fx + lean, -0.02 * length - bend * 0.86, knuckle + fl * length)),
             "size": [r * 0.52, r * 0.50], "roundness": 1.0},
        ]
        loft_rings(bm, resample(path, 3), max(8, segments // 2), True, True)

    if thumb > 0:
        tr = width * 0.125
        tl = 0.34 * length * thumb
        path = [
            {"position": Vector((sx * hw * 0.55, 0.0, 0.10 * length)), "size": [tr, tr * 0.95], "roundness": 1.2},
            {"position": Vector((sx * (hw * 0.95), -0.02 * length, 0.10 * length + tl * 0.45)),
             "size": [tr * 0.86, tr * 0.82], "roundness": 1.1},
            {"position": Vector((sx * (hw * 1.10), -0.05 * length * thumb, 0.10 * length + tl)),
             "size": [tr * 0.56, tr * 0.54], "roundness": 1.0},
        ]
        loft_rings(bm, resample(path, 3), max(8, segments // 2), True, True)

    obj = bpy.data.objects.new(name, finish(name, bm, True if smooth is None else smooth))
    obj["blendy_points"] = {
        "wrist": [0.0, 0.0, 0.0], "palm": [0.0, 0.0, 0.22 * length],
        "knuckles": [0.0, -0.02 * length, knuckle],
        "fingertip": [0.0, -0.02 * length - curl * 0.44 * length * 0.86, knuckle + 0.44 * length],
        "thumb_tip": [sx * width * 0.55, -0.05 * length * thumb, 0.10 * length + 0.34 * length * thumb],
    }
    return obj
