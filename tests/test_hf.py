# SPDX-License-Identifier: GPL-3.0-or-later
"""Network failures against a fake Hugging Face (simready/server/hf.py) and job errors (jobs.py)."""

import errno
import hashlib
import socket
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simready.server import jobs  # noqa: E402
from simready.server.hf import HFClient, HFError, IntegrityError  # noqa: E402
from simready.server.index import AssetRecord  # noqa: E402

DATA = bytes(range(256)) * 4096  # 1 MiB
SHA = hashlib.sha256(DATA).hexdigest()


class FakeHF(BaseHTTPRequestHandler):
    """Serves DATA at any resolve URL; `script` is a list of behaviours, one per request."""
    protocol_version = "HTTP/1.1"
    script: list = []
    seen: list = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        FakeHF.seen.append(self.headers.get("Range"))
        what = FakeHF.script.pop(0) if FakeHF.script else "ok"
        if what in (503, 404, 429):
            body = b"no"
            self.send_response(what)
            if what == 429:
                self.send_header("Retry-After", "1")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        start = 0
        rng = self.headers.get("Range")
        if rng and what != "ignore_range":
            start = int(rng.split("=")[1].split("-")[0])
        body = DATA if what != "corrupt" else b"x" * len(DATA)
        if start:
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(DATA) - 1}/{len(DATA)}")
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(body) - start))
        self.end_headers()
        if what == "drop":  # half of the rest, then the connection dies
            self.wfile.write(body[start:start + (len(body) - start) // 2])
            self.wfile.flush()
            self.connection.shutdown(socket.SHUT_RDWR)
            self.close_connection = True
            return
        self.wfile.write(body[start:])


class FakeHubTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeHF)
        cls.httpd.daemon_threads = True
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.hub = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def setUp(self):
        FakeHF.script, FakeHF.seen = [], []
        self.tmp = tempfile.TemporaryDirectory()
        self.dst = Path(self.tmp.name, "f.bin")
        self.hf = HFClient(hub=self.hub, backoff=0.01, timeout=5)

    def tearDown(self):
        self.tmp.cleanup()

    def get(self, **kw):
        got = []
        n = self.hf.download("rev", "a/f.bin", self.dst, len(DATA), SHA, progress=got.append, **kw)
        return n, sum(got)

    def test_plain(self):
        self.assertEqual(self.get(), (len(DATA), len(DATA)))
        self.assertEqual(self.dst.read_bytes(), DATA)
        self.assertEqual(self.get(), (0, 0))  # already there
        self.assertEqual(list(Path(self.tmp.name).glob("*.part")), [])

    def test_resume_after_drop(self):
        FakeHF.script = ["drop", "drop", "ok"]
        n, progress = self.get()
        self.assertEqual(self.dst.read_bytes(), DATA)
        self.assertEqual(progress, len(DATA))
        self.assertEqual(FakeHF.seen, [None, f"bytes={len(DATA) // 2}-", f"bytes={len(DATA) * 3 // 4}-"])

    def test_server_ignores_range(self):
        FakeHF.script = ["drop", "ignore_range"]
        n, progress = self.get()
        self.assertEqual(self.dst.read_bytes(), DATA)
        self.assertEqual(progress, len(DATA))  # the first half was taken back

    def test_server_errors_then_ok(self):
        FakeHF.script = [503, 503, "ok"]
        self.get()
        self.assertEqual(self.dst.read_bytes(), DATA)

    def test_rate_limit_waits(self):
        FakeHF.script = [429, "ok"]
        t = time.time()
        self.get()
        self.assertGreaterEqual(time.time() - t, 0.9)
        self.assertEqual(self.dst.read_bytes(), DATA)

    def test_corrupt_gives_up(self):
        FakeHF.script = ["corrupt"] * 3
        with self.assertRaises(IntegrityError) as cm:
            self.get()
        self.assertEqual(cm.exception.kind, "integrity")
        self.assertFalse(self.dst.exists())
        self.assertEqual(list(Path(self.tmp.name).glob("*.part")), [])

    def test_not_found_is_not_retried(self):
        FakeHF.script = [404, "ok"]
        with self.assertRaises(HFError) as cm:
            self.get()
        self.assertEqual(cm.exception.kind, "not_found")
        self.assertEqual(len(FakeHF.seen), 1)

    def test_endless_drops_give_up(self):
        FakeHF.script = ["drop"] * 50
        with mock.patch.object(FakeHF, "do_GET", _always_empty):
            with self.assertRaises(HFError) as cm:
                self.get(max_stalls=2)
        self.assertEqual(cm.exception.kind, "offline")

    def test_cancel(self):
        with self.assertRaises(HFError) as cm:
            self.get(cancel=lambda: True)
        self.assertEqual(cm.exception.kind, "cancelled")


def _always_empty(self):
    """Headers announce the file, then the connection closes without a byte."""
    self.send_response(200)
    self.send_header("Content-Length", str(len(DATA)))
    self.end_headers()
    self.wfile.flush()
    self.connection.shutdown(socket.SHUT_RDWR)
    self.close_connection = True


class OfflineTest(unittest.TestCase):
    def test_nothing_listening(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()  # a free port: connection refused
        hf = HFClient(hub=f"http://127.0.0.1:{port}", backoff=0.01, timeout=2)
        t = time.time()
        with self.assertRaises(HFError) as cm:
            hf.get_bytes("rev", "x")
        self.assertEqual(cm.exception.kind, "offline")
        self.assertLess(time.time() - t, 5)
        self.assertEqual(jobs.describe(cm.exception)[0], "offline")


def _record(files) -> AssetRecord:
    return AssetRecord(id="box_test_a01", root="simready_usd/x.usd", files=files, oid="o", rev="r",
                       content_digest="d", id_type="OBJECT", name="box_test_a01", mesh_count=1,
                       confidence="high", method="test", thumb=None)


class JobErrorsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        s = jobs.Settings(blender=Path("blender"), state_dir=self.root)
        self.conv = jobs.Converter(s, HFClient(hub="http://127.0.0.1:9"), lambda a: "x", lambda ev: None)

    def tearDown(self):
        self.conv.pool.shutdown()
        self.conv.dl_pool.shutdown()
        self.tmp.cleanup()

    def test_disk_space(self):
        rec = _record([["simready_usd/x.usd", 10 ** 9, "0" * 64]])
        with mock.patch("shutil.disk_usage", return_value=mock.Mock(free=10 ** 9)):
            with self.assertRaises(jobs.JobError) as cm:
                self.conv._check_space(rec, download=True)
        self.assertEqual(cm.exception.kind, "disk_full")
        self.assertIn("GB free", str(cm.exception))
        with mock.patch("shutil.disk_usage", return_value=mock.Mock(free=10 ** 11)):
            self.conv._check_space(rec, download=True)

    def test_describe(self):
        self.assertEqual(jobs.describe(OSError(errno.ENOSPC, "full"))[0], "disk_full")
        self.assertEqual(jobs.describe(HFError("x", "rate_limit"))[0], "rate_limit")
        self.assertEqual(jobs.describe(ValueError("boom"))[0], "internal")

    def test_converter_errors(self):
        log = "Read blend\nTraceback (most recent call last):\n  File \"x.py\", line 3\nRuntimeError: Error: bad prim\nSIMREADY_CONVERT_FAILED\n"
        e = jobs._converter_error(1, log)
        self.assertEqual(e.kind, "converter")
        self.assertIn("bad prim", str(e))
        self.assertEqual(jobs._converter_error(-9, "").kind, "memory")
        self.assertEqual(jobs._converter_error(1, "OSError: [Errno 28] No space left on device").kind, "disk_full")

    def test_cleanup(self):
        raw = self.root / "raw/box_test_a01/simready_usd"
        raw.mkdir(parents=True)
        (raw / "x.usd.1234abcd.part").write_bytes(b"x")
        (raw / "x.usd").write_bytes(b"x")
        bdir = self.root / "blend/res2048/box"
        bdir.mkdir(parents=True)
        for name in ("box_test_a01.aaaa.blend", "box_test_a01.bbbb.blend", "box_test_a01.1234.tmp.blend",
                     "box_test_a01.1234.tmp.blend.tmp", "box_test_a01.1234.tmp.blend.raw", "box_test_a01.json"):
            (bdir / name).write_bytes(b"x")
        self.conv.state.data = {"box_test_a01@2048": {"file": "box_test_a01.aaaa.blend"}}
        self.conv._cleanup()
        self.assertEqual(sorted(p.name for p in bdir.iterdir()), ["box_test_a01.aaaa.blend", "box_test_a01.json"])
        self.assertEqual([p.name for p in raw.iterdir()], ["x.usd"])  # recent download kept for a retry


if __name__ == "__main__":
    unittest.main()
