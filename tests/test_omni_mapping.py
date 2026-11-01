# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the OmniPBR/OmniGlass -> OpenPBR mapping (pure Python, no Blender).

Run:  python3 -m unittest discover -s tests
Input values are taken from real nvidia/simready-assets materials.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simready.convert.omni_mapping import TextureInput as T, map_material, map_omniglass, map_omnipbr  # noqa: E402

BANANA = {  # food_banana_a01, m_opaque__plant__banana_clean_a01
    "bump_factor": 1.0,
    "diffuse_texture": T("/x/t_banana_alb.png", "sRGB"),
    "emissive_color": (1, 1, 1),
    "emissive_intensity": 0.0,
    "enable_ORM_texture": True,
    "flip_tangent_u": False,
    "flip_tangent_v": True,
    "metallic_texture_influence": 1.0,
    "normalmap_texture": T("/x/t_banana_nor.png", "raw"),
    "opacity_constant": 1.0,
    "ORM_texture": T("/x/t_banana_orm.png", "raw"),
    "reflection_roughness_texture_influence": 1.0,
    "specular_level": 0.5,
}

PAINTED_BLUE = {  # warehouse_conveyorBelt_a01, m_opaque__metal__paintedBlue_glossy_a01
    **BANANA,
    "diffuse_texture": T("/x/t_metal_painted_gray_alb.png", "raw"),
    "diffuse_tint": (0.023153365, 0.046665102, 0.15292618),
}

GOGGLES = {  # ppe_eyeProtection_goggles_a01: ORM disabled, separate maps pointing at the ORM file
    "diffuse_texture": T("/x/goggles_Alb.png", "sRGB"),
    "enable_ORM_texture": False,
    "metallic_texture": T("/x/goggles_ORM.png", "raw"),
    "metallic_texture_influence": 1.0,
    "reflectionroughness_texture": T("/x/goggles_ORM.png", "raw"),
    "reflection_roughness_texture_influence": 1.0,
    "normalmap_texture": T("/x/goggles_Nor.png", "raw"),
    "texture_scale": (1, 1),
}


class OmniPBRTests(unittest.TestCase):
    def test_banana_orm_channels(self):
        m = map_omnipbr("banana", BANANA)
        self.assertEqual(m.base_color.texture.colorspace, "sRGB")
        self.assertIsNone(m.base_color.multiply)
        self.assertEqual((m.specular_roughness.texture.channel, m.specular_roughness.influence), ("g", 1.0))
        self.assertEqual((m.base_metalness.texture.channel, m.base_metalness.influence), ("b", 1.0))
        self.assertEqual(m.specular_roughness.texture.colorspace, "Non-Color")
        self.assertAlmostEqual(m.specular_weight, 1.0)
        self.assertTrue(m.geometry_normal.flip_green)  # flip_tangent_v=True -> DirectX map
        self.assertIsNone(m.emission_color)  # enable_emission not set
        self.assertIsNone(m.geometry_opacity)
        self.assertTrue(m.uv.is_identity)
        self.assertEqual(m.notes, [])

    def test_tint_is_kept(self):
        m = map_omnipbr("blue", PAINTED_BLUE)
        self.assertEqual(m.base_color.multiply, (0.023153365, 0.046665102, 0.15292618))
        self.assertEqual(m.base_color.texture.colorspace, "Non-Color")  # declared 'raw'

    def test_separate_maps_use_red_channel(self):
        m = map_omnipbr("goggles", GOGGLES)
        self.assertEqual(m.specular_roughness.texture.channel, "r")
        self.assertEqual(m.base_metalness.texture.channel, "r")

    def test_defaults_without_textures(self):
        m = map_omnipbr("plain", {})
        self.assertEqual(m.base_color.constant, (0.2, 0.2, 0.2))
        self.assertFalse(m.specular_roughness.uses_texture)
        self.assertEqual(m.specular_roughness.constant, 0.5)
        self.assertEqual(m.base_metalness.constant, 0.0)
        self.assertIsNone(m.geometry_normal)

    def test_orm_enabled_but_missing_reads_zero(self):
        m = map_omnipbr("x", {"enable_ORM_texture": True, "reflection_roughness_constant": 0.8,
                              "reflection_roughness_texture_influence": 0.5})
        self.assertAlmostEqual(m.specular_roughness.constant, 0.4)
        self.assertIsNone(m.specular_roughness.texture)
        self.assertTrue(any("ORM" in n for n in m.notes))

    def test_unresolved_texture_is_ignored_and_noted(self):
        m = map_omnipbr("x", {"diffuse_texture": T("", "sRGB")})
        self.assertIsNone(m.base_color.texture)
        self.assertTrue(any("diffuse_texture" in n for n in m.notes))

    def test_uv_transform_and_albedo_scale(self):
        m = map_omnipbr("x", {"diffuse_texture": T("/a.png", "auto"), "texture_scale": (4, 2),
                              "texture_rotate": 90.0, "albedo_brightness": 0.5, "albedo_add": 0.1})
        self.assertEqual(m.uv.scale, (4.0, 2.0))
        self.assertEqual(m.uv.rotate_deg, 90.0)
        self.assertEqual((m.base_color.tex_scale, m.base_color.tex_offset), (0.5, 0.1))
        self.assertEqual(m.base_color.texture.colorspace, "sRGB")

    def test_emission_opacity_coat(self):
        m = map_omnipbr("x", {"enable_emission": True, "emissive_color": (0, 1, 0), "emissive_intensity": 5.0,
                              "enable_opacity": True, "opacity_constant": 0.3, "opacity_threshold": 0.5,
                              "enable_clearcoat": True, "clearcoat_weight": 0.7})
        self.assertEqual(m.emission_color.constant, (0.0, 1.0, 0.0))
        self.assertEqual(m.emission_luminance, 5.0)
        self.assertEqual(m.geometry_opacity.constant, 0.3)
        self.assertEqual(m.opacity_threshold, 0.5)
        self.assertEqual(m.coat_weight, 0.7)


class OmniGlassTests(unittest.TestCase):
    def test_goggles_visor(self):  # m_trans__glass__scratchedGlossy_a01 (legacy OmniGlass)
        m = map_omniglass("visor", {"cutout_opacity": 0.0636, "glass_color": (0.8, 0.8, 0.8), "glass_ior": 1.5,
                                    "flip_tangent_v": False, "reflection_roughness_texture_influence": 1.0,
                                    "roughness_texture": T("/x/rou.png", "raw"),
                                    "roughness_texture_influence": 1.0})
        self.assertEqual(m.transmission_weight, 1.0)
        self.assertEqual(m.specular_ior, 1.5)
        self.assertEqual(m.base_color.constant, (0.8, 0.8, 0.8))
        self.assertAlmostEqual(m.geometry_opacity.constant, 0.0636)  # legacy cutout always applied
        self.assertEqual((m.specular_roughness.texture.channel, m.specular_roughness.influence), ("r", 1.0))

    def test_current_omniglass_opacity_needs_enable(self):
        self.assertIsNone(map_omniglass("g", {"opacity_constant": 0.5}).geometry_opacity)
        self.assertEqual(map_omniglass("g", {"opacity_constant": 0.5, "enable_opacity": True}).geometry_opacity.constant, 0.5)

    def test_dispatch(self):
        self.assertEqual(map_material("OmniPBR", "a", {}).source_model, "OmniPBR")
        self.assertEqual(map_material("OmniGlass", "a", {}).source_model, "OmniGlass")
        self.assertIsNone(map_material("OmniSurface", "a", {}))


if __name__ == "__main__":
    unittest.main()
