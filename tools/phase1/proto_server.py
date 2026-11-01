# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase-1 prototype of the "virtual" remote asset library server.

Standard library only. Serves a Blender 5.2 remote asset listing (API v1) for a
couple of fake assets whose .blend files do not exist until Blender asks for
them. On the first request the .blend is produced by a headless Blender
(fake_convert.py), the listing is updated with the real hash/size, and the file
is returned in the same HTTP response.

Usage:
    python3 proto_server.py --state-dir DIR [--port 8765] [--blender PATH]
                            [--convert-delay SEC] [--no-hash-update]

Extra endpoints for testing:
    GET  /_status                     conversions done / running / failed, listing generation
    POST /_admin/reconvert/<asset>    drop the converted file, bump the variant
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
import zlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
HF_REVISION = "fake-revision-0001"
BLENDER_VERSION = "5.2"
PAGE_SIZE = 250
CATALOG_NS = uuid.UUID("6f0b6f0e-5a1e-4c55-9d6b-5e1d2e9a0001")

FAKE_ASSETS = [
    {"id": "fake_cube_01", "kind": "cube", "catalog": "Fake/Cubes", "color": (200, 60, 40)},
    {"id": "fake_monkey_01", "kind": "monkey", "catalog": "Fake/Monkeys", "color": (40, 120, 200)},
]


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


def make_png(w: int, h: int, rgb: tuple[int, int, int]) -> bytes:
    """Tiny PNG writer: solid colour with a lighter diagonal stripe."""
    rows = []
    for y in range(h):
        row = bytearray([0])
        for x in range(w):
            if abs(x - y) < 12:
                row += bytes(min(255, c + 80) for c in rgb)
            else:
                row += bytes(rgb)
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


class Library:
    """Asset state + listing generation."""

    def __init__(self, state_dir: Path, blender: str, convert_delay: float, hash_update: bool) -> None:
        self.state_dir = state_dir
        self.blend_dir = state_dir / "blend"
        self.thumb_dir = state_dir / "thumbs"
        self.blender = blender
        self.convert_delay = convert_delay
        self.hash_update = hash_update
        self.lock = threading.RLock()
        self.asset_locks: dict[str, threading.Lock] = {a["id"]: threading.Lock() for a in FAKE_ASSETS}
        self.generation = 0
        self.events: list[dict] = []
        self.running: set[str] = set()
        self.state_path = state_dir / "state.json"
        self.state: dict[str, dict] = {}
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text())
        for a in FAKE_ASSETS:
            self.state.setdefault(a["id"], {"variant": 0})
        self._save_state()
        self.thumb_dir.mkdir(parents=True, exist_ok=True)
        for a in FAKE_ASSETS:
            p = self.thumb_dir / f"{a['id']}.png"
            if not p.exists():
                p.write_bytes(make_png(256, 256, a["color"]))

    # ---- state -------------------------------------------------------------

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state, indent=1))

    @staticmethod
    def blend_relpath(asset: dict) -> str:
        cat = asset["catalog"].split("/")[0].lower()
        return f"blend/{cat}/{asset['id']}.blend"

    def asset_by_relpath(self, relpath: str) -> dict | None:
        for a in FAKE_ASSETS:
            if self.blend_relpath(a) == relpath:
                return a
        return None

    def file_entry(self, asset: dict) -> dict:
        st = self.state[asset["id"]]
        if st.get("listed_hash"):
            h, size = st["listed_hash"], st["listed_size"]
        else:
            # Deterministic placeholder until the real file exists.
            h = sha256_bytes(f"{asset['id']}:{HF_REVISION}:{st['variant']}:pending".encode())
            size = 50_000
        return {
            "path": self.blend_relpath(asset),
            "size_in_bytes": size,
            "hash": "SHA256:" + h,
            "blender_version": BLENDER_VERSION,
        }

    # ---- listing -----------------------------------------------------------

    def build_listing(self) -> dict[str, bytes]:
        """Return {url_path: body} for the meta, index and page files."""
        with self.lock:
            assets, files = [], []
            for a in FAKE_ASSETS:
                thumb = self.thumb_dir / f"{a['id']}.png"
                cat_id = str(uuid.uuid5(CATALOG_NS, a["catalog"]))
                assets.append({
                    "name": a["id"],
                    "id_type": "COLLECTION",
                    "files": [self.blend_relpath(a)],
                    "thumbnail": {"url": f"thumbs/{a['id']}.png", "hash": "SHA256:" + sha256_file(thumb)},
                    "bl_versions": {"min": BLENDER_VERSION},
                    "meta": {
                        "catalog_id": cat_id,
                        "tags": ["fake", a["kind"]],
                        "author": "NVIDIA",
                        "license": "CC-BY-4.0",
                        "copyright": "© NVIDIA Corporation",
                        "description": f"Fake asset {a['id']} for phase-1 testing",
                    },
                })
                files.append(self.file_entry(a))

            out: dict[str, bytes] = {}
            pages = []
            for i in range(0, len(assets), PAGE_SIZE):
                pa, pf = assets[i:i + PAGE_SIZE], files[i:i + PAGE_SIZE]
                body = dumps({"asset_count": len(pa), "file_count": len(pf), "assets": pa, "files": pf})
                url = f"_v1/assets-{i // PAGE_SIZE:05}.json"
                out["/" + url] = body
                pages.append({"url": url, "hash": "SHA256:" + sha256_bytes(body)})

            catalogs = []
            seen = set()
            for a in FAKE_ASSETS:
                parts = a["catalog"].split("/")
                for n in range(1, len(parts) + 1):
                    path = "/".join(parts[:n])
                    if path in seen:
                        continue
                    seen.add(path)
                    catalogs.append({"path": path, "simple_name": path.replace("/", "-"),
                                     "uuids": [str(uuid.uuid5(CATALOG_NS, path))]})

            index = dumps({
                "schema_version": "1.0.0",
                "asset_size_bytes": sum(f["size_in_bytes"] for f in files),
                "asset_count": len(assets),
                "file_count": len(files),
                "pages": pages,
                "catalogs": catalogs,
            })
            out["/_v1/asset-index.json"] = index
            out["/_asset-library-meta.json"] = dumps({
                "name": "NVIDIA SimReady (phase-1 prototype)",
                "contact": {"name": "Tonysa87", "url": "https://huggingface.co/datasets/nvidia/simready-assets"},
                "api_versions": {"v1": {"url": "_v1/asset-index.json", "hash": "SHA256:" + sha256_bytes(index)}},
            })
            return out

    # ---- conversion --------------------------------------------------------

    def ensure_converted(self, asset: dict) -> Path:
        aid = asset["id"]
        path = self.state_dir / self.blend_relpath(asset)
        with self.asset_locks[aid]:  # concurrent requests wait for the same conversion
            if path.exists():
                return path
            with self.lock:
                self.running.add(aid)
            t0 = time.time()
            try:
                variant = self.state[aid]["variant"]
                if self.convert_delay:
                    time.sleep(self.convert_delay)
                cmd = [self.blender, "-b", "--factory-startup", "--python", str(HERE / "fake_convert.py"),
                       "--", aid, asset["kind"], str(path), str(variant),
                       str(uuid.uuid5(CATALOG_NS, asset["catalog"])), asset["catalog"].replace("/", "-"),
                       ",".join(["fake", asset["kind"]]), str(self.thumb_dir / f"{aid}.png")]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                if proc.returncode != 0 or not path.exists():
                    raise RuntimeError(f"conversion failed (rc={proc.returncode}): {proc.stdout[-500:]} {proc.stderr[-500:]}")
                real_hash, real_size = sha256_file(path), path.stat().st_size
                with self.lock:
                    st = self.state[aid]
                    st["real_hash"], st["real_size"] = real_hash, real_size
                    if self.hash_update:
                        st["listed_hash"], st["listed_size"] = real_hash, real_size
                        self.generation += 1
                    self._save_state()
                    self.events.append({"t": time.time(), "type": "converted", "asset": aid,
                                        "seconds": round(time.time() - t0, 2), "generation": self.generation})
                log(f"converted {aid} in {time.time() - t0:.1f}s -> {real_size} B sha256={real_hash[:12]}")
                return path
            except Exception as ex:
                with self.lock:
                    self.events.append({"t": time.time(), "type": "error", "asset": aid, "message": str(ex)})
                raise
            finally:
                with self.lock:
                    self.running.discard(aid)

    def reconvert(self, aid: str) -> None:
        asset = next(a for a in FAKE_ASSETS if a["id"] == aid)
        with self.asset_locks[aid], self.lock:
            p = self.state_dir / self.blend_relpath(asset)
            p.unlink(missing_ok=True)
            st = self.state[aid]
            st["variant"] += 1
            for k in ("listed_hash", "listed_size", "real_hash", "real_size"):
                st.pop(k, None)
            self.generation += 1
            self._save_state()

    def status(self) -> dict:
        with self.lock:
            return {"generation": self.generation, "running": sorted(self.running),
                    "events": self.events[-50:], "state": self.state}


LOG_FILE: Path | None = None


def log(msg: str) -> None:
    line = time.strftime("%H:%M:%S ") + msg
    print(line, flush=True)
    if LOG_FILE:
        with LOG_FILE.open("a") as f:
            f.write(line + "\n")


class Handler(BaseHTTPRequestHandler):
    library: Library
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet default logging; we log ourselves
        pass

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_file(self, path: Path, ctype: str) -> None:
        size = path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.end_headers()
        with path.open("rb") as f:
            while chunk := f.read(1 << 20):
                self.wfile.write(chunk)

    def do_GET(self):
        t0 = time.time()
        url = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(url.path)
        code = 200
        try:
            lib = self.library
            if path == "/_status":
                self._send(200, dumps(lib.status()), "application/json")
            elif path in ("/_asset-library-meta.json", "/_v1/asset-index.json") or path.startswith("/_v1/assets-"):
                listing = lib.build_listing()
                body = listing.get(path)
                if body is None:
                    code = 404
                    self._send(404, b"not found", "text/plain")
                else:
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
                asset = lib.asset_by_relpath(path.lstrip("/"))
                if not asset:
                    code = 404
                    self._send(404, b"unknown asset", "text/plain")
                else:
                    try:
                        p = lib.ensure_converted(asset)
                        self._send_file(p, "application/x-blender")
                    except Exception as ex:
                        code = 502
                        self._send(502, f"conversion failed: {ex}".encode(), "text/plain")
            else:
                code = 404
                self._send(404, b"not found", "text/plain")
        finally:
            log(f"GET {self.path} -> {code} ({time.time() - t0:.2f}s) X-Blender={self.headers.get('X-Blender')} "
                f"INM={self.headers.get('If-None-Match')} IMS={self.headers.get('If-Modified-Since')}")

    do_HEAD = do_GET

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if path.startswith("/_admin/reconvert/"):
            self.library.reconvert(path.rsplit("/", 1)[1])
            self._send(200, b"ok", "text/plain")
        else:
            self._send(404, b"not found", "text/plain")
        log(f"POST {self.path}")


def main() -> None:
    global LOG_FILE
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", type=Path, required=True)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--blender", default=str(Path.home() / "blender/blender-5.2.2-linux-x64/blender"))
    ap.add_argument("--convert-delay", type=float, default=3.0)
    ap.add_argument("--no-hash-update", action="store_true",
                    help="never replace the placeholder hash (negative test)")
    args = ap.parse_args()
    args.state_dir.mkdir(parents=True, exist_ok=True)
    LOG_FILE = args.state_dir / "requests.log"
    Handler.library = Library(args.state_dir, args.blender, args.convert_delay, not args.no_hash_update)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    log(f"serving on http://127.0.0.1:{args.port}/ state={args.state_dir}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
