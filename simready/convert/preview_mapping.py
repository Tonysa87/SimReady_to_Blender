# SPDX-License-Identifier: GPL-3.0-or-later
"""Translate a UsdPreviewSurface network to OpenPBRMaterial (pure Python).

Used for SimReady materials that ship without MDL. The network is pre-read by
the caller into {input_name: constant | PreviewTex}.

SimReady normal maps are DirectX-style (measured on the dataset, consistent
with flip_tangent_v=true in the MDL), also when referenced by UsdPreviewSurface,
so the green channel is flipped by default (`directx_normals=True`).
"""

from __future__ import annotations

from typing import Any, NamedTuple

from .openpbr import NormalMap, OpenPBRMaterial, Param, TexRef, UVTransform


class PreviewTex(NamedTuple):
    path: str               # resolved file path ('' if unresolved)
    colorspace: str         # UsdUVTexture sourceColorSpace: 'sRGB', 'raw', 'auto'
    channel: str            # connected output: 'rgb', 'r', 'g', 'b', 'a'
    scale: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)
    bias: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    uv: UVTransform | None = None


def _ref(t: PreviewTex, color: bool) -> TexRef:
    if color:
        cs = "Non-Color" if t.colorspace == "raw" else "sRGB"
    else:
        cs = "sRGB" if t.colorspace == "sRGB" else "Non-Color"
    return TexRef(t.path, t.channel, cs)


def _scalar(v: Any, default: float, mat: OpenPBRMaterial, name: str) -> Param:
    if isinstance(v, PreviewTex):
        if not v.path:
            mat.notes.append(f"texture for '{name}' could not be resolved: ignored")
            return Param(default)
        idx = {"r": 0, "g": 1, "b": 2, "a": 3}.get(v.channel, 0)
        return Param(constant=default, texture=_ref(v, color=False), influence=1.0,
                     tex_scale=v.scale[idx], tex_offset=v.bias[idx])
    return Param(float(default if v is None else v))


def map_preview_surface(name: str, inputs: dict[str, Any], directx_normals: bool = True) -> OpenPBRMaterial:
    mat = OpenPBRMaterial(name=name, source_model="UsdPreviewSurface")

    # UV transform: take the first texture that carries one.
    for v in inputs.values():
        if isinstance(v, PreviewTex) and v.uv is not None:
            mat.uv = v.uv
            break

    dc = inputs.get("diffuseColor")
    if isinstance(dc, PreviewTex) and dc.path:
        mat.base_color = Param(constant=(0.0, 0.0, 0.0), texture=_ref(dc, color=True))
        if dc.scale[:3] != (1.0, 1.0, 1.0):
            mat.base_color.multiply = tuple(float(x) for x in dc.scale[:3])
        if any(dc.bias[:3]):
            mat.notes.append("diffuse texture bias not applied")
    else:
        if isinstance(dc, PreviewTex):
            mat.notes.append("diffuseColor texture could not be resolved: ignored")
            dc = None
        mat.base_color = Param(constant=tuple(float(x) for x in (dc or (0.18, 0.18, 0.18))))

    mat.specular_roughness = _scalar(inputs.get("roughness"), 0.5, mat, "roughness")
    mat.base_metalness = _scalar(inputs.get("metallic"), 0.0, mat, "metallic")
    if inputs.get("useSpecularWorkflow"):
        mat.notes.append("specular workflow not supported: using metallic workflow")
    mat.specular_ior = float(inputs.get("ior") or 1.5)

    op = inputs.get("opacity")
    if op is not None and (isinstance(op, PreviewTex) or float(op) < 1.0):
        mat.geometry_opacity = _scalar(op, 1.0, mat, "opacity")
        mat.opacity_threshold = float(inputs.get("opacityThreshold") or 0.0)

    em = inputs.get("emissiveColor")
    if isinstance(em, PreviewTex) and em.path:
        mat.emission_color = Param(constant=(0.0, 0.0, 0.0), texture=_ref(em, color=True))
        mat.emission_luminance = 1.0
    elif em is not None and not isinstance(em, PreviewTex) and any(float(x) > 0 for x in em):
        mat.emission_color = Param(constant=tuple(float(x) for x in em))
        mat.emission_luminance = 1.0

    if float(inputs.get("clearcoat") or 0.0) > 0.0:
        mat.coat_weight = float(inputs["clearcoat"])
        mat.coat_roughness = float(inputs.get("clearcoatRoughness") or 0.01)

    nor = inputs.get("normal")
    if isinstance(nor, PreviewTex) and nor.path:
        if nor.scale[:3] != (2.0, 2.0, 2.0) or nor.bias[:3] != (-1.0, -1.0, -1.0):
            mat.notes.append(f"unusual normal map scale/bias {nor.scale}/{nor.bias}")
        mat.geometry_normal = NormalMap(TexRef(nor.path, "rgb", "Non-Color"), strength=1.0,
                                        flip_green=directx_normals)
    return mat
