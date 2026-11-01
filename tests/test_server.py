# SPDX-License-Identifier: GPL-3.0-or-later
"""Server pieces that need neither Blender nor the network."""

import gzip
import hashlib
import http.client
import json
import threading
import time
import unittest
import zlib
from concurrent.futures import Future
from pathlib import Path
from tempfile import TemporaryDirectory

from simready.server import app as app_mod
from simready.server.catalogs import Catalogs, catalog_uuid
from simready.server.hf import RateLimiter
from simready.server.index import AssetRecord, Index, safe_relpath, valid_asset_id, wanted_for_conversion
from simready.server.jobs import placeholder_hash
from simready.server.listing import build

TABLE = {"root": "SimReady", "split": ["food"], "split_min": 2, "labels": {"ppe": "PPE"},
         "groups": {"Food": ["food"], "Safety": ["ppe"]}}


def rec(aid, **kw):
    r = AssetRecord(id=aid, oid="o" + aid, root="simready_usd/sm.usd",
                    files=[["simready_usd/sm.usd", 10, "a" * 64]], thumb=[".thumbs/x.png", 5, "b" * 64],
                    content_digest="c" * 64, id_type="OBJECT", name=aid + "_mesh_00")
    for k, v in kw.items():
        setattr(r, k, v)
    return r


class CatalogTest(unittest.TestCase):
    def test_assign(self):
        c = Catalogs(TABLE)
        m = c.assign(["food_produce_apple_a01", "food_produce_pear_a01", "food_banana_a01",
                      "ppe_goggles_a01", "robot_arm_a01"])
        self.assertEqual(m["food_produce_apple_a01"], "SimReady/Food/Food/Produce")
        self.assertEqual(m["food_banana_a01"], "SimReady/Food/Food")  # second token used once only
        self.assertEqual(m["ppe_goggles_a01"], "SimReady/Safety/PPE")
        self.assertEqual(m["robot_arm_a01"], "SimReady/Other/Robot")

    def test_listing_entries_have_parents_and_stable_uuids(self):
        e = Catalogs.listing_entries(["SimReady/Food/Food/Produce"])
        self.assertEqual([x["path"] for x in e],
                         ["SimReady", "SimReady/Food", "SimReady/Food/Food", "SimReady/Food/Food/Produce"])
        self.assertEqual(e[-1]["uuids"], [catalog_uuid("SimReady/Food/Food/Produce")])
        self.assertEqual(catalog_uuid("SimReady/Food"), catalog_uuid("SimReady/Food"))

    def test_shipped_table_loads(self):
        c = Catalogs.load()
        self.assertIn("Food & Beverage", c.assign(["food_produce_apple_a01"])["food_produce_apple_a01"])


class IndexHelpersTest(unittest.TestCase):
    def test_safe_relpath(self):
        for ok in ("simready_usd/a.usd", "simready_usd/Textures/t.png"):
            self.assertTrue(safe_relpath(ok), ok)
        for bad in ("", "/etc/passwd", "../x", "simready_usd/../../x", "a\\b", "C:/x", "a/./b", "a\x00b"):
            self.assertFalse(safe_relpath(bad), bad)

    def test_wanted_for_conversion(self):
        root = "simready_usd/sm_a.usd"
        yes = [root, ".metadata/LICENSE.txt", "simready_usd/payloads/base.usda", "simready_usd/Textures/t.png",
               "simready_usd/textures/t.jpg", "simready_usd/runnables/physics/physx.usda"]
        no = ["simready_usd/source_assets/x.max", "simready_usd/.thumbs/256x256/sm_a.usd.png",
              "simready_usd/Textures/.thumbs/t.png", "simready_usd/.simready/validation.json",
              ".metadata/com.nvidia.simready.packaging.bom.json", "README.md"]
        for p in yes:
            self.assertTrue(wanted_for_conversion(p, root), p)
        for p in no:
            self.assertFalse(wanted_for_conversion(p, root), p)

    def test_asset_ids(self):
        self.assertTrue(valid_asset_id("food_banana_a01"))
        for bad in ("..", "a/b", ".hidden", "", "a..b"):
            self.assertFalse(valid_asset_id(bad), bad)

    def test_roundtrip(self):
        with TemporaryDirectory() as d:
            idx = Index(revision="r1", assets={"food_banana_a01": rec("food_banana_a01")}, complete=True)
            idx.save(Path(d) / "i.json")
            back = Index.load(Path(d) / "i.json")
            self.assertEqual(back.assets["food_banana_a01"], idx.assets["food_banana_a01"])
            self.assertTrue(back.complete)


class ListingTest(unittest.TestCase):
    def test_placeholder_then_real(self):
        a, b = rec("food_produce_apple_a01"), rec("ppe_goggles_a01", id_type="COLLECTION", name="ppe_goggles_a01")
        idx = Index(revision="r", assets={a.id: a, b.id: b})
        paths = Catalogs(TABLE).assign(list(idx.assets))
        real = {"hash": "d" * 64, "size": 1234, "id_type": "COLLECTION", "name": "food_produce_apple_a01"}
        out = build(idx, paths, lambda r: real if r.id == a.id else None, 2048)
        page = json.loads(out["/_v1/assets-00000.json"])
        files = {f["path"]: f for f in page["files"]}
        self.assertEqual(files["blend/food/food_produce_apple_a01.blend"]["hash"], "SHA256:" + "d" * 64)
        self.assertEqual(files["blend/ppe/ppe_goggles_a01.blend"]["hash"], "SHA256:" + placeholder_hash(b, 2048))
        names = {x["name"]: x["id_type"] for x in page["assets"]}
        self.assertEqual(names, {"food_produce_apple_a01": "COLLECTION", "ppe_goggles_a01": "COLLECTION"})
        meta = json.loads(out["/_asset-library-meta.json"])
        self.assertEqual(meta["api_versions"]["v1"]["hash"],
                         "SHA256:" + hashlib.sha256(out["/_v1/asset-index.json"]).hexdigest())

    def test_placeholder_changes_with_inputs(self):
        a = rec("x_a01")
        h = placeholder_hash(a, 2048)
        self.assertNotEqual(h, placeholder_hash(a, 1024))
        self.assertNotEqual(h, placeholder_hash(rec("x_a01", content_digest="e" * 64), 2048))


class RateLimiterTest(unittest.TestCase):
    def test_blocks_when_low(self):
        rl = RateLimiter(reserve=5)
        rl.update('"resolvers";r=100;t=40')
        self.assertNotIn("resolvers", rl.until)
        rl.update('"resolvers";r=3;t=40')
        self.assertGreater(rl.until["resolvers"], time.time() + 39)


class _FakeConverter:
    def __init__(self, fut):
        self.fut = fut

    def ensure(self, rev, rec):
        return self.fut

    def current(self, rec):
        return None


class _FakeLib:
    def __init__(self, fut):
        r = rec("food_banana_a01")
        self.index = Index(revision="r", assets={r.id: r})
        self._by_blend = {"/blend/food/food_banana_a01.blend": r}
        self.converter = _FakeConverter(fut)

        class S:
            timeout = 30
        self.s = S()


class KeepAliveTest(unittest.TestCase):
    """The gzip stream with empty deflate blocks decodes to the exact file."""

    def setUp(self):
        self.saved = (app_mod.PLAIN_WAIT, app_mod.KEEPALIVE_EVERY)
        app_mod.PLAIN_WAIT, app_mod.KEEPALIVE_EVERY = 0.1, 0.1
        self.tmp = TemporaryDirectory()

    def tearDown(self):
        app_mod.PLAIN_WAIT, app_mod.KEEPALIVE_EVERY = self.saved
        self.tmp.cleanup()

    def _serve(self, fut):
        app_mod.Handler.lib = _FakeLib(fut)
        httpd = app_mod.Server(("127.0.0.1", 0), app_mod.Handler)
        app_mod.Handler.port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.shutdown)
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
        conn.request("GET", "/blend/food/food_banana_a01.blend",
                     headers={"Accept-Encoding": "gzip", "X-Blender": "test"})
        return conn.getresponse()

    def test_slow_conversion(self):
        data = bytes(range(256)) * 5000
        blend = Path(self.tmp.name) / "a.blend"
        blend.write_bytes(data)
        fut = Future()
        threading.Timer(0.6, lambda: fut.set_result(blend)).start()
        resp = self._serve(fut)
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.getheader("Content-Encoding"), "gzip")
        raw = resp.read()
        self.assertGreater(raw.count(app_mod.EMPTY_STORED_BLOCK), 2)
        self.assertEqual(gzip.decompress(raw), data)

    def test_failed_conversion_breaks_the_stream(self):
        fut = Future()
        threading.Timer(0.4, lambda: fut.set_exception(RuntimeError("boom"))).start()
        resp = self._serve(fut)
        self.assertEqual(resp.status, 200)
        with self.assertRaises(http.client.IncompleteRead):
            raw = resp.read()
            zlib.decompressobj(wbits=31).decompress(raw)  # not reached: chunked body never terminated


if __name__ == "__main__":
    unittest.main()
