# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase-3 test server: long conversions with HTTP keep-alive tricks + listing/.blend mismatch.

Derived from tools/phase1/proto_server.py (stdlib only).

    python3 tools/phase3/longconv_server.py --state-dir DIR --port 8790 \
        [--keepalive gzip|continue|none] [--interval 20] [--pad 0] \
        [--delay ASSET=SEC ...] [--fail ASSET ...] [--fail-style abort|finish] \
        [--keep-prediction]

Keep-alive modes for a .blend that is not converted yet:
  none      convert first, then send 200 + Content-Length (phase-1 behaviour)
  gzip      send 200 + Content-Encoding: gzip + chunked at once; gzip header,
            then every --interval s an empty deflate stored block (00 00 00 ff ff,
            repeated to reach --pad bytes); when ready: compressed file + trailer.
            Failure: --fail-style abort  -> close the TCP connection mid-body
                     --fail-style finish -> send the last chunk without gzip trailer
  continue  every --interval s an interim "HTTP/1.1 100 Continue" response, then
            the normal final response (200 + Content-Length, or 502 on failure).

Runtime config:  POST /_admin/config  {"keepalive": "...", "delays": {...}, "fail": [...], ...}
                 POST /_admin/reconvert/<asset>
                 GET  /_status
"""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
BLENDER_VERSION = "5.2"
CATALOG_NS = uuid.UUID("6f0b6f0e-5a1e-4c55-9d6b-5e1d2e9a0001")
EMPTY_STORED_BLOCK = b"\x00\x00\x00\xff\xff"  # deflate: non-final, stored, LEN=0, NLEN=0xffff

# pred = what the listing declares before conversion; real = what the converter produces.
ASSETS = [
    {"id": "fake_cube_01", "kind": "cube", "catalog": "Fake/Cubes", "color": (200, 60, 40),
     "pred": ("COLLECTION", "fake_cube_01"), "real": ("COLLECTION", "fake_cube_01", "fake_cube_01_mesh")},
    {"id": "fake_monkey_01", "kind": "monkey", "catalog": "Fake/Monkeys", "color": (40, 120, 200),
     "pred": ("COLLECTION", "fake_monkey_01"), "real": ("COLLECTION", "fake_monkey_01", "fake_monkey_01_mesh")},
    # listing says OBJECT <mesh>, file has COLLECTION <folder> with that mesh inside
    {"id": "mis_type_a01", "kind": "cube", "catalog": "Mismatch/Type", "color": (60, 180, 60),
     "pred": ("OBJECT", "mis_type_a01_mesh_00"), "real": ("COLLECTION", "mis_type_a01", "mis_type_a01_mesh_00")},
    # same type, different name
    {"id": "mis_name_a01", "kind": "monkey", "catalog": "Mismatch/Name", "color": (180, 180, 40),
     "pred": ("COLLECTION", "mis_name_WRONG"), "real": ("COLLECTION", "mis_name_a01", "mis_name_a01_mesh_00")},
    # listing says COLLECTION, file has a single OBJECT
    {"id": "mis_rev_a01", "kind": "cube", "catalog": "Mismatch/Reverse", "color": (160, 60, 160),
     "pred": ("COLLECTION", "mis_rev_a01"), "real": ("OBJECT", "mis_rev_a01_mesh_00", "")},
]
BY_ID = {a["id"]: a for a in ASSETS}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dumps(obj) -> bytes:
    return json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False).encode()


def make_png(w: int, h: int, rgb) -> bytes:
    raw = b"".join(b"\x00" + b"".join(bytes(min(255, c + 80) for c in rgb) if abs(x - y) < 12 else bytes(rgb)
                                       for x in range(w)) for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


LOG_FILE: Path | None = None


def log(msg: str) -> None:
    line = time.strftime("%H:%M:%S ") + msg
    print(line, flush=True)
    if LOG_FILE:
        with LOG_FILE.open("a") as f:
            f.write(line + "\n")


class Library:
    def __init__(self, state_dir: Path, blender: str, cfg: dict) -> None:
        self.state_dir, self.blender, self.cfg = state_dir, blender, cfg
        self.thumb_dir = state_dir / "thumbs"
        self.lock = threading.RLock()
        self.asset_locks = {a["id"]: threading.Lock() for a in ASSETS}
        self.generation = 0
        self.events: list[dict] = []
        self.running: set[str] = set()
        self.state_path = state_dir / "state.json"
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        for a in ASSETS:
            self.state.setdefault(a["id"], {"variant": 0})
        self._save_state()
        self.thumb_dir.mkdir(parents=True, exist_ok=True)
        for a in ASSETS:
            p = self.thumb_dir / f"{a['id']}.png"
            if not p.exists():
                p.write_bytes(make_png(128, 128, a["color"]))

    def _save_state(self) -> None:
        self.state_path.write_text(json.dumps(self.state, indent=1))

    @staticmethod
    def relpath(a: dict) -> str:
        return f"blend/{a['catalog'].split('/')[0].lower()}/{a['id']}.blend"

    def asset_by_relpath(self, rel: str):
        return next((a for a in ASSETS if self.relpath(a) == rel), None)

    def blend_path(self, a: dict) -> Path:
        return self.state_dir / self.relpath(a)

    def build_listing(self) -> dict[str, bytes]:
        with self.lock:
            assets, files = [], []
            for a in ASSETS:
                st = self.state[a["id"]]
                corrected = st.get("listed_hash") and not self.cfg["keep_prediction"]
                id_type, name = (a["real"][0], a["real"][1]) if corrected else a["pred"]
                thumb = self.thumb_dir / f"{a['id']}.png"
                assets.append({
                    "name": name, "id_type": id_type, "files": [self.relpath(a)],
                    "thumbnail": {"url": f"thumbs/{a['id']}.png", "hash": "SHA256:" + sha256_file(thumb)},
                    "bl_versions": {"min": BLENDER_VERSION},
                    "meta": {"catalog_id": str(uuid.uuid5(CATALOG_NS, a["catalog"])), "tags": ["fake", a["kind"]],
                             "author": "NVIDIA", "license": "CC-BY-4.0",
                             "description": f"phase-3 fake {a['id']}"},
                })
                if st.get("listed_hash"):
                    h, size = st["listed_hash"], st["listed_size"]
                else:
                    h, size = sha256_bytes(f"{a['id']}:{st['variant']}:pending".encode()), 50_000
                files.append({"path": self.relpath(a), "size_in_bytes": size, "hash": "SHA256:" + h,
                              "blender_version": BLENDER_VERSION})
            page = dumps({"asset_count": len(assets), "file_count": len(files), "assets": assets, "files": files})
            catalogs, seen = [], set()
            for a in ASSETS:
                parts = a["catalog"].split("/")
                for n in range(1, len(parts) + 1):
                    p = "/".join(parts[:n])
                    if p not in seen:
                        seen.add(p)
                        catalogs.append({"path": p, "simple_name": p.replace("/", "-"),
                                         "uuids": [str(uuid.uuid5(CATALOG_NS, p))]})
            index = dumps({"schema_version": "1.0.0", "asset_size_bytes": sum(f["size_in_bytes"] for f in files),
                           "asset_count": len(assets), "file_count": len(files),
                           "pages": [{"url": "_v1/assets-00000.json", "hash": "SHA256:" + sha256_bytes(page)}],
                           "catalogs": catalogs})
            meta = dumps({"name": "SimReady phase-3 test", "contact": {"name": "test"},
                          "api_versions": {"v1": {"url": "_v1/asset-index.json",
                                                  "hash": "SHA256:" + sha256_bytes(index)}}})
            return {"/_v1/assets-00000.json": page, "/_v1/asset-index.json": index,
                    "/_asset-library-meta.json": meta}

    def ensure_converted(self, a: dict) -> Path:
        aid, path = a["id"], self.blend_path(a)
        with self.asset_locks[aid]:
            if path.exists():
                return path
            with self.lock:
                self.running.add(aid)
            t0 = time.time()
            try:
                delay = float(self.cfg["delays"].get(aid, self.cfg["convert_delay"]))
                log(f"convert {aid}: start (simulated {delay:.0f}s)")
                time.sleep(delay)
                if aid in self.cfg["fail"]:
                    raise RuntimeError("simulated conversion failure")
                variant = self.state[aid]["variant"]
                id_type, name, mesh = a["real"]
                cmd = [self.blender, "-b", "--factory-startup", "--python", str(HERE / "fake_convert2.py"), "--",
                       str(path), id_type, name, mesh or name, a["kind"], str(variant),
                       str(uuid.uuid5(CATALOG_NS, a["catalog"])), str(self.thumb_dir / f"{aid}.png"),
                       ",".join(["fake", a["kind"]])]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                if proc.returncode != 0 or not path.exists():
                    raise RuntimeError(f"conversion failed rc={proc.returncode}: {proc.stdout[-300:]}")
                h, size = sha256_file(path), path.stat().st_size
                with self.lock:
                    st = self.state[aid]
                    st.update(real_hash=h, real_size=size, listed_hash=h, listed_size=size)
                    self.generation += 1
                    self._save_state()
                    self.events.append({"t": time.time(), "type": "converted", "asset": aid,
                                        "seconds": round(time.time() - t0, 1), "generation": self.generation})
                log(f"convert {aid}: done in {time.time() - t0:.1f}s -> {size} B sha256={h[:16]}")
                return path
            except Exception as ex:
                with self.lock:
                    self.events.append({"t": time.time(), "type": "error", "asset": aid, "message": str(ex)})
                log(f"convert {aid}: FAILED {ex}")
                raise
            finally:
                with self.lock:
                    self.running.discard(aid)

    def reconvert(self, aid: str) -> None:
        a = BY_ID[aid]
        with self.asset_locks[aid], self.lock:
            self.blend_path(a).unlink(missing_ok=True)
            st = self.state[aid]
            st["variant"] += 1
            for k in ("listed_hash", "listed_size", "real_hash", "real_size"):
                st.pop(k, None)
            self.generation += 1
            self._save_state()

    def status(self) -> dict:
        with self.lock:
            return {"generation": self.generation, "running": sorted(self.running), "cfg": self.cfg,
                    "events": self.events[-50:], "state": self.state}


class Handler(BaseHTTPRequestHandler):
    library: Library
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body: bytes, ctype: str, extra=None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_file(self, path: Path, ctype: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.end_headers()
        with path.open("rb") as f:
            while chunk := f.read(1 << 20):
                self.wfile.write(chunk)

    def _chunk(self, data: bytes) -> None:
        if data:
            self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))

    # ---- the interesting part ---------------------------------------------

    def _serve_blend(self, a: dict) -> int:
        lib, cfg = self.library, self.library.cfg
        conv_conn = self.headers.get("Connection")
        mode = cfg["keepalive"]
        gz_ok = "gzip" in (self.headers.get("Accept-Encoding") or "")
        already = lib.blend_path(a).exists() and a["id"] not in lib.running
        if already or mode == "none" or (mode == "gzip" and not gz_ok):
            try:
                p = lib.ensure_converted(a)
            except Exception as ex:
                self._send(502, f"conversion failed: {ex}".encode(), "text/plain")
                return 502
            self._send_file(p, "application/x-blender")
            return 200

        result: dict = {}
        done = threading.Event()

        def work():
            try:
                result["path"] = lib.ensure_converted(a)
            except Exception as ex:
                result["error"] = ex
            finally:
                done.set()

        threading.Thread(target=work, daemon=True).start()
        interval = float(cfg["interval"])
        n_ka = 0

        if mode == "continue":
            while not done.wait(interval):
                self.wfile.write(b"HTTP/1.1 100 Continue\r\n\r\n")
                n_ka += 1
            log(f"  {a['id']}: sent {n_ka} x '100 Continue' (Connection={conv_conn})")
            if "error" in result:
                self._send(502, f"conversion failed: {result['error']}".encode(), "text/plain")
                return 502
            self._send_file(result["path"], "application/x-blender")
            return 200

        # gzip keep-alive
        self.send_response(200)
        self.send_header("Content-Type", "application/x-blender")
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        comp = zlib.compressobj(int(cfg["gzip_level"]), zlib.DEFLATED, 31)
        self._chunk(comp.flush(zlib.Z_SYNC_FLUSH))  # 10-byte gzip header + empty stored block
        pad = max(1, int(cfg["pad"]) // len(EMPTY_STORED_BLOCK))
        while not done.wait(interval):
            self._chunk(EMPTY_STORED_BLOCK * pad)
            n_ka += 1
        log(f"  {a['id']}: sent {n_ka} gzip keep-alives of {pad * 5} B")
        if "error" in result:
            if cfg["fail_style"] == "finish":
                self.wfile.write(b"0\r\n\r\n")  # well-formed chunked end, but gzip stream without trailer
                log(f"  {a['id']}: failure -> terminating chunk WITHOUT gzip trailer")
            else:
                log(f"  {a['id']}: failure -> aborting TCP connection mid-body")
                try:
                    self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                except OSError:
                    pass
                self.connection.shutdown(socket.SHUT_RDWR)
            self.close_connection = True
            return 599
        sent = 0
        with result["path"].open("rb") as f:
            while data := f.read(1 << 20):
                out = comp.compress(data)
                self._chunk(out)
                sent += len(out)
        tail = comp.flush()
        self._chunk(tail)
        self.wfile.write(b"0\r\n\r\n")
        log(f"  {a['id']}: streamed {result['path'].stat().st_size} B as {sent + len(tail)} B gzip")
        return 200

    def do_GET(self):
        t0 = time.time()
        url = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(url.path)
        code = 200
        peer = self.client_address[1]
        log(f"GET {self.path} <- :{peer} start AE={self.headers.get('Accept-Encoding')} "
            f"Conn={self.headers.get('Connection')}")
        try:
            lib = self.library
            if path == "/_status":
                self._send(200, dumps(lib.status()), "application/json")
            elif path in ("/_asset-library-meta.json", "/_v1/asset-index.json", "/_v1/assets-00000.json"):
                body = lib.build_listing()[path]
                etag = '"' + sha256_bytes(body)[:32] + '"'
                if self.headers.get("If-None-Match") == etag:
                    code = 304
                    self.send_response(304)
                    self.send_header("ETag", etag)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                else:
                    self._send(200, body, "application/json", {"ETag": etag, "Cache-Control": "no-cache"})
            elif path.startswith("/thumbs/"):
                p = lib.thumb_dir / Path(path).name
                if p.exists():
                    self._send_file(p, "image/png")
                else:
                    code = 404
                    self._send(404, b"not found", "text/plain")
            elif path.startswith("/blend/"):
                a = lib.asset_by_relpath(path.lstrip("/"))
                if not a:
                    code = 404
                    self._send(404, b"unknown asset", "text/plain")
                else:
                    code = self._serve_blend(a)
            else:
                code = 404
                self._send(404, b"not found", "text/plain")
        except (BrokenPipeError, ConnectionResetError) as ex:
            code = f"client-gone({type(ex).__name__})"
            self.close_connection = True
        finally:
            log(f"GET {self.path} <- :{peer} -> {code} ({time.time() - t0:.1f}s) INM={self.headers.get('If-None-Match')}")

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        if path.startswith("/_admin/reconvert/"):
            self.library.reconvert(path.rsplit("/", 1)[1])
            self._send(200, b"ok", "text/plain")
        elif path == "/_admin/config":
            self.library.cfg.update(json.loads(body or b"{}"))
            self._send(200, dumps(self.library.cfg), "application/json")
        else:
            self._send(404, b"not found", "text/plain")
        log(f"POST {self.path} {body[:200]!r}")


def main() -> None:
    global LOG_FILE
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", type=Path, required=True)
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--blender", default=str(Path.home() / "blender/blender-5.2.2-linux-x64/blender"))
    ap.add_argument("--keepalive", choices=["none", "gzip", "continue"], default="gzip")
    ap.add_argument("--interval", type=float, default=20.0)
    ap.add_argument("--pad", type=int, default=0, help="bytes per gzip keep-alive (multiple of 5)")
    ap.add_argument("--gzip-level", type=int, default=1)
    ap.add_argument("--convert-delay", type=float, default=2.0)
    ap.add_argument("--delay", action="append", default=[], help="ASSET=SECONDS")
    ap.add_argument("--fail", action="append", default=[], help="ASSET whose conversion fails")
    ap.add_argument("--fail-style", choices=["abort", "finish"], default="abort")
    ap.add_argument("--keep-prediction", action="store_true", help="never correct id_type/name in the listing")
    args = ap.parse_args()
    args.state_dir.mkdir(parents=True, exist_ok=True)
    LOG_FILE = args.state_dir / "requests.log"
    cfg = {"keepalive": args.keepalive, "interval": args.interval, "pad": args.pad, "gzip_level": args.gzip_level,
           "convert_delay": args.convert_delay, "delays": dict(d.split("=") for d in args.delay),
           "fail": list(args.fail), "fail_style": args.fail_style, "keep_prediction": args.keep_prediction}
    Handler.library = Library(args.state_dir, args.blender, cfg)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    srv.daemon_threads = True
    log(f"serving on http://127.0.0.1:{args.port}/ cfg={cfg}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
