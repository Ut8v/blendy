"""Procedural material recipes -> node trees. No image textures: detail is
noise, grunge, scratches and bump the agent can tune by number."""

from __future__ import annotations

from typing import Any

import bpy


def _link(links, a, b):
    links.new(a, b)


def build_material(name: str, m: dict[str, Any]) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    _link(links, bsdf.outputs["BSDF"], out.inputs["Surface"])
    base = tuple(m.get("base_color", (0.8, 0.8, 0.8))) + (1.0,)
    bsdf.inputs["Base Color"].default_value = base
    bsdf.inputs["Roughness"].default_value = m.get("roughness", 0.5)
    bsdf.inputs["Metallic"].default_value = m.get("metallic", 0.0)
    if "specular" in m and "Specular IOR Level" in bsdf.inputs:
        bsdf.inputs["Specular IOR Level"].default_value = m["specular"]
    if "subsurface" in m and "Subsurface Weight" in bsdf.inputs:
        bsdf.inputs["Subsurface Weight"].default_value = m["subsurface"]
        if "subsurface_color" in m and "Subsurface Radius" in bsdf.inputs:
            r, g, b = m["subsurface_color"]
            bsdf.inputs["Subsurface Radius"].default_value = (r, g, b)
    if "sheen" in m and "Sheen Weight" in bsdf.inputs:
        bsdf.inputs["Sheen Weight"].default_value = m["sheen"]
    if "emission" in m:
        bsdf.inputs["Emission Color"].default_value = tuple(m["emission"]) + (1.0,)
        bsdf.inputs["Emission Strength"].default_value = m.get("emission_strength", 1.0)
    mat.diffuse_color = base
    mat.roughness, mat.metallic = m.get("roughness", 0.5), m.get("metallic", 0.0)

    coords = nodes.new("ShaderNodeTexCoord")
    color_socket = None
    rough_socket = None

    if "noise" in m:
        n = m["noise"]
        tex = nodes.new("ShaderNodeTexNoise")
        tex.inputs["Scale"].default_value = n["scale"]
        tex.inputs["Detail"].default_value = n.get("detail", 4.0)
        _link(links, coords.outputs["Object"], tex.inputs["Vector"])
        mix = nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        mix.inputs["Factor"].default_value = n["strength"]
        mix.inputs[6].default_value = base
        mix.inputs[7].default_value = tuple(n.get("color", (0.5, 0.5, 0.5))) + (1.0,)
        ramp = nodes.new("ShaderNodeMapRange")
        ramp.inputs["From Min"].default_value, ramp.inputs["From Max"].default_value = 0.35, 0.65
        _link(links, tex.outputs["Fac"], ramp.inputs["Value"])
        mul = nodes.new("ShaderNodeMath")
        mul.operation = "MULTIPLY"
        mul.inputs[1].default_value = n["strength"]
        _link(links, ramp.outputs["Result"], mul.inputs[0])
        _link(links, mul.outputs["Value"], mix.inputs["Factor"])
        color_socket = mix.outputs[2]

    if "grunge" in m:
        g = m["grunge"]
        tex = nodes.new("ShaderNodeTexNoise")
        tex.inputs["Scale"].default_value = g.get("scale", 3.0)
        tex.inputs["Detail"].default_value = 6.0
        tex.inputs["Roughness"].default_value = 0.7
        _link(links, coords.outputs["Object"], tex.inputs["Vector"])
        ramp = nodes.new("ShaderNodeMapRange")
        ramp.inputs["From Min"].default_value, ramp.inputs["From Max"].default_value = 0.4, 0.7
        _link(links, tex.outputs["Fac"], ramp.inputs["Value"])
        dark = nodes.new("ShaderNodeMix")
        dark.data_type = "RGBA"
        dark.inputs[7].default_value = (0.05, 0.04, 0.035, 1.0)
        if color_socket is not None:
            _link(links, color_socket, dark.inputs[6])
        else:
            dark.inputs[6].default_value = base
        fac = nodes.new("ShaderNodeMath")
        fac.operation = "MULTIPLY"
        fac.inputs[1].default_value = g["strength"]
        _link(links, ramp.outputs["Result"], fac.inputs[0])
        _link(links, fac.outputs["Value"], dark.inputs["Factor"])
        color_socket = dark.outputs[2]
        rough = nodes.new("ShaderNodeMath")
        rough.operation = "ADD"
        rough.inputs[0].default_value = m.get("roughness", 0.5)
        rough_mul = nodes.new("ShaderNodeMath")
        rough_mul.operation = "MULTIPLY"
        rough_mul.inputs[1].default_value = 0.5 * g["strength"]
        _link(links, ramp.outputs["Result"], rough_mul.inputs[0])
        _link(links, rough_mul.outputs["Value"], rough.inputs[1])
        rough_socket = rough.outputs["Value"]

    if "scratches" in m:
        s = m["scratches"]
        wave = nodes.new("ShaderNodeTexWave")
        wave.wave_type, wave.bands_direction = "BANDS", "DIAGONAL"
        wave.inputs["Scale"].default_value = s.get("scale", 40.0)
        wave.inputs["Distortion"].default_value = 8.0
        wave.inputs["Detail"].default_value = 3.0
        _link(links, coords.outputs["Object"], wave.inputs["Vector"])
        ramp = nodes.new("ShaderNodeMapRange")
        ramp.inputs["From Min"].default_value, ramp.inputs["From Max"].default_value = 0.85, 1.0
        _link(links, wave.outputs["Fac"], ramp.inputs["Value"])
        mix = nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        mix.inputs[7].default_value = (0.6, 0.6, 0.6, 1.0)
        if color_socket is not None:
            _link(links, color_socket, mix.inputs[6])
        else:
            mix.inputs[6].default_value = base
        fac = nodes.new("ShaderNodeMath")
        fac.operation = "MULTIPLY"
        fac.inputs[1].default_value = s["strength"]
        _link(links, ramp.outputs["Result"], fac.inputs[0])
        _link(links, fac.outputs["Value"], mix.inputs["Factor"])
        color_socket = mix.outputs[2]

    if color_socket is not None:
        _link(links, color_socket, bsdf.inputs["Base Color"])
    if rough_socket is not None:
        _link(links, rough_socket, bsdf.inputs["Roughness"])

    if "bump" in m:
        b = m["bump"]
        tex = nodes.new("ShaderNodeTexNoise")
        tex.inputs["Scale"].default_value = b.get("scale", 20.0)
        tex.inputs["Detail"].default_value = b.get("detail", 6.0)
        _link(links, coords.outputs["Object"], tex.inputs["Vector"])
        bump = nodes.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = b["strength"]
        bump.inputs["Distance"].default_value = 0.01
        _link(links, tex.outputs["Fac"], bump.inputs["Height"])
        _link(links, bump.outputs["Normal"], bsdf.inputs["Normal"])
    return mat
