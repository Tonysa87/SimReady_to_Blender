# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for simready/server/structure.py (datablock type/name prediction).

The pxr tests are skipped when pxr is not importable (system Python); run them with
Blender's Python too:  <blender>/5.2/python/bin/python3.13 -m unittest discover -s tests
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simready.server import structure  # noqa: E402

P = "simready_usd/payloads"
ROOT = "simready_usd/sm_thing_a01_01.usda"

ROOT_USDA = """#usda 1.0
(
    defaultPrim = "RootNode"
)

def Xform "RootNode" (
    prepend references = @./payloads/base.usda@
    variants = {
        string PhysX = "Enabled"
    }
    append variantSets = ["PhysX"]
)
{
    variantSet "PhysX" = {
        "Disabled" {
        }
        "Enabled" {
            def Mesh "physx_collider"
            {
            }
        }
    }
}
"""


def base_usda(parts: list[tuple[str, str]], extra: str = "") -> str:
    """parts: (Xform name in base.usda, /Instances/<target>)."""
    body = "".join(f"""
        def Xform "{name}" (
            instanceable = true
            prepend references = @./instances.usda@</Instances/{target}>
        )
        {{
        }}
""" for name, target in parts)
    return f"""#usda 1.0
(
    defaultPrim = "RootNode"
    subLayers = [
        @./Physics/physics.usda@
    ]
)

def Xform "RootNode"
{{
    def Scope "Geometry"
    {{{body}{extra}
    }}
}}
"""


def instances_usda(entries: list[tuple[str, str, list[str]]]) -> str:
    """entries: (instance Xform name, /Geometries/<geom>, [mesh over names])."""
    out = ['#usda 1.0\n\ndef Scope "Instances"\n{']
    for name, geom, overs in entries:
        out.append(f'    def Xform "{name}" (\n        prepend references = @./geometries.usd@</Geometries/{geom}>\n    )\n    {{')
        for o in overs:
            out.append(f'        over "{o}" (\n            apiSchemas = ["MaterialBindingAPI"]\n        )\n        {{\n'
                       f'            rel material:binding = </Instances/{name}/VisualMaterials/m>\n'
                       f'            over "subset_a"\n            {{\n            }}\n        }}')
        out.append('        def Scope "VisualMaterials"\n        {\n            def Material "m"\n            {\n            }\n        }')
        out.append('    }')
    out.append('}\n')
    return "\n".join(out)


def files(base: str, instances: str | None = None, root: str | None = ROOT_USDA) -> dict:
    f = {f"{P}/base.usda": base.encode()}
    if instances is not None:
        f[f"{P}/instances.usda"] = instances.encode()
    if root is not None:
        f[ROOT] = root.encode()
    return f


class WantedFilesTest(unittest.TestCase):
    def test_small_layers_only(self):
        bom = [
            {"relative_path": ROOT, "size": 20_000},
            {"relative_path": f"{P}/base.usda", "size": 11_000},
            {"relative_path": f"{P}/instances.usda", "size": 3_000_000},
            {"relative_path": f"{P}/geometries.usd", "size": 1_000_000},
            {"relative_path": "simready_usd/Textures/t_alb.png", "size": 10},
        ]
        self.assertEqual(structure.wanted_files("thing_a01", bom, ROOT), [f"{P}/base.usda"])
        self.assertEqual(structure.wanted_files("thing_a01", bom, ROOT, max_bytes=5_000_000, include_root=True),
                         [ROOT, f"{P}/base.usda", f"{P}/instances.usda"])

    def test_unsafe_and_missing(self):
        bom = [{"relative_path": "../evil/base.usda", "size": 1}, {"relative_path": ROOT, "size": 1}]
        self.assertEqual(structure.wanted_files("x", bom, "../evil/x.usda", include_root=True), [])
        self.assertEqual(structure.wanted_files("x", bom, ROOT, include_root=True), [ROOT])
        self.assertEqual(structure.wanted_files("x", bom, ROOT), [])


class TextFallbackTest(unittest.TestCase):
    """Regex method: works without pxr (low confidence)."""

    def test_single(self):
        f = files(base_usda([("a_mesh_00", "a_mesh_00")]), instances_usda([("a_mesh_00", "a_mesh_00", ["a_mesh_00"])]))
        r = structure._predict_text("thing_a01", f, f"{P}/base.usda")
        self.assertEqual((r["id_type"], r["name"], r["mesh_count"], r["confidence"]), ("OBJECT", "thing_a01", 1, "low"))

    def test_inner_name(self):
        f = files(base_usda([("obj", "part_mesh_00_1")]), instances_usda([("part_mesh_00_1", "part_mesh_00", ["part_mesh_00"])]))
        r = structure._predict_text("thing_a01", f, f"{P}/base.usda")
        self.assertEqual(r["meshes"], ["part_mesh_00"])

    def test_multi_and_no_base(self):
        f = files(base_usda([("a", "a"), ("b", "b")]))
        r = structure._predict_text("thing_a01", f, f"{P}/base.usda")
        self.assertEqual((r["id_type"], r["name"], r["mesh_count"]), ("COLLECTION", "thing_a01", 2))
        r = structure._predict_text("thing_a01", {}, f"{P}/base.usda")
        self.assertEqual((r["id_type"], r["name"], r["confidence"]), ("COLLECTION", "thing_a01", "low"))


@unittest.skipUnless(structure.HAVE_PXR, "pxr not available (run with Blender's Python)")
class PxrPredictTest(unittest.TestCase):
    def predict(self, f):
        return structure.predict("thing_a01", f)

    def test_single_mesh_object(self):
        f = files(base_usda([("thing_mesh_00", "thing_mesh_00")]),
                  instances_usda([("thing_mesh_00", "thing_mesh_00", ["thing_mesh_00"])]))
        r = self.predict(f)
        self.assertEqual((r["id_type"], r["name"], r["mesh_count"], r["confidence"]),
                         ("OBJECT", "thing_a01", 1, "high"), r)
        self.assertEqual(r["meshes"], ["thing_mesh_00"])
        self.assertTrue(r["method"].startswith("pxr"))

    def test_inner_over_name_and_shared_mesh(self):
        f = files(base_usda([("body_obj", "body_mesh_00"), ("door1_obj", "door_mesh_00"), ("door2_obj", "door_mesh_00_1")]),
                  instances_usda([("body_mesh_00", "body_mesh_00", ["body_mesh_00"]),
                                  ("door_mesh_00", "door_mesh_00", ["door_mesh_00"]),
                                  ("door_mesh_00_1", "door_mesh_00", ["door_mesh_00"])]))
        r = self.predict(f)
        self.assertEqual((r["id_type"], r["name"], r["mesh_count"]), ("COLLECTION", "thing_a01", 3), r)
        self.assertEqual(sorted(r["meshes"]), ["body_mesh_00", "door_mesh_00", "door_mesh_00"])

    def test_single_instance_with_inner_name(self):
        f = files(base_usda([("obj", "part_mesh_00_1")]),
                  instances_usda([("part_mesh_00_1", "part_mesh_00", ["part_mesh_00"])]))
        self.assertEqual(self.predict(f)["meshes"], ["part_mesh_00"])

    def test_invisible_guide_and_curves_pruned(self):
        extra = """
        def Xform "grasp_identifier"
        {
            uniform token purpose = "guide"
            def Mesh "grasp_mesh"
            {
            }
        }
        def Mesh "hidden_mesh"
        {
            token visibility = "invisible"
        }
        def BasisCurves "curve"
        {
        }
        def Mesh "inline_mesh"
        {
        }
"""
        f = files(base_usda([("a_obj", "a_mesh_00")], extra), instances_usda([("a_mesh_00", "a_mesh_00", ["a_mesh_00"])]))
        r = self.predict(f)
        self.assertEqual(sorted(r["meshes"]), ["a_mesh_00", "inline_mesh"], r)

    def test_physics_variant_disabled(self):
        """The converter selects "Disabled": meshes of the Enabled variant do not count."""
        f = files(base_usda([("a_obj", "a_mesh_00")]), instances_usda([("a_mesh_00", "a_mesh_00", ["a_mesh_00"])]))
        r = self.predict(f)
        self.assertNotIn("physx_collider", r["meshes"])
        self.assertEqual(r["id_type"], "OBJECT")

    def test_without_root_or_instances(self):
        f = files(base_usda([("a_obj", "a_mesh_00"), ("b_obj", "b_mesh_00")]), None, root=None)
        r = self.predict(f)
        self.assertEqual((r["id_type"], r["name"], r["mesh_count"], r["confidence"]), ("COLLECTION", "thing_a01", 2, "low"), r)
        f = files(base_usda([("a_obj", "a_mesh_00")]), None, root=None)
        self.assertEqual(self.predict(f)["meshes"], ["a_mesh_00"])   # /Geometries/<x>/<x> convention

    def test_untrusted_asset_paths_ignored(self):
        extra = """
        def Xform "evil" (
            prepend references = @/etc/passwd@</X>
        )
        {
        }
        def Xform "evil2" (
            prepend payload = @../../../outside.usda@</X>
        )
        {
        }
"""
        f = files(base_usda([("a_obj", "a_mesh_00")], extra), instances_usda([("a_mesh_00", "a_mesh_00", ["a_mesh_00"])]))
        r = self.predict(f)
        self.assertEqual((r["id_type"], r["mesh_count"]), ("OBJECT", 1), r)

    def test_value_clips_never_open_other_files(self):
        import tempfile
        from pxr import Sdf
        with tempfile.TemporaryDirectory() as d:
            outside = Path(d) / "outside.usda"
            outside.write_text('#usda 1.0\ndef Xform "Clip"\n{\n    token visibility.timeSamples = { 0: "invisible" }\n}\n')
            extra = f"""
        def Xform "clipped" (
            clips = {{
                dictionary default = {{
                    asset[] assetPaths = [@{outside.as_posix()}@]
                    string primPath = "/Clip"
                    double2[] active = [(0, 0)]
                }}
            }}
        )
        {{
        }}
"""
            f = files(base_usda([("a_obj", "a_mesh_00")], extra), instances_usda([("a_mesh_00", "a_mesh_00", ["a_mesh_00"])]))
            r = self.predict(f)
            self.assertEqual((r["id_type"], r["mesh_count"]), ("OBJECT", 1), r)
            self.assertIsNone(Sdf.Layer.Find(str(outside)), "a value clip made pxr open a local file")

    def test_garbage_falls_back(self):
        r = self.predict({f"{P}/base.usda": b"#usda 1.0\n def Xform (((", ROOT: b"PXR-USDC garbage"})
        self.assertEqual((r["id_type"], r["name"], r["confidence"]), ("COLLECTION", "thing_a01", "low"))

    def test_no_files(self):
        r = self.predict({})
        self.assertEqual((r["id_type"], r["name"], r["confidence"]), ("COLLECTION", "thing_a01", "low"))


if __name__ == "__main__":
    unittest.main()
