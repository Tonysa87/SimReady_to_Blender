# SPDX-License-Identifier: GPL-3.0-or-later
"""USD import hook: convert Omniverse MDL materials (OmniPBR/OmniGlass) ourselves.

Blender's USD importer calls `material_import_poll` for every material; when it
returns True, the importer skips its UsdPreviewSurface translation (which loses
e.g. diffuse_tint and glass) and calls `on_material_import` with an empty
Blender material to fill. Other materials keep the native import.
"""

from __future__ import annotations

from pathlib import PurePath

import bpy
from pxr import Sdf, UsdShade

from .blender_material import ImageLoader, build_principled
from .omni_mapping import TextureInput, map_material
from .openpbr import UVTransform
from .preview_mapping import PreviewTex, map_preview_surface

SUPPORTED_MODELS = {"OmniPBR", "OmniGlass"}

# Set by the converter before running the import.
image_loader: ImageLoader | None = None
normal_flip_override: bool | None = None  # testing only: force the green-channel flip
report: dict = {"hook": [], "native": []}


def mdl_shader(usd_material: UsdShade.Material) -> tuple[UsdShade.Shader, str] | None:
    """Return (shader, model) of the MDL surface, if it is a supported Omni template."""
    out = usd_material.GetSurfaceOutput("mdl")
    if not out or not out.HasConnectedSource():
        return None
    sources = out.GetConnectedSources()[0]
    if not sources:
        return None
    shader = UsdShade.Shader(sources[0].source.GetPrim())
    prim = shader.GetPrim()
    model = prim.GetAttribute("info:mdl:sourceAsset:subIdentifier").Get() or ""
    if model not in SUPPORTED_MODELS:
        asset = prim.GetAttribute("info:mdl:sourceAsset").Get()
        stem = PurePath(asset.path).stem if asset else ""
        model = stem if stem in SUPPORTED_MODELS else model
    return (shader, model) if model in SUPPORTED_MODELS else None


def preview_shader(usd_material: UsdShade.Material) -> UsdShade.Shader | None:
    out = usd_material.GetSurfaceOutput()
    if not out:
        return None
    attrs = UsdShade.Utils.GetValueProducingAttributes(out)
    if not attrs:
        return None
    shader = UsdShade.Shader(attrs[0].GetPrim())
    return shader if shader and shader.GetIdAttr().Get() == "UsdPreviewSurface" else None


def _resolve(import_context, value) -> str:
    if not isinstance(value, Sdf.AssetPath):
        return ""
    path = value.resolvedPath
    if not path and value.path and import_context is not None:
        local, _is_tmp = import_context.import_texture(value.path)
        path = local if local and local != value.path else ""
    return path


def _tuple(v, n: int, default: float) -> tuple:
    if v is None:
        return (default,) * n
    return tuple(float(x) for x in v)[:n]


def read_preview_inputs(import_context, shader: UsdShade.Shader) -> dict:
    """{input: constant | PreviewTex}, following connections through node graphs."""
    result = {}
    for inp in shader.GetInputs():
        name = inp.GetBaseName()
        attrs = UsdShade.Utils.GetValueProducingAttributes(inp)
        if not attrs:
            continue
        attr = attrs[0]
        if UsdShade.Utils.GetType(attr.GetName()) != UsdShade.AttributeType.Output:
            v = attr.Get()
            result[name] = tuple(float(x) for x in v) if hasattr(v, "__len__") and not isinstance(v, str) else v
            continue
        tex = UsdShade.Shader(attr.GetPrim())
        if tex.GetIdAttr().Get() != "UsdUVTexture":
            result[name] = None
            continue
        channel = UsdShade.Utils.GetBaseNameAndType(attr.GetName())[0]
        get = lambda n: tex.GetInput(n).Get() if tex.GetInput(n) else None  # noqa: E731
        uv = None
        st = tex.GetInput("st")
        if st:
            st_attrs = UsdShade.Utils.GetValueProducingAttributes(st)
            if st_attrs:
                xf = UsdShade.Shader(st_attrs[0].GetPrim())
                if xf.GetIdAttr().Get() == "UsdTransform2d":
                    g = lambda n: xf.GetInput(n).Get() if xf.GetInput(n) else None  # noqa: E731
                    uv = UVTransform(scale=_tuple(g("scale"), 2, 1.0), rotate_deg=float(g("rotation") or 0.0),
                                     translate=_tuple(g("translation"), 2, 0.0))
        result[name] = PreviewTex(
            path=_resolve(import_context, get("file")),
            colorspace=str(get("sourceColorSpace") or "auto"),
            channel=channel,
            scale=_tuple(get("scale"), 4, 1.0),
            bias=_tuple(get("bias"), 4, 0.0),
            uv=uv,
        )
    return result


def read_inputs(import_context, shader: UsdShade.Shader) -> dict:
    inputs = {}
    for inp in shader.GetInputs():
        value = inp.Get()
        if isinstance(value, Sdf.AssetPath):
            path = value.resolvedPath
            if not path and value.path and import_context is not None:
                local, _is_tmp = import_context.import_texture(value.path)
                path = local if local and local != value.path else ""
            value = TextureInput(path, inp.GetAttr().GetColorSpace() or "")
        elif hasattr(value, "__len__") and not isinstance(value, str):
            value = tuple(float(x) for x in value)
        inputs[inp.GetBaseName()] = value
    return inputs


class SimReadyUSDHook(bpy.types.USDHook):
    """Convert OmniPBR/OmniGlass MDL materials to Principled BSDF (via OpenPBR params)."""

    bl_idname = "simready_usd_hook"
    bl_label = "SimReady MDL materials"

    @staticmethod
    def material_import_poll(import_context, usd_material):
        found = mdl_shader(usd_material) is not None or preview_shader(usd_material) is not None
        if not found:
            report["native"].append(str(usd_material.GetPath()))
        return found

    @staticmethod
    def on_material_import(import_context, bl_material, usd_material):
        found = mdl_shader(usd_material)
        if found is not None:
            shader, model = found
            m = map_material(model, bl_material.name, read_inputs(import_context, shader))
        else:
            model = "UsdPreviewSurface"
            shader = preview_shader(usd_material)
            m = map_preview_surface(bl_material.name, read_preview_inputs(import_context, shader))
        if m is None or image_loader is None:
            return False
        if normal_flip_override is not None and m.geometry_normal is not None:
            m.geometry_normal.flip_green = normal_flip_override
        build_principled(bl_material, m, image_loader)
        report["hook"].append({"material": bl_material.name, "model": model, "notes": m.notes})
        return True


def register() -> None:
    bpy.utils.register_class(SimReadyUSDHook)


def unregister() -> None:
    bpy.utils.unregister_class(SimReadyUSDHook)
