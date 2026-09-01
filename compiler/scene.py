"""Scene-level compiler steps: reset, timing, world, render settings, saving.

Runs inside Blender. Data API only; no bpy.ops except where noted.
"""

from __future__ import annotations

import os
from typing import Any

import bpy

from .refs import ROOT

SCENE_NAME = "Shot"
COLLECTIONS = ("Assets", "Rigs", "Cameras", "Lights", "Landmarks", "Helpers")


class BuildContext:
    """Everything resolvers share. Objects are registered by spec id so later
    stages (landmarks, cameras) can look them up without touching bpy.data names."""

    def __init__(self, spec: dict[str, Any], resolved: dict[str, Any], profiles):
        self.spec = spec
        self.resolved = resolved            # asset_id -> {"path": ..., "profile": {...}}
        self.profiles = profiles            # ProfileIndex
        self.scene: bpy.types.Scene | None = None
        self.objects: dict[str, bpy.types.Object] = {}       # spec id -> object
        self.armatures: dict[str, bpy.types.Object] = {}     # rig id -> armature object
        self.meshes: dict[str, list[bpy.types.Object]] = {}  # asset id -> mesh objects
        self.landmark_empties: dict[str, bpy.types.Object] = {}
        self.warnings: list[str] = []
        self.collections: dict[str, bpy.types.Collection] = {}

    def link(self, obj: bpy.types.Object, collection: str) -> None:
        self.collections[collection].objects.link(obj)

    def register(self, spec_id: str, obj: bpy.types.Object) -> None:
        if spec_id in self.objects:
            raise RuntimeError(f"object for id '{spec_id}' registered twice")
        self.objects[spec_id] = obj

    def warn(self, message: str) -> None:
        self.warnings.append(message)


# --- step 2: reset -------------------------------------------------------------

def reset() -> bpy.types.Scene:
    """Return an empty scene. Everything from whatever was open is removed, and
    orphan data is purged so rebuilds do not accumulate."""
    # A fresh scene, then drop every other one. Do not rely on the startup file.
    scene = bpy.data.scenes.new(SCENE_NAME + "_tmp")
    for other in list(bpy.data.scenes):
        if other != scene:
            bpy.data.scenes.remove(other, do_unlink=True)
    scene.name = SCENE_NAME
    for window in bpy.context.window_manager.windows:
        window.scene = scene

    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for coll in list(bpy.data.collections):
        bpy.data.collections.remove(coll, do_unlink=True)
    for block_type in ("meshes", "armatures", "cameras", "lights", "materials", "images",
                       "actions", "worlds", "node_groups", "sounds", "curves", "textures"):
        for block in list(getattr(bpy.data, block_type)):
            if block.users == 0:
                getattr(bpy.data, block_type).remove(block)
    return scene


def make_collections(ctx: BuildContext) -> None:
    for name in COLLECTIONS:
        coll = bpy.data.collections.new(name)
        ctx.scene.collection.children.link(coll)
        ctx.collections[name] = coll


# --- step 3: timing (BEFORE any animation import) ------------------------------

def set_timing(scene: bpy.types.Scene, meta: dict[str, Any]) -> None:
    fps = meta["fps"]
    scene.render.fps = int(round(fps))
    scene.render.fps_base = int(round(fps)) / fps if fps else 1.0   # 29.97 etc.
    scene.frame_start = meta["frame_start"]
    scene.frame_end = meta["frame_end"]
    scene.frame_current = meta["frame_start"]


# --- step 4: world ---------------------------------------------------------------

def build_world(ctx: BuildContext, hdri_path: str | None) -> None:
    w = ctx.spec["world"]
    world = bpy.data.worlds.new("World")
    world.use_nodes = True
    ctx.scene.world = world
    nodes, links = world.node_tree.nodes, world.node_tree.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputWorld")
    bg = nodes.new("ShaderNodeBackground")
    bg.inputs["Strength"].default_value = w["strength"]
    links.new(bg.outputs["Background"], out.inputs["Surface"])

    if w["hdri"] is not None:
        if not hdri_path or not os.path.exists(hdri_path):
            raise RuntimeError(f"world.hdri {w['hdri']} is not resolved to a local file")
        env = nodes.new("ShaderNodeTexEnvironment")
        env.image = bpy.data.images.load(hdri_path, check_existing=True)
        links.new(env.outputs["Color"], bg.inputs["Color"])
    else:
        r, g, b = w["background_color"]
        bg.inputs["Color"].default_value = (r, g, b, 1.0)


# --- step 9: render settings -----------------------------------------------------

def apply_render_settings(scene: bpy.types.Scene, render: dict[str, Any],
                          engine_override: str | None = None) -> None:
    engine = engine_override or render["engine"]
    scene.render.engine = engine
    scene.render.resolution_x, scene.render.resolution_y = render["resolution"]
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = bool(render.get("film_transparent", False))
    scene.render.image_settings.file_format = render.get("file_format", "PNG")
    scene.render.image_settings.color_mode = "RGBA" if scene.render.film_transparent else "RGB"
    scene.render.use_file_extension = True
    scene.render.use_overwrite = False       # frames are resumable
    scene.render.use_placeholder = False
    samples = render["samples"]
    if engine == "BLENDER_EEVEE":
        scene.eevee.taa_render_samples = samples
    elif engine == "CYCLES":
        scene.cycles.samples = samples
        scene.cycles.use_denoising = True
    elif engine == "BLENDER_WORKBENCH":
        scene.display.shading.light = "STUDIO"
        scene.display.shading.color_type = "MATERIAL"
        scene.display.shading.show_shadows = True
        scene.display.shading.show_cavity = True
        scene.display.render_aa = "OFF"
    else:
        raise RuntimeError(f"unknown render engine {engine}")


def output_path(render: dict[str, Any]) -> str:
    out = render["output"]
    return out if os.path.isabs(out) else os.path.join(str(ROOT), out)


# --- saving ------------------------------------------------------------------------

def save_blend(path: str) -> None:
    """Write to a fresh path. Never overwrite something the user has open by hand."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=path, copy=True, compress=True)
