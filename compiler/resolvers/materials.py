"""Material overrides: a Principled BSDF per override, applied by slot name."""

from __future__ import annotations

from typing import Any

import bpy


def make_material(name: str, spec: dict[str, Any]) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    if "base_color" in spec:
        r, g, b = spec["base_color"]
        bsdf.inputs["Base Color"].default_value = (r, g, b, 1.0)
        mat.diffuse_color = (r, g, b, 1.0)          # Workbench previews read this
    if "metallic" in spec:
        bsdf.inputs["Metallic"].default_value = spec["metallic"]
        mat.metallic = spec["metallic"]
    if "roughness" in spec:
        bsdf.inputs["Roughness"].default_value = spec["roughness"]
        mat.roughness = spec["roughness"]
    if "emission" in spec:
        r, g, b = spec["emission"]
        bsdf.inputs["Emission Color"].default_value = (r, g, b, 1.0)
        bsdf.inputs["Emission Strength"].default_value = spec.get("emission_strength", 1.0)
    return mat


def apply_overrides(asset_id: str, objects: list[bpy.types.Object],
                    overrides: dict[str, dict[str, Any]], warn) -> None:
    """Replace materials by slot name. "default" targets every slot, and creates a
    slot on objects that have none (primitives)."""
    for slot_name, mat_spec in overrides.items():
        mat = make_material(f"{asset_id}.{slot_name}", mat_spec)
        hit = False
        for obj in objects:
            if obj.type != "MESH":
                continue
            if slot_name == "default":
                if not obj.data.materials:
                    obj.data.materials.append(mat)
                else:
                    for i in range(len(obj.data.materials)):
                        obj.data.materials[i] = mat
                hit = True
            else:
                for i, m in enumerate(obj.data.materials):
                    if m is not None and m.name == slot_name:
                        obj.data.materials[i] = mat
                        hit = True
        if not hit:
            warn(f"{asset_id}: material_overrides slot '{slot_name}' matched nothing")
