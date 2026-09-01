"""Deterministic scene fingerprint. Same spec in, same fingerprint out.

Hashes everything that describes the scene: object names, types, hierarchy,
world matrices (rounded), mesh vertex counts and a vertex-position digest,
materials, constraints, f-curve keyframes, NLA strips, lights, cameras, world
and render settings. Two builds that differ in anything listed here differ in
output, and the determinism test (tests/blender) compares this string.
"""

from __future__ import annotations

import hashlib
import struct

import bpy


def _r(v, nd=5):
    return round(float(v), nd)


def _matrix(m):
    return [[_r(m[i][j]) for j in range(4)] for i in range(4)]


def _mesh_digest(mesh) -> str:
    h = hashlib.sha1()
    verts = mesh.vertices
    buf = bytearray()
    for v in verts:
        buf += struct.pack("<fff", _r(v.co.x), _r(v.co.y), _r(v.co.z))
    h.update(bytes(buf))
    h.update(str(len(mesh.polygons)).encode())
    return h.hexdigest()[:16]


def _fcurves(action):
    if hasattr(action, "layers") and action.layers:
        return [fc for layer in action.layers for strip in layer.strips
                for cb in strip.channelbags for fc in cb.fcurves]
    return list(action.fcurves)


def _animation(obj) -> list:
    ad = obj.animation_data
    if ad is None:
        return []
    out = []
    if ad.action:
        for fc in sorted(_fcurves(ad.action), key=lambda f: (f.data_path, f.array_index)):
            out.append([fc.data_path, fc.array_index,
                        [(_r(k.co.x, 3), _r(k.co.y), k.interpolation) for k in fc.keyframe_points]])
    for track in ad.nla_tracks:
        for s in track.strips:
            out.append(["nla", track.name, s.name, s.action.name if s.action else None,
                        _r(s.frame_start, 3), _r(s.frame_end, 3), _r(s.blend_in, 3),
                        _r(s.blend_out, 3), s.repeat, s.blend_type, s.extrapolation])
    return out


def _object(obj) -> list:
    rec = [obj.name, obj.type, obj.parent.name if obj.parent else None,
           obj.parent_type, obj.parent_bone, _matrix(obj.matrix_world),
           [c.name for c in obj.users_collection],
           [(c.type, c.target.name if getattr(c, "target", None) else None)
            for c in obj.constraints],
           _animation(obj)]
    d = obj.data
    if obj.type == "MESH":
        rec.append(["mesh", len(d.vertices), _mesh_digest(d),
                    [m.name if m else None for m in d.materials]])
    elif obj.type == "CAMERA":
        rec.append(["camera", d.type, _r(d.lens), _r(d.ortho_scale), _r(d.sensor_width),
                    d.dof.use_dof, _r(d.dof.focus_distance), _r(d.dof.aperture_fstop),
                    d.dof.focus_object.name if d.dof.focus_object else None,
                    _animation(d) if hasattr(d, "animation_data") else []])
    elif obj.type == "LIGHT":
        rec.append(["light", d.type, _r(d.energy), [_r(c) for c in d.color],
                    _r(getattr(d, "size", 0)), _r(getattr(d, "spot_size", 0)),
                    _r(getattr(d, "shadow_soft_size", 0)),
                    _animation(d) if hasattr(d, "animation_data") else []])
    elif obj.type == "ARMATURE":
        rec.append(["armature", [(b.name, _r(b.length)) for b in d.bones]])
    return rec


def _material(mat) -> list:
    rec = [mat.name, [_r(c) for c in mat.diffuse_color], _r(mat.roughness), _r(mat.metallic)]
    if mat.use_nodes:
        for node in sorted(mat.node_tree.nodes, key=lambda n: n.name):
            for inp in node.inputs:
                if hasattr(inp, "default_value"):
                    v = inp.default_value
                    try:
                        rec.append([node.name, inp.name, [_r(x) for x in v]])
                    except TypeError:
                        rec.append([node.name, inp.name, _r(v) if isinstance(v, (int, float)) else str(v)])
    return rec


def scene_fingerprint(scene: bpy.types.Scene) -> str:
    r = scene.render
    parts = [
        ["scene", scene.name, r.fps, _r(r.fps_base), scene.frame_start, scene.frame_end,
         r.engine, r.resolution_x, r.resolution_y, r.film_transparent,
         scene.camera.name if scene.camera else None],
        ["world", [_r(c) for c in scene.world.color] if scene.world else None,
         [(n.name, n.bl_idname) for n in scene.world.node_tree.nodes] if scene.world and scene.world.use_nodes else None],
        ["objects", [_object(o) for o in sorted(scene.objects, key=lambda o: o.name)]],
        ["materials", [_material(m) for m in sorted(bpy.data.materials, key=lambda m: m.name)]],
    ]
    text = repr(parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
