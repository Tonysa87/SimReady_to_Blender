# SPDX-License-Identifier: GPL-3.0-or-later
"""Backend: build a Blender Principled BSDF node tree from an OpenPBRMaterial.

When Blender gains a native OpenPBR node, a second backend can be added here
without touching the source-material mapping.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import bpy

from .openpbr import OpenPBRMaterial, Param, TexRef

ImageLoader = Callable[[str, str], "bpy.types.Image | None"]  # (path, colorspace) -> image


class _Builder:
    def __init__(self, material: bpy.types.Material, load_image: ImageLoader) -> None:
        self.mat = material
        self.nt = material.node_tree
        self.load_image = load_image
        self.nt.nodes.clear()
        self.out = self._node("ShaderNodeOutputMaterial", 600, 0)
        self.bsdf = self._node("ShaderNodeBsdfPrincipled", 250, 0)
        self.nt.links.new(self.bsdf.outputs["BSDF"], self.out.inputs["Surface"])
        self._uv_socket = None
        self._images: dict[tuple[str, str], bpy.types.Node] = {}
        self._channels: dict[tuple[str, str, str], bpy.types.NodeSocket] = {}
        self._row = 0

    # -- helpers -------------------------------------------------------------

    def _node(self, kind: str, x: float, y: float) -> bpy.types.Node:
        n = self.nt.nodes.new(kind)
        n.location = (x, y)
        return n

    def _next_y(self) -> float:
        self._row += 1
        return 300 - self._row * 280

    def link(self, src: bpy.types.NodeSocket, dst: bpy.types.NodeSocket) -> None:
        self.nt.links.new(src, dst)

    def uv_socket(self, mat: OpenPBRMaterial) -> bpy.types.NodeSocket:
        if self._uv_socket is None:
            uv = self._node("ShaderNodeUVMap", -1300, 0)  # empty name -> active render UV ('st')
            sock = uv.outputs["UV"]
            if not mat.uv.is_identity:
                mp = self._node("ShaderNodeMapping", -1100, 0)
                mp.vector_type = 'POINT'
                mp.inputs["Location"].default_value = (*mat.uv.translate, 0.0)
                mp.inputs["Rotation"].default_value = (0.0, 0.0, math.radians(mat.uv.rotate_deg))
                mp.inputs["Scale"].default_value = (*mat.uv.scale, 1.0)
                self.link(sock, mp.inputs["Vector"])
                sock = mp.outputs["Vector"]
            self._uv_socket = sock
        return self._uv_socket

    def image_node(self, tex: TexRef, mat: OpenPBRMaterial) -> bpy.types.Node | None:
        key = (tex.path, tex.colorspace)
        if key in self._images:
            return self._images[key]
        img = self.load_image(tex.path, tex.colorspace)
        if img is None:
            mat.notes.append(f"could not load texture {tex.path}")
            return None
        n = self._node("ShaderNodeTexImage", -800, self._next_y())
        n.image = img
        n.interpolation = 'Linear'
        self.link(self.uv_socket(mat), n.inputs["Vector"])
        self._images[key] = n
        return n

    def channel(self, tex: TexRef, mat: OpenPBRMaterial) -> bpy.types.NodeSocket | None:
        key = (tex.path, tex.colorspace, tex.channel)
        if key in self._channels:
            return self._channels[key]
        img = self.image_node(tex, mat)
        if img is None:
            return None
        x, y = img.location.x + 300, img.location.y
        if tex.channel == "rgb":
            sock = img.outputs["Color"]
        elif tex.channel == "a":
            sock = img.outputs["Alpha"]
        elif tex.channel in ("r", "g", "b"):
            sep = self._separate(img, (x, y))
            sock = sep.outputs[{"r": "Red", "g": "Green", "b": "Blue"}[tex.channel]]
        elif tex.channel == "luminance":
            bw = self._node("ShaderNodeRGBToBW", x, y)
            self.link(img.outputs["Color"], bw.inputs["Color"])
            sock = bw.outputs["Val"]
        else:  # average
            sep = self._separate(img, (x, y))
            add1 = self._math("ADD", x + 150, y)
            self.link(sep.outputs["Red"], add1.inputs[0])
            self.link(sep.outputs["Green"], add1.inputs[1])
            add2 = self._math("ADD", x + 300, y)
            self.link(add1.outputs[0], add2.inputs[0])
            self.link(sep.outputs["Blue"], add2.inputs[1])
            div = self._math("DIVIDE", x + 450, y)
            self.link(add2.outputs[0], div.inputs[0])
            div.inputs[1].default_value = 3.0
            sock = div.outputs[0]
        self._channels[key] = sock
        return sock

    def _separate(self, img: bpy.types.Node, loc: tuple[float, float]) -> bpy.types.Node:
        for link in img.outputs["Color"].links:
            if link.to_node.bl_idname == "ShaderNodeSeparateColor":
                return link.to_node
        sep = self._node("ShaderNodeSeparateColor", *loc)
        self.link(img.outputs["Color"], sep.inputs["Color"])
        return sep

    def _math(self, op: str, x: float, y: float) -> bpy.types.Node:
        n = self._node("ShaderNodeMath", x, y)
        n.operation = op
        return n

    # -- parameters ----------------------------------------------------------

    def scalar(self, p: Param, target: bpy.types.NodeSocket, mat: OpenPBRMaterial) -> None:
        target.default_value = float(p.constant)
        if not p.uses_texture:
            return
        sock = self.channel(p.texture, mat)
        if sock is None:
            return
        y = sock.node.location.y
        if p.tex_scale != 1.0 or p.tex_offset != 0.0:
            ma = self._math("MULTIPLY_ADD", 0, y)
            self.link(sock, ma.inputs[0])
            ma.inputs[1].default_value = p.tex_scale
            ma.inputs[2].default_value = p.tex_offset
            sock = ma.outputs[0]
        if p.influence < 1.0:
            mix = self._node("ShaderNodeMix", 50, y)
            mix.data_type = 'FLOAT'
            mix.inputs["Factor"].default_value = p.influence
            mix.inputs["A"].default_value = float(p.constant)
            self.link(sock, mix.inputs["B"])
            sock = mix.outputs["Result"]
        self.link(sock, target)

    def color(self, p: Param, target: bpy.types.NodeSocket, mat: OpenPBRMaterial) -> None:
        const = tuple(p.constant)
        target.default_value = (*const, 1.0)
        sock = None
        y = 300
        if p.uses_texture:
            sock = self.channel(p.texture, mat)
        if sock is not None:
            y = sock.node.location.y
            if p.tex_scale != 1.0 or p.tex_offset != 0.0:
                vm = self._node("ShaderNodeVectorMath", -150, y)
                vm.operation = 'MULTIPLY_ADD'
                self.link(sock, vm.inputs[0])
                vm.inputs[1].default_value = (p.tex_scale,) * 3
                vm.inputs[2].default_value = (p.tex_offset,) * 3
                sock = vm.outputs["Vector"]
            if p.influence < 1.0:
                mix = self._node("ShaderNodeMix", -50, y)
                mix.data_type = 'RGBA'
                mix.inputs["Factor"].default_value = p.influence
                mix.inputs[6].default_value = (*const, 1.0)  # A (color)
                self.link(sock, mix.inputs[7])  # B (color)
                sock = mix.outputs[2]
        if p.multiply is not None:
            mul = self._node("ShaderNodeMix", 50, y)
            mul.data_type = 'RGBA'
            mul.blend_type = 'MULTIPLY'
            mul.inputs["Factor"].default_value = 1.0
            if sock is not None:
                self.link(sock, mul.inputs[6])
            else:
                mul.inputs[6].default_value = (*const, 1.0)
            mul.inputs[7].default_value = (*p.multiply, 1.0)
            sock = mul.outputs[2]
        if sock is not None:
            self.link(sock, target)

    def normal(self, mat: OpenPBRMaterial) -> None:
        nm = mat.geometry_normal
        if nm is None:
            return
        img = self.image_node(nm.texture, mat)
        if img is None:
            return
        y = img.location.y
        sock = img.outputs["Color"]
        if nm.flip_red:
            # The Normal Map node has no red-axis option: invert R by hand (rare).
            sep = self._node("ShaderNodeSeparateColor", -450, y)
            comb = self._node("ShaderNodeCombineColor", -150, y)
            self.link(sock, sep.inputs["Color"])
            inv = self._math("SUBTRACT", -300, y)
            inv.inputs[0].default_value = 1.0
            self.link(sep.outputs["Red"], inv.inputs[1])
            self.link(inv.outputs[0], comb.inputs["Red"])
            self.link(sep.outputs["Green"], comb.inputs["Green"])
            self.link(sep.outputs["Blue"], comb.inputs["Blue"])
            sock = comb.outputs["Color"]
        nmap = self._node("ShaderNodeNormalMap", 0, y)
        nmap.space = 'TANGENT'
        # Blender 5.x Normal Map node handles the green-axis convention itself.
        nmap.convention = 'DIRECTX' if nm.flip_green else 'OPENGL'
        nmap.inputs["Strength"].default_value = nm.strength
        self.link(sock, nmap.inputs["Color"])
        self.link(nmap.outputs["Normal"], self.bsdf.inputs["Normal"])

    # -- main ----------------------------------------------------------------

    def build(self, m: OpenPBRMaterial) -> None:
        b = self.bsdf.inputs
        self.color(m.base_color, b["Base Color"], m)
        self.scalar(m.base_metalness, b["Metallic"], m)
        self.scalar(m.specular_roughness, b["Roughness"], m)
        b["IOR"].default_value = m.specular_ior
        b["Specular IOR Level"].default_value = m.specular_weight / 2.0
        b["Transmission Weight"].default_value = m.transmission_weight
        b["Thin Wall"].default_value = m.geometry_thin_walled
        if m.coat_weight > 0.0:
            b["Coat Weight"].default_value = m.coat_weight
            b["Coat Roughness"].default_value = m.coat_roughness
            b["Coat IOR"].default_value = m.coat_ior
            b["Coat Tint"].default_value = (*m.coat_color, 1.0)
        if m.emission_color is not None:
            self.color(m.emission_color, b["Emission Color"], m)
            b["Emission Strength"].default_value = m.emission_luminance
        if m.geometry_opacity is not None:
            p = m.geometry_opacity
            if m.opacity_threshold > 0.0:
                # MDL: opacity >= threshold -> 1, else 0.
                if p.uses_texture:
                    sock = self.channel(p.texture, m)
                    gt = self._math("GREATER_THAN", 0, -900)
                    self.link(sock, gt.inputs[0])
                    gt.inputs[1].default_value = m.opacity_threshold - 1e-6
                    self.link(gt.outputs[0], b["Alpha"])
                else:
                    b["Alpha"].default_value = 1.0 if float(p.constant) >= m.opacity_threshold else 0.0
            else:
                self.scalar(p, b["Alpha"], m)
        self.normal(m)
        self.mat["simready_source_model"] = m.source_model
        if m.notes:
            self.mat["simready_notes"] = "; ".join(m.notes)


def build_principled(material: bpy.types.Material, m: OpenPBRMaterial, load_image: ImageLoader) -> None:
    material.use_nodes = True
    _Builder(material, load_image).build(m)
