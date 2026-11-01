# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase-2 test server: the real SimReady assets in cache/raw, converted on demand.

Same mechanism as tools/phase1/proto_server.py (placeholder hash -> conversion
on first request -> real hash), but the listing describes real NVIDIA assets
and the .blend files come from simready/convert/convert_asset.py.

python3 tools/phase2/test_server.py --state-dir DIR [--port 8765] [--max-res 0|2048|1024]

Test-only shortcuts (to be replaced in phase 3):
- assets = the folders already downloaded in cache/raw (no Hugging Face access);
- id_type (OBJECT/COLLECTION) taken from earlier conversion reports in out/blend/*/;
- thumbnails = NVIDIA PNGs served as they are (1024x1024).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "cache/raw"
BLENDER = Path.home() / "blender/blender-5.2.2-linux-x64/blender"
CONVERTER = ROOT / "simready/convert/convert_asset.py"
CATALOG_NS = uuid.UUID("2b9c1f3e-6a57-4f0e-9c1d-5e1d2e9a5172")  # same as convert_batch.py
CONVERTER_VERSION = "phase2-1"
BLENDER_VERSION = "5.2"
TEST_ASSETS = ["food_banana_a01", "kitchenware_bowl_b01", "ppe_eyeProtection_goggles_a01",
               "fixture_cabinet_a01", "rack_composition_a04", "warehouse_conveyorBelt_a01"]
USEFUL_PREFIXES = ("simready_usd/payloads/", "simready_usd/Textures/", "simready_usd/textures/")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dumps(o) -> bytes:
    return json.dumps(o, indent=1, sort_keys=True, ensure_ascii=False).encode()


def log(msg: str, log_file: Path) -> None:
    line = time.strftime("%H:%M:%S ") + msg
    print(line, flush=True)
    with log_file.open("a") as f:
        f.write(line + "\n")


class Asset:
    def __init__(self, aid: str) -> None:
        self.id = aid
        d = RAW / aid
        self.prefix = aid.split("_")[0]
        self.catalog_path = f"SimReady/{self.prefix[0].upper()}{self.prefix[1:]}"
        self.catalog_id = str(uuid.uuid5(CATALOG_NS, self.catalog_path))
        root = json.loads((d / ".metadata/com.nvidia.simready.root_usds.json").read_text())["entries"][0]
        self.root = root
        thumb = d / Path(root).parent / ".thumbs/256x256" / (Path(root).name + ".png")
        self.thumb = thumb
        bom = json.loads((d / ".metadata/com.nvidia.simready.packaging.bom.json").read_text())["items"]
        self.useful_bytes = sum(i["size"] for i in bom
                                if i["relative_path"] == root or i["relative_path"].startswith(USEFUL_PREFIXES))
        self.bom_digest = sha256_bytes(json.dumps(sorted((i["relative_path"], i["hash"]["sha256"]) for i in bom)).encode())
        rep = next((p for p in sorted((ROOT / "out/blend").glob(f"*/{aid}.json"))), None)
        self.id_type = json.loads(rep.read_text())["asset_type"] if rep else "COLLECTION"

    @property
    def relpath(self) -> str:
        return f"blend/{self.prefix}/{self.id}.blend"

    @property
    def tags(self) -> list[str]:
        return [self.prefix, "simready"]


class Library:
    def __init__(self, state_dir: Path, max_res: int) -> None:
        self.state_dir = state_dir
        self.max_res = max_res
        self.assets = {a: Asset(a) for a in TEST_ASSETS if (RAW / a).exists()}
        self.lock = threading.RLock()
        self.asset_locks = {a: threading.Lock() for a in self.assets}
        self.generation = 0
        self.events: list[dict] = []
        self.running: set[str] = set()
        self.log_file = state_dir / "requests.log"
        self.state_path = state_dir / "state.json"
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}

    def out_path(self, a: Asset) -> Path:
        return self.state_dir / f"res{self.max_res}" / a.relpath

    def file_entry(self, a: Asset) -> dict:
        key = f"{a.id}@{self.max_res}"
        st = self.state.get(key)
        if st and self.out_path(a).exists():
            h, size = st["hash"], st["size"]
        else:
            h = sha256_bytes(f"{a.id}:{a.bom_digest}:{self.max_res}:{CONVERTER_VERSION}:pending".encode())
            size = a.useful_bytes
        return {"path": a.relpath, "size_in_bytes": size, "hash": "SHA256:" + h, "blender_version": BLENDER_VERSION}

    def build_listing(self) -> dict[str, bytes]:
        with self.lock:
            assets, files = [], []
            for a in self.assets.values():
                assets.append({
                    "name": a.id, "id_type": a.id_type, "files": [a.relpath],
                    "thumbnail": {"url": f"thumbs/{a.id}.png", "hash": "SHA256:" + sha256_file(a.thumb)},
                    "bl_versions": {"min": BLENDER_VERSION},
                    "meta": {"catalog_id": a.catalog_id, "tags": a.tags, "author": "NVIDIA",
                             "license": "CC-BY-4.0", "copyright": "© NVIDIA Corporation",
                             "description": f"NVIDIA SimReady asset '{a.id}'.\n"
                                            f"https://huggingface.co/datasets/nvidia/simready-assets/tree/main/{a.id}"},
                })
                files.append(self.file_entry(a))
            page = dumps({"asset_count": len(assets), "file_count": len(files), "assets": assets, "files": files})
            cats, seen = [], set()
            for a in self.assets.values():
                parts = a.catalog_path.split("/")
                for n in range(1, len(parts) + 1):
                    p = "/".join(parts[:n])
                    if p not in seen:
                        seen.add(p)
                        cats.append({"path": p, "simple_name": p.replace("/", "-"),
                                     "uuids": [str(uuid.uuid5(CATALOG_NS, p))]})
            index = dumps({"schema_version": "1.0.0", "asset_size_bytes": sum(f["size_in_bytes"] for f in files),
                           "asset_count": len(assets), "file_count": len(files),
                           "pages": [{"url": "_v1/assets-00000.json", "hash": "SHA256:" + sha256_bytes(page)}],
                           "catalogs": cats})
            meta = dumps({"name": "NVIDIA SimReady (phase-2 test)",
                          "contact": {"name": "Tonysa87",
                                      "url": "https://huggingface.co/datasets/nvidia/simready-assets"},
                          "api_versions": {"v1": {"url": "_v1/asset-index.json",
                                                  "hash": "SHA256:" + sha256_bytes(index)}}})
            return {"/_asset-library-meta.json": meta, "/_v1/asset-index.json": index, "/_v1/assets-00000.json": page}

    def ensure_converted(self, a: Asset) -> Path:
        out = self.out_path(a)
        with self.asset_locks[a.id]:
            if out.exists():
                return out
            with self.lock:
                self.running.add(a.id)
            t0 = time.time()
            try:
                report = out.with_suffix(".json")
                p = subprocess.run([str(BLENDER), "-b", "--factory-startup", "--python", str(CONVERTER), "--",
                                    "--asset-dir", str(RAW / a.id), "--out", str(out), "--max-res", str(self.max_res),
                                    "--catalog-id", a.catalog_id, "--tags", ",".join(a.tags), "--report", str(report)],
                                   capture_output=True, text=True, timeout=280)
                if "SIMREADY_CONVERT_OK" not in p.stdout or not out.exists():
                    raise RuntimeError((p.stdout + p.stderr)[-800:])
                h, size = sha256_file(out), out.stat().st_size
                with self.lock:
                    self.state[f"{a.id}@{self.max_res}"] = {"hash": h, "size": size}
                    self.state_path.write_text(json.dumps(self.state, indent=1))
                    self.generation += 1
                    self.events.append({"t": time.time(), "type": "converted", "asset": a.id,
                                        "seconds": round(time.time() - t0, 2), "generation": self.generation})
                log(f"converted {a.id} (max_res={self.max_res}) in {time.time() - t0:.1f}s -> {size / 1e6:.1f} MB", self.log_file)
                return out
            except Exception as ex:
                with self.lock:
                    self.events.append({"t": time.time(), "type": "error", "asset": a.id, "message": str(ex)[-300:]})
                log(f"conversion FAILED {a.id}: {str(ex)[-300:]}", self.log_file)
                raise
            finally:
                with self.lock:
                    self.running.discard(a.id)

    def status(self) -> dict:
        with self.lock:
            return {"generation": self.generation, "running": sorted(self.running), "events": self.events[-50:]}


class Handler(BaseHTTPRequestHandler):
    lib: Library
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, p: Path, ctype: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(p.stat().st_size))
        self.end_headers()
        with p.open("rb") as f:
            while chunk := f.read(1 << 20):
                self.wfile.write(chunk)

    def do_GET(self):
        t0, code = time.time(), 200
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        lib = self.lib
        try:
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
            elif path.startswith("/thumbs/") and path.endswith(".png"):
                a = lib.assets.get(Path(path).stem)
                if a:
                    self._send_file(a.thumb, "image/png")
                else:
                    code = 404
                    self._send(404, b"not found", "text/plain")
            elif path.startswith("/blend/"):
                a = next((x for x in lib.assets.values() if "/" + x.relpath == path), None)
                if not a:
                    code = 404
                    self._send(404, b"unknown asset", "text/plain")
                else:
                    try:
                        self._send_file(lib.ensure_converted(a), "application/x-blender")
                    except Exception as ex:
                        code = 502
                        self._send(502, f"conversion failed: {ex}".encode()[-500:], "text/plain")
            else:
                code = 404
                self._send(404, b"not found", "text/plain")
        finally:
            log(f"GET {self.path} -> {code} ({time.time() - t0:.2f}s) X-Blender={self.headers.get('X-Blender')}", lib.log_file)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", type=Path, required=True)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--max-res", type=int, default=2048, choices=(0, 2048, 1024))
    args = ap.parse_args()
    args.state_dir.mkdir(parents=True, exist_ok=True)
    Handler.lib = Library(args.state_dir, args.max_res)
    log(f"serving {len(Handler.lib.assets)} assets on http://127.0.0.1:{args.port}/ max_res={args.max_res} "
        f"state={args.state_dir}", Handler.lib.log_file)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
