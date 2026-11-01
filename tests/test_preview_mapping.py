# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for UsdPreviewSurface -> OpenPBR mapping (pure Python)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simready.convert.openpbr import UVTransform  # noqa: E402
from simready.convert.preview_mapping import PreviewTex as P, map_preview_surface  # noqa: E402

N_SCALE, N_BIAS = (2.0, 2.0, 2.0, 2.0), (-1.0, -1.0, -1.0, -1.0)

BOWL = {  # kitchenware_bowl_b01 (preview only, no MDL)
    "diffuseColor": P("/x/bowl_alb.png", "sRGB", "rgb"),
    "roughness": P("/x/bowl_orm.png", "raw", "g"),
    "metallic": P("/x/bowl_orm.png", "raw", "b"),
    "normal": P("/x/bowl_nor.png", "raw", "rgb", N_SCALE, N_BIAS),
}


class PreviewTests(unittest.TestCase):
    def test_bowl(self):
        m = map_preview_surface("bowl", BOWL)
        self.assertEqual(m.base_color.texture.colorspace, "sRGB")
        self.assertEqual(m.specular_roughness.texture.channel, "g")
        self.assertEqual(m.specular_roughness.texture.colorspace, "Non-Color")
        self.assertEqual(m.base_metalness.texture.channel, "b")
        self.assertTrue(m.geometry_normal.flip_green)  # SimReady maps are DirectX
        self.assertEqual(m.notes, [])

    def test_opengl_option(self):
        self.assertFalse(map_preview_surface("x", BOWL, directx_normals=False).geometry_normal.flip_green)

    def test_constants_and_opacity(self):
        m = map_preview_surface("x", {"diffuseColor": (0.5, 0.2, 0.1), "roughness": 0.3, "metallic": 1.0,
                                      "opacity": 0.4, "opacityThreshold": 0.5, "ior": 1.33})
        self.assertEqual(m.base_color.constant, (0.5, 0.2, 0.1))
        self.assertEqual((m.specular_roughness.constant, m.base_metalness.constant), (0.3, 1.0))
        self.assertEqual((m.geometry_opacity.constant, m.opacity_threshold), (0.4, 0.5))
        self.assertEqual(m.specular_ior, 1.33)

    def test_uv_transform_and_scale(self):
        uv = UVTransform(scale=(3.0, 3.0))
        m = map_preview_surface("x", {"diffuseColor": P("/a.png", "auto", "rgb", (0.5, 0.5, 0.5, 1.0), uv=uv)})
        self.assertEqual(m.uv.scale, (3.0, 3.0))
        self.assertEqual(m.base_color.multiply, (0.5, 0.5, 0.5))

    def test_unresolved(self):
        m = map_preview_surface("x", {"diffuseColor": P("", "sRGB", "rgb"), "roughness": P("", "raw", "g")})
        self.assertIsNone(m.base_color.texture)
        self.assertEqual(len(m.notes), 2)


if __name__ == "__main__":
    unittest.main()
