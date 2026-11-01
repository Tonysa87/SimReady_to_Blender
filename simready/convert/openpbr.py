# SPDX-License-Identifier: GPL-3.0-or-later
"""Renderer-neutral material description using OpenPBR parameter names.

Source materials (OmniPBR, OmniGlass, ...) are first translated into an
`OpenPBRMaterial`; a backend then builds the actual shading network (today a
Blender Principled BSDF, later possibly an OpenPBR node or MaterialX).

Pure Python: no `bpy`, no `pxr`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

Color = tuple[float, float, float]


@dataclass(frozen=True)
class TexRef:
    """A texture lookup.

    channel: 'rgb', 'r', 'g', 'b', 'a', 'luminance' or 'average'.
    colorspace: 'sRGB' (color data) or 'Non-Color' (raw data).
    """

    path: str
    channel: str = "rgb"
    colorspace: str = "Non-Color"


@dataclass
class Param:
    """value = lerp(constant, texture, influence) * multiply (+ offset).

    With no texture (or influence 0) the value is just the constant.
    `scale`/`offset` are applied to the texture lookup before the lerp.
    """

    constant: float | Color
    texture: TexRef | None = None
    influence: float = 1.0
    multiply: Color | None = None
    tex_scale: float = 1.0
    tex_offset: float = 0.0

    @property
    def uses_texture(self) -> bool:
        return self.texture is not None and self.influence > 0.0


@dataclass
class NormalMap:
    texture: TexRef
    strength: float = 1.0
    flip_green: bool = False  # True for DirectX-style maps (Blender expects OpenGL)
    flip_red: bool = False


@dataclass
class UVTransform:
    scale: tuple[float, float] = (1.0, 1.0)
    rotate_deg: float = 0.0
    translate: tuple[float, float] = (0.0, 0.0)

    @property
    def is_identity(self) -> bool:
        return self.scale == (1.0, 1.0) and self.rotate_deg == 0.0 and self.translate == (0.0, 0.0)


@dataclass
class OpenPBRMaterial:
    name: str
    source_model: str  # e.g. 'OmniPBR', 'OmniGlass'

    base_color: Param = field(default_factory=lambda: Param((0.8, 0.8, 0.8)))
    base_metalness: Param = field(default_factory=lambda: Param(0.0))
    specular_weight: float = 1.0  # 1.0 == "physically neutral" (F0 0.04 at IOR 1.5)
    specular_roughness: Param = field(default_factory=lambda: Param(0.5))
    specular_ior: float = 1.5

    transmission_weight: float = 0.0
    transmission_color: Color = (1.0, 1.0, 1.0)

    coat_weight: float = 0.0
    coat_color: Color = (1.0, 1.0, 1.0)
    coat_roughness: float = 0.0
    coat_ior: float = 1.5

    emission_color: Param | None = None
    emission_luminance: float = 0.0

    geometry_opacity: Param | None = None  # None == fully opaque
    opacity_threshold: float = 0.0  # > 0 -> alpha clip
    geometry_thin_walled: bool = False
    geometry_normal: NormalMap | None = None

    uv: UVTransform = field(default_factory=UVTransform)

    notes: list[str] = field(default_factory=list)
