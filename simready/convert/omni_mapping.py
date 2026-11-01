# SPDX-License-Identifier: GPL-3.0-or-later
"""Translate NVIDIA Omniverse MDL materials (OmniPBR, OmniGlass) to OpenPBRMaterial.

Pure Python. Inputs are plain dicts {mdl_input_name: value}; texture inputs are
`TextureInput(resolved_path, declared_colorspace)` (path is '' when unresolved).

The OmniPBR semantics follow NVIDIA's own MDL source shipped with the assets
(OmniPBR.mdl -> OmniPBR_ClearCoat.mdl -> OmniPBRBase.mdl, version 2.1.2):

- base color   = (texture * albedo_brightness + albedo_add  |  diffuse_color_constant) * diffuse_tint
- roughness    = lerp(reflection_roughness_constant, ORM.g | roughness_tex.r, reflection_roughness_texture_influence)
- metallic     = lerp(metallic_constant, ORM.b | metallic_tex.r, metallic_texture_influence)
- normal map   : flip_tangent_v "true for dx, false for opengl" (comment in NVIDIA's code)
- roughness is squared inside OmniPBRBase, like Blender's Principled BSDF -> map 1:1
- specular_level 0.5 == F0 0.04 == Principled "Specular IOR Level" 0.5
- uv transform (texture_scale/rotate/translate) is applied to every lookup
"""

from __future__ import annotations

from typing import Any, NamedTuple

from .openpbr import Color, NormalMap, OpenPBRMaterial, Param, TexRef, UVTransform


class TextureInput(NamedTuple):
    path: str
    colorspace: str  # as declared on the USD attribute: 'sRGB', 'raw', 'auto' or ''


Inputs = dict[str, Any]

# Defaults from OmniPBR.mdl (code, not docs: e.g. emissive_intensity is 40 in code).
OMNIPBR_DEFAULTS: Inputs = {
    "diffuse_color_constant": (0.2, 0.2, 0.2),
    "albedo_desaturation": 0.0,
    "albedo_add": 0.0,
    "albedo_brightness": 1.0,
    "diffuse_tint": (1.0, 1.0, 1.0),
    "reflection_roughness_constant": 0.5,
    "reflection_roughness_texture_influence": 0.0,
    "metallic_constant": 0.0,
    "metallic_texture_influence": 0.0,
    "specular_level": 0.5,
    "enable_ORM_texture": False,
    "ao_to_diffuse": 0.0,
    "enable_emission": False,
    "emissive_color": (1.0, 0.1, 0.1),
    "emissive_intensity": 40.0,
    "enable_opacity": False,
    "enable_opacity_texture": False,
    "opacity_constant": 1.0,
    "opacity_mode": "mono_average",
    "opacity_threshold": 0.0,
    "bump_factor": 1.0,
    "detail_bump_factor": 0.3,
    "flip_tangent_u": False,
    "flip_tangent_v": True,
    "project_uvw": False,
    "texture_scale": (1.0, 1.0),
    "texture_rotate": 0.0,
    "texture_translate": (0.0, 0.0),
    "enable_clearcoat": False,
    "clearcoat_weight": 1.0,
    "clearcoat_tint": (1.0, 1.0, 1.0),
    "clearcoat_reflection_roughness": 0.0,
    "clearcoat_ior": 1.56,
}

# OmniGlass defaults (Omniverse docs; the .mdl is not shipped in the useful file set).
# Current OmniGlass: opacity_constant gated by enable_opacity. Older OmniGlass
# (used by SimReady packages) has `cutout_opacity`, applied unconditionally.
OMNIGLASS_DEFAULTS: Inputs = {
    "glass_color": (1.0, 1.0, 1.0),
    "glass_ior": 1.491,
    "thin_walled": False,
    "frosting_roughness": 0.0,
    "roughness_texture_influence": 1.0,
    "enable_opacity": False,
    "opacity_constant": 1.0,
    "opacity_threshold": 0.0,
    "bump_factor": 1.0,
    "flip_tangent_u": False,
    "flip_tangent_v": True,
    "texture_scale": (1.0, 1.0),
    "texture_rotate": 0.0,
    "texture_translate": (0.0, 0.0),
}

_OPACITY_MODE_CHANNEL = {
    "mono_alpha": "a",
    "mono_average": "average",
    "mono_luminance": "luminance",
    "mono_maximum": "average",  # no exact equivalent; noted
}


def _get(inputs: Inputs, defaults: Inputs, name: str) -> Any:
    v = inputs.get(name)
    return defaults.get(name) if v is None else v


def _tex(inputs: Inputs, name: str) -> TextureInput | None:
    v = inputs.get(name)
    if isinstance(v, TextureInput) and v.path:
        return v
    return None


def _color_cs(t: TextureInput) -> str:
    """Colorspace for a color texture: honour an explicit 'raw', else sRGB."""
    return "Non-Color" if t.colorspace == "raw" else "sRGB"


def _vec2(v: Any) -> tuple[float, float]:
    return (float(v[0]), float(v[1]))


def _color(v: Any) -> Color:
    return (float(v[0]), float(v[1]), float(v[2]))


def _uv(inputs: Inputs, defaults: Inputs, mat: OpenPBRMaterial) -> None:
    mat.uv = UVTransform(
        scale=_vec2(_get(inputs, defaults, "texture_scale")),
        rotate_deg=float(_get(inputs, defaults, "texture_rotate")),
        translate=_vec2(_get(inputs, defaults, "texture_translate")),
    )
    if _get(inputs, defaults, "project_uvw"):
        mat.notes.append("project_uvw (projected UVs) not supported: using mesh UVs")


def _unresolved(inputs: Inputs, mat: OpenPBRMaterial) -> None:
    for name, v in inputs.items():
        if isinstance(v, TextureInput) and not v.path:
            mat.notes.append(f"texture input '{name}' could not be resolved: ignored")


def map_omnipbr(name: str, inputs: Inputs) -> OpenPBRMaterial:
    d = OMNIPBR_DEFAULTS
    mat = OpenPBRMaterial(name=name, source_model="OmniPBR")
    _unresolved(inputs, mat)
    _uv(inputs, d, mat)

    # Base color.
    tint = _color(_get(inputs, d, "diffuse_tint"))
    diffuse = _tex(inputs, "diffuse_texture")
    if diffuse:
        mat.base_color = Param(
            constant=(0.0, 0.0, 0.0),
            texture=TexRef(diffuse.path, "rgb", _color_cs(diffuse)),
            influence=1.0,
            tex_scale=float(_get(inputs, d, "albedo_brightness")),
            tex_offset=float(_get(inputs, d, "albedo_add")),
        )
        if float(_get(inputs, d, "albedo_desaturation")) != 0.0:
            mat.notes.append("albedo_desaturation not applied")
    else:
        mat.base_color = Param(constant=_color(_get(inputs, d, "diffuse_color_constant")))
    if tint != (1.0, 1.0, 1.0):
        mat.base_color.multiply = tint
    if float(_get(inputs, d, "ao_to_diffuse")) > 0.0:
        mat.notes.append("ao_to_diffuse not applied")

    # Roughness / metallic: ORM channels or separate maps (red channel), see module doc.
    use_orm = bool(_get(inputs, d, "enable_ORM_texture"))
    orm = _tex(inputs, "ORM_texture")
    if use_orm and not orm:
        mat.notes.append("enable_ORM_texture set but no ORM texture: lookups read 0")

    def channel_param(const_name: str, infl_name: str, orm_channel: str, sep_tex_name: str) -> Param:
        const = float(_get(inputs, d, const_name))
        infl = float(_get(inputs, d, infl_name))
        if use_orm:
            tex = TexRef(orm.path, orm_channel, "Non-Color") if orm else None
            if tex is None and infl > 0.0:
                # Invalid texture lookups return 0 in MDL.
                return Param(constant=const * (1.0 - infl))
        else:
            sep = _tex(inputs, sep_tex_name)
            tex = TexRef(sep.path, "r", "Non-Color") if sep else None
            if tex is None and infl > 0.0:
                return Param(constant=const * (1.0 - infl))
        return Param(constant=const, texture=tex, influence=infl if tex else 0.0)

    mat.specular_roughness = channel_param(
        "reflection_roughness_constant", "reflection_roughness_texture_influence", "g", "reflectionroughness_texture")
    mat.base_metalness = channel_param(
        "metallic_constant", "metallic_texture_influence", "b", "metallic_texture")
    mat.specular_weight = 2.0 * float(_get(inputs, d, "specular_level"))

    # Normal map.
    nor = _tex(inputs, "normalmap_texture")
    if nor:
        mat.geometry_normal = NormalMap(
            texture=TexRef(nor.path, "rgb", "Non-Color"),
            strength=float(_get(inputs, d, "bump_factor")),
            flip_green=bool(_get(inputs, d, "flip_tangent_v")),
            flip_red=bool(_get(inputs, d, "flip_tangent_u")),
        )
    if _tex(inputs, "detail_normalmap_texture"):
        mat.notes.append("detail normal map not applied")

    # Emission.
    if _get(inputs, d, "enable_emission"):
        etex = _tex(inputs, "emissive_color_texture")
        if etex:
            mat.emission_color = Param(constant=(0.0, 0.0, 0.0), texture=TexRef(etex.path, "rgb", _color_cs(etex)))
        else:
            mat.emission_color = Param(constant=_color(_get(inputs, d, "emissive_color")))
        mat.emission_luminance = float(_get(inputs, d, "emissive_intensity"))
        if _tex(inputs, "emissive_mask_texture"):
            mat.notes.append("emissive_mask_texture not applied")

    # Opacity (cutout).
    if _get(inputs, d, "enable_opacity"):
        otex = _tex(inputs, "opacity_texture")
        mode = str(_get(inputs, d, "opacity_mode"))
        if _get(inputs, d, "enable_opacity_texture") and otex:
            mat.geometry_opacity = Param(
                constant=1.0, texture=TexRef(otex.path, _OPACITY_MODE_CHANNEL.get(mode, "average"), "Non-Color"))
            if mode == "mono_maximum":
                mat.notes.append("opacity_mode mono_maximum approximated with average")
        else:
            mat.geometry_opacity = Param(constant=float(_get(inputs, d, "opacity_constant")))
        mat.opacity_threshold = float(_get(inputs, d, "opacity_threshold"))

    # Clear coat.
    if _get(inputs, d, "enable_clearcoat"):
        mat.coat_weight = float(_get(inputs, d, "clearcoat_weight"))
        mat.coat_color = _color(_get(inputs, d, "clearcoat_tint"))
        mat.coat_roughness = float(_get(inputs, d, "clearcoat_reflection_roughness"))
        mat.coat_ior = float(_get(inputs, d, "clearcoat_ior"))

    return mat


def map_omniglass(name: str, inputs: Inputs) -> OpenPBRMaterial:
    d = OMNIGLASS_DEFAULTS
    mat = OpenPBRMaterial(name=name, source_model="OmniGlass")
    _unresolved(inputs, mat)
    _uv(inputs, d, mat)

    color = _color(_get(inputs, d, "glass_color"))
    ctex = _tex(inputs, "glass_color_texture")
    mat.base_color = Param(constant=color, texture=TexRef(ctex.path, "rgb", _color_cs(ctex)) if ctex else None)
    mat.transmission_weight = 1.0
    mat.transmission_color = color
    mat.specular_ior = float(_get(inputs, d, "glass_ior"))
    mat.base_metalness = Param(0.0)
    mat.geometry_thin_walled = bool(_get(inputs, d, "thin_walled"))

    rough_const = float(_get(inputs, d, "frosting_roughness"))
    rtex = _tex(inputs, "roughness_texture")
    infl_name = "roughness_texture_influence" if "roughness_texture_influence" in inputs \
        else "reflection_roughness_texture_influence"
    infl = float(inputs.get(infl_name, d["roughness_texture_influence"]))
    mat.specular_roughness = Param(
        constant=rough_const,
        texture=TexRef(rtex.path, "r", "Non-Color") if rtex else None,
        influence=infl if rtex else 0.0,
    )

    nor = _tex(inputs, "normalmap_texture") or _tex(inputs, "normal_map_texture")
    if nor:
        strength = inputs.get("bump_factor", inputs.get("normal_map_strength", d["bump_factor"]))
        mat.geometry_normal = NormalMap(
            texture=TexRef(nor.path, "rgb", "Non-Color"),
            strength=float(strength),
            flip_green=bool(_get(inputs, d, "flip_tangent_v")),
            flip_red=bool(_get(inputs, d, "flip_tangent_u")),
        )

    if "cutout_opacity" in inputs:  # legacy parameter: always applied
        mat.geometry_opacity = Param(constant=float(inputs["cutout_opacity"]))
    elif _get(inputs, d, "enable_opacity"):
        otex = _tex(inputs, "opacity_texture")
        mat.geometry_opacity = Param(constant=1.0, texture=TexRef(otex.path, "average", "Non-Color")) if otex \
            else Param(constant=float(_get(inputs, d, "opacity_constant")))
        mat.opacity_threshold = float(_get(inputs, d, "opacity_threshold"))
    return mat


def map_material(model: str, name: str, inputs: Inputs) -> OpenPBRMaterial | None:
    """Dispatch on the MDL sub-identifier. Returns None for unsupported models."""
    if model == "OmniPBR":
        return map_omnipbr(name, inputs)
    if model == "OmniGlass":
        return map_omniglass(name, inputs)
    return None
