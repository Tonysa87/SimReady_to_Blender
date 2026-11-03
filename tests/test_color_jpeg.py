# SPDX-License-Identifier: GPL-3.0-or-later
"""Opaque color textures as JPEG (convert_asset._color_jpeg). Needs Blender; skipped otherwise."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BLENDER = Path(os.environ.get("SIMREADY_BLENDER", Path.home() / "blender/blender-5.2.2-linux-x64/blender"))


@unittest.skipUnless(BLENDER.is_file(), f"Blender not found at {BLENDER}")
class ColorJpeg(unittest.TestCase):
    def test_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            res_path = Path(tmp, "res.json")
            p = subprocess.run([str(BLENDER), "-b", "--factory-startup", "--python",
                                str(ROOT / "tests/blender_color_jpeg.py"), "--", tmp, str(res_path)],
                               capture_output=True, text=True, timeout=300)
            self.assertIn("COLOR_JPEG_OK", p.stdout, p.stdout[-2000:] + p.stderr[-2000:])
            r = json.loads(res_path.read_text())
        self.assertTrue(r["noise_alb.png"]["jpeg"])
        self.assertEqual(r["noise_alb.png"]["first"], "ffd8ff")          # a real JPEG
        self.assertEqual(r["noise_alb.png"]["channels"], 3)
        self.assertLess(r["noise_alb.png"]["bytes"], 0.8 * r["noise_alb.png"]["png"])
        self.assertTrue(r["noise_alb.png"]["same_again"])                # deterministic
        self.assertEqual(r["noise_small_alb.png"]["size"], [128, 128])   # reduced like --max-res
        self.assertFalse(r["flat_alb.png"]["jpeg"])                      # PNG is smaller
        self.assertFalse(r["alpha_alb.png"]["jpeg"])                     # real transparency
        self.assertTrue(r["opaque4_alb.png"]["jpeg"])                    # alpha all 255: fine


if __name__ == "__main__":
    unittest.main()
