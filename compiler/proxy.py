"""Director-mode proxy export: a greybox glTF the browser frames against.

- Heavy meshes become bounding boxes; light ones export as-is.
- Rigged characters export decimated with their armature and baked NLA motion,
  so the director frames a moving character at a specific frame.
- Landmark empties export as named nodes (LM_<asset>_<landmark>) so the UI can
  list and snap to them.
The exporter's default +Y up conversion is used; the browser side reverses it
with compiler/coords.py, the only place that arithmetic lives.
"""

from __future__ import annotations

import os

import bpy
from mathutils import Vector

from .refs import parse_ref
from .resolvers.animation import bake_visible_motion
from .resolvers.landmarks import landmark_empty

HEAVY_POLYS = 20000
DECIMATE_RATIO = 0.15


def _bbox_proxy(ctx, obj: bpy.types.Object) -> bpy.types.Object:
    from .resolvers.assets import make_primitive
    box = make_primitive(f"PROXY_{obj.name}", "cube")
    ctx.link(box, "Helpers")
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    lo = Vector(map(min, *corners))
    hi = Vector(map(max, *corners))
    box.location = (lo + hi) / 2
    box.scale = (hi - lo) / 2
    return box


def _materialize_all_landmarks(ctx) -> None:
    """Every landmark of every profiled asset becomes an empty, not just those the
    spec references: the director needs the whole vocabulary to snap to."""
    for asset in ctx.spec["assets"]:
        resolved = ctx.resolved.get(asset["id"], {})
        profile = resolved.get("profile") or ctx.profiles.profile_for(asset)
        for name in (profile or {}).get("landmarks", {}):
            ref = f"@{asset['id']}.{name}"
            try:
                landmark_empty(ctx, ref)
            except RuntimeError as e:
                ctx.warn(f"proxy: skipped landmark {ref}: {e}")
    for empty in ctx.landmark_empties.values():
        empty.empty_display_size = 0.08


def export_proxy(ctx, out_path: str) -> str:
    scene = ctx.scene
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    _materialize_all_landmarks(ctx)

    export_objects: list[bpy.types.Object] = list(ctx.landmark_empties.values())
    rigged_assets = {r["asset_id"] for r in ctx.spec["rigs"]}
    for asset in ctx.spec["assets"]:
        aid = asset["id"]
        meshes = ctx.meshes.get(aid, [])
        if aid in rigged_assets:
            for m in meshes:
                if len(m.data.polygons) > HEAVY_POLYS:
                    mod = m.modifiers.new("proxy_decimate", "DECIMATE")
                    mod.ratio = DECIMATE_RATIO
                export_objects.append(m)
            arm = ctx.armatures.get(f"asset:{aid}")
            if arm is not None:
                if arm.animation_data and arm.animation_data.nla_tracks:
                    bake_visible_motion(ctx, arm)
                export_objects.append(arm)
            continue
        for m in meshes:
            if len(m.data.polygons) > HEAVY_POLYS:
                export_objects.append(_bbox_proxy(ctx, m))
            else:
                export_objects.append(m)
    for cam_spec in ctx.spec["cameras"]:
        export_objects.append(ctx.objects[cam_spec["id"]])

    for obj in scene.objects:
        obj.select_set(obj in export_objects)
    with bpy.context.temp_override(scene=scene, selected_objects=export_objects):
        bpy.ops.export_scene.gltf(
            filepath=out_path, export_format="GLB", use_selection=True,
            export_apply=True, export_animations=True, export_frame_range=True,
            export_cameras=True, export_lights=False, export_materials="NONE",
            export_yup=True, export_extras=True)
    return out_path
