# SPDX-License-Identifier: GPL-3.0-or-later
"""addon/cache_link.py: Blender's library cache moved into the data folder via a link.

Runs on Linux (symlink) and with Windows Python (directory junction)."""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "cache_link", Path(__file__).resolve().parents[1] / "addon" / "cache_link.py")
cl = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cl)


class CacheLinkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.link = self.root / "remote-assets" / "ef7615a0e6baf832"
        self.target = self.root / "data" / "blender-cache"

    def tearDown(self):
        # never let rmtree follow the link into the target
        if cl.is_link(self.link):
            cl._remove_link(self.link)
        self.tmp.cleanup()

    def test_folder_name_matches_blender(self):
        # seen on Tonysa87's machine for http://127.0.0.1:8765/
        self.assertEqual(cl.library_cache_dir("http://127.0.0.1:8765/", Path("R")).name, "ef7615a0e6baf832")

    def test_fresh_link(self):
        state, _ = cl.ensure(self.link, self.target)
        self.assertEqual(state, "linked")
        self.assertTrue(cl.is_link(self.link))
        (self.link / "a.blend").write_bytes(b"x")
        self.assertTrue((self.target / "a.blend").exists())
        self.assertEqual(cl.ensure(self.link, self.target)[0], "ok")

    def test_existing_cache_is_moved(self):
        (self.link / "blend" / "food").mkdir(parents=True)
        (self.link / "blend" / "food" / "banana.blend").write_bytes(b"b")
        (self.link / "_v1").mkdir()
        (self.link / "_v1" / "asset-index.json").write_text("{}")
        state, msg = cl.ensure(self.link, self.target)
        self.assertEqual(state, "moved", msg)
        self.assertTrue(cl.is_link(self.link))
        self.assertEqual((self.target / "blend" / "food" / "banana.blend").read_bytes(), b"b")
        self.assertTrue((self.link / "_v1" / "asset-index.json").exists())
        self.assertFalse(self.link.with_name(self.link.name + cl.MIGRATING).exists())

    def test_data_folder_changed(self):
        cl.ensure(self.link, self.target)
        (self.target / "old.blend").write_bytes(b"o")
        new = self.root / "data2" / "blender-cache"
        state, msg = cl.ensure(self.link, new)
        self.assertEqual(state, "linked")
        self.assertIn("previous", msg)
        self.assertTrue((self.target / "old.blend").exists())  # old cache untouched
        (self.link / "n.blend").write_bytes(b"n")
        self.assertTrue((new / "n.blend").exists())

    def test_interrupted_migration_is_finished(self):
        pending = self.link.with_name(self.link.name + cl.MIGRATING)
        (pending / "blend").mkdir(parents=True)
        (pending / "blend" / "x.blend").write_bytes(b"x")
        cl.ensure(self.link, self.target)
        self.assertTrue((self.target / "blend" / "x.blend").exists())
        self.assertFalse(pending.exists())

    def test_old_cache_wins_over_partial_copy(self):
        (self.link / "blend").mkdir(parents=True)
        (self.link / "blend" / "big.blend").write_bytes(b"A" * 1000)
        (self.target / "blend").mkdir(parents=True)
        (self.target / "blend" / "big.blend").write_bytes(b"A" * 10)   # leftover of an interrupted copy
        (self.target / "blend" / "x.blend.simready-part").write_bytes(b"p")
        self.assertEqual(cl.ensure(self.link, self.target)[0], "moved")
        self.assertEqual(len((self.target / "blend" / "big.blend").read_bytes()), 1000)

    def test_link_failure_restores_the_cache(self):
        (self.link / "blend").mkdir(parents=True)
        (self.link / "blend" / "keep.blend").write_bytes(b"k")
        orig = cl._make_link

        def boom(link, target):
            raise OSError("unsupported drive")
        cl._make_link = boom
        try:
            state, _ = cl.ensure(self.link, self.target)
        finally:
            cl._make_link = orig
        self.assertEqual(state, "error")
        self.assertFalse(cl.is_link(self.link))
        self.assertTrue((self.link / "blend" / "keep.blend").exists())
        self.assertFalse(self.link.with_name(self.link.name + cl.MIGRATING).exists())

    def test_remove_link_keeps_target(self):
        cl.ensure(self.link, self.target)
        (self.target / "keep.blend").write_bytes(b"k")
        cl._remove_link(self.link)
        self.assertFalse(self.link.exists())
        self.assertTrue((self.target / "keep.blend").exists())


if __name__ == "__main__":
    unittest.main()
