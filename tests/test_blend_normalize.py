# SPDX-License-Identifier: GPL-3.0-or-later
"""Reproducible .blend files (simready/convert/blend_normalize.py).

Needs Blender (SIMREADY_BLENDER or ~/blender/blender-5.2.2-linux-x64/blender); skipped otherwise.
The seek-table test needs `zstandard` (Blender's Python has it).
"""

import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from simready.convert import blend_normalize as bn  # noqa: E402

BLENDER = Path(os.environ.get("SIMREADY_BLENDER", Path.home() / "blender/blender-5.2.2-linux-x64/blender"))
HELPER = ROOT / "tests/blender_repro_scene.py"

try:
    import zstandard  # noqa: F401
    HAS_ZSTD = True
except ImportError:
    HAS_ZSTD = False


def run_blender(*args: str) -> str:
    p = subprocess.run([str(BLENDER), "-b", "--factory-startup", "--python", str(HELPER), "--", *args],
                       capture_output=True, text=True, timeout=300)
    if p.returncode != 0:
        raise AssertionError(p.stdout[-2000:] + p.stderr[-2000:])
    return p.stdout


@unittest.skipUnless(HAS_ZSTD, "zstandard not available (run with Blender's Python)")
class SeekableFormat(unittest.TestCase):
    def test_round_trip_and_seek_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst, back = Path(tmp, "a.raw"), Path(tmp, "a.blend"), Path(tmp, "b.raw")
            data = os.urandom(300_000) + bytes(3 * bn.FRAME)  # several frames, last one partial
            src.write_bytes(data)
            bn.compress(src, dst)
            first = dst.read_bytes()
            bn.compress(src, dst)
            self.assertEqual(first, dst.read_bytes(), "compression must be deterministic")
            bn.decompress(dst, back)
            self.assertEqual(back.read_bytes(), data)
            # the seek table Blender looks for (filereader_zstd.cc)
            magic, = struct.unpack("<I", first[-4:])
            self.assertEqual(magic, 0x8F92EAB1)
            self.assertEqual(first[-5], 0)
            frames, = struct.unpack("<I", first[-9:-5])
            self.assertEqual(frames, 4)
            length = frames * 8 + 9
            start = len(first) - 8 - length
            self.assertEqual(struct.unpack_from("<II", first, start), (0x184D2A5E, length))
            sizes = [struct.unpack_from("<II", first, start + 8 + 8 * i) for i in range(frames)]
            self.assertEqual(sum(c for c, _ in sizes), start)
            self.assertEqual(sum(u for _, u in sizes), len(data))


class Unsupported(unittest.TestCase):
    def test_old_header_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp, "old.blend")
            p.write_bytes(b"BLENDER-v405" + bytes(64))
            with self.assertRaises(bn.NotSupported):
                bn.normalize(p)


@unittest.skipUnless(BLENDER.is_file(), f"Blender not found at {BLENDER}")
class Reproducible(unittest.TestCase):
    def test_same_scene_same_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b, res = Path(tmp, "a.blend"), Path(tmp, "b.blend"), Path(tmp, "check.json")
            out = run_blender("build", str(a))
            info = json.loads(out.split("REPRO_INFO ", 1)[1].splitlines()[0])
            self.assertEqual(info["warnings"], [])
            self.assertGreater(info["normalize"]["node_identifier"], 0)
            time.sleep(1.1)  # Mesh.face_sets_color_seed depends on the current second
            run_blender("build", str(b))
            self.assertEqual(hashlib.sha256(a.read_bytes()).hexdigest(),
                             hashlib.sha256(b.read_bytes()).hexdigest())
            run_blender("check", str(a), str(res))
            seen = json.loads(res.read_text())
            self.assertEqual(seen["objects"], ["part_0", "part_1"])
            self.assertEqual(seen["images"], [["albedo", True, [64, 64]]])
            for m in ("mat_0", "mat_1"):
                self.assertIn("ShaderNodeTexImage.Color>ShaderNodeBsdfPrincipled.Base Color",
                              seen["materials"][m]["links"])
                self.assertEqual(len(seen["materials"][m]["links"]), 3)


if __name__ == "__main__":
    unittest.main()
