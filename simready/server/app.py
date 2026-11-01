# SPDX-License-Identifier: GPL-3.0-or-later
"""Local HTTP server (127.0.0.1 only) publishing the dataset as a Blender
remote asset library.

Endpoints
  /_asset-library-meta.json, /_v1/asset-index.json, /_v1/assets-NNNNN.json   listing (ETag -> 304)
  /thumbs/<asset>.webp     256² preview, made on first request
  /blend/<prefix>/<asset>.blend   converted on first request (see "keep-alive")
  /_status                 JSON for the add-on: generation, index build, jobs, events

Keep-alive: Blender waits at most 300 s between two bytes. A conversion can
take longer, so when the .blend is not ready within a few seconds the answer
starts at once as a gzip stream (Blender asks for gzip and decodes it itself)
and, until the file exists, empty deflate blocks are sent every few seconds.
If the conversion fails the connection is closed before the gzip trailer:
Blender sees a broken download and keeps nothing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import socket
import struct
import threading
import time
import urllib.parse
import zlib
from concurrent.futures import TimeoutError as FutureTimeout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import common
from .catalogs import Catalogs
from .hf import HFClient
from .index import Index, IndexBuilder
from .jobs import Converter, Settings
from .listing import blend_relpath, build
from .thumbs import Thumbnails

log = logging.getLogger("simready.server")

SERVER_VERSION = "0.3"
EMPTY_STORED_BLOCK = b"\x00\x00\x00\xff\xff"  # non-final, zero-length stored deflate block
# Blender checks its Cancel button once per 8 KB received: make each keep-alive a bit larger.
KEEPALIVE = EMPTY_STORED_BLOCK * 1640
KEEPALIVE_EVERY = 15.0        # seconds between keep-alive blocks (Blender's limit is 300)
PLAIN_WAIT = 4.0              # wait this long for a quick conversion before switching to gzip


class Library:
    """Everything the handler needs: index, catalogs, converter, thumbnails, listing cache."""

    def __init__(self, settings: Settings, hf: HFClient, mirror: Path | None = None,
                 revision: str | None = None, refresh: bool = True, prefetch_thumbs: bool = False,
                 seed_index: Path | None = None, check_hours: float = 24) -> None:
        self.prefetch_thumbs = prefetch_thumbs
        self.s = settings
        self.hf = hf
        self.lock = threading.RLock()
        self.catalogs = Catalogs.load()
        self.index_path = settings.state_dir / "index" / "index.json"
        self.index: Index = Index.load(self.index_path) or Index()
        if not self.index.assets and seed_index:  # first start: a ready index, updated incrementally below
            seed = Index.load(seed_index)
            if seed and seed.assets:
                seed.complete = False  # always reconciled with Hugging Face before being trusted as complete
                self.index = seed
                log.info("index: seeded with %d assets from %s", len(seed.assets), seed_index)
        self.catalog_paths = self.catalogs.assign(list(self.index.assets))
        self.generation = 1
        self.events: list[dict] = []
        self.event_seq = 0
        self.index_status = {"building": False, "done": 0, "total": 0, "error": ""}
        self.thumbs = Thumbnails(hf, settings.state_dir / "thumbs")
        self.converter = Converter(settings, hf, lambda a: self.catalog_paths[a], self.add_event,
                                   preview_of=lambda rev, rec: self.thumbs.get(rec.rev or rev, rec)[0])
        self._listing: tuple[int, dict[str, bytes]] | None = None
        self._first_build = False
        self._by_blend = {}
        self._reindex()
        self.builder = IndexBuilder(hf, self.index_path, mirror=mirror, on_progress=self._index_progress)
        if refresh or not self.index.complete:
            threading.Thread(target=self.refresh_index, kwargs={"revision": revision}, daemon=True,
                             name="index").start()
        if check_hours > 0 and not revision:
            threading.Thread(target=self._periodic_check, args=(check_hours * 3600,), daemon=True,
                             name="index-check").start()
        elif prefetch_thumbs:
            self._start_prefetch()

    def _periodic_check(self, every: float) -> None:
        """Look for a new dataset revision now and then (one API request when nothing changed)."""
        while True:
            time.sleep(every)
            self.refresh_index()

    def _start_prefetch(self) -> None:
        idx = self.index
        threading.Thread(target=self.thumbs.prefetch, args=(idx.revision, list(idx.assets.values())),
                         kwargs={"stop": lambda: self.index is not idx}, daemon=True, name="thumbs").start()

    # ---------------------------------------------------------------- events

    def add_event(self, ev: dict) -> None:
        with self.lock:
            self.event_seq += 1
            self.generation += 1
            self.events.append({"id": self.event_seq, "t": round(time.time(), 1), "generation": self.generation, **ev})
            del self.events[:-200]

    def status(self) -> dict:
        with self.lock:
            return {
                "server": {"version": SERVER_VERSION, "converter": common.CONVERTER_VERSION,
                           "max_res": self.s.max_res},
                "generation": self.generation,
                "index": {"revision": self.index.revision, "assets": len(self.index.assets),
                          "failed": len(self.index.failed), "complete": self.index.complete, **self.index_status},
                "jobs": self.converter.running(),
                "events": self.events[-50:],
            }

    # ----------------------------------------------------------------- index

    def _reindex(self) -> None:
        self.catalog_paths = self.catalogs.assign(list(self.index.assets))
        self._by_blend = {"/" + blend_relpath(r): r for r in self.index.assets.values()}

    def snapshot(self) -> tuple[Index, dict]:
        """Index and blend-path map that belong together (an index swap replaces both)."""
        with self.lock:
            return self.index, self._by_blend

    def _index_progress(self, idx: Index, done: int, total: int) -> None:
        with self.lock:
            self.index_status.update(done=done, total=total)
            if self._first_build and done and done % 200 == 0:  # publish partial listings meanwhile
                self._swap(Index(revision=idx.revision, assets=dict(idx.assets)))

    def _swap(self, idx: Index) -> None:
        with self.lock:
            self.index = idx
            self._reindex()
        self.add_event({"type": "index", "revision": idx.revision, "assets": len(idx.assets)})

    def refresh_index(self, force: bool = False, revision: str | None = None) -> None:
        with self.lock:
            if self.index_status["building"]:
                return
            self.index_status.update(building=True, error="")
            self._first_build = not self.index.assets
        try:
            new = self.builder.refresh(self.index if self.index.assets else None, force=force, revision=revision)
            if new is not self.index:
                self._swap(new)
            if self.prefetch_thumbs:
                self._start_prefetch()
        except Exception as e:  # network down: keep serving the old index
            log.exception("index refresh failed")
            with self.lock:
                self.index_status["error"] = str(e)[:300]
        finally:
            with self.lock:
                self.index_status["building"] = False
                self._first_build = False

    # --------------------------------------------------------------- listing

    def listing(self) -> dict[str, bytes]:
        with self.lock:
            if self._listing and self._listing[0] == self.generation:
                return self._listing[1]
            gen, index, paths = self.generation, self.index, dict(self.catalog_paths)
        body = build(index, paths, self.converter.current, self.s.max_res)
        with self.lock:
            self._listing = (gen, body)
        return body


class Handler(BaseHTTPRequestHandler):
    lib: Library
    port: int
    require_x_blender = True
    protocol_version = "HTTP/1.1"
    server_version = f"SimReadyToBlender/{SERVER_VERSION}"

    def log_message(self, fmt, *args):  # replaced by our own access log
        pass

    # --------------------------------------------------------------- helpers

    def _send(self, code: int, body: bytes, ctype: str = "text/plain; charset=utf-8", headers: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)
        return code

    def _send_file(self, path: Path, ctype: str, etag: str | None = None):
        if etag and self.headers.get("If-None-Match") == etag:
            return self._not_modified(etag)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(path.stat().st_size))
        if etag:
            self.send_header("ETag", etag)
        self.end_headers()
        with path.open("rb") as f:
            while chunk := f.read(1 << 20):
                self.wfile.write(chunk)
        return 200

    def _not_modified(self, etag: str):
        self.send_response(304)
        self.send_header("ETag", etag)
        self.send_header("Content-Length", "0")
        self.end_headers()
        return 304

    def _chunk(self, data: bytes) -> None:
        if data:
            self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
            self.wfile.flush()

    def _abort(self) -> None:
        """Reset the connection (after a started response): the client must see a failure."""
        self.close_connection = True
        try:
            self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _host_ok(self) -> bool:
        """Refuse other Host names (DNS rebinding from a web page)."""
        host = (self.headers.get("Host") or "").lower()
        return host in {f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"[::1]:{self.port}"}

    # ---------------------------------------------------------------- routes

    def end_headers(self):
        self._headers_sent = True
        super().end_headers()

    def do_GET(self):
        self._headers_sent = False
        t0, code = time.time(), 500
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        try:
            if not self._host_ok():
                code = self._send(421, b"wrong host")
            elif path == "/_status":
                code = self._send(200, json.dumps(self.lib.status(), indent=1).encode(), "application/json",
                                  {"Cache-Control": "no-store"})
            elif path in ("/_asset-library-meta.json", "/_v1/asset-index.json") or re.fullmatch(r"/_v1/assets-\d{5}\.json", path):
                code = self._listing(path)
            elif m := re.fullmatch(r"/thumbs/([A-Za-z0-9_.-]+)\.webp", path):
                code = self._thumb(m.group(1))
            elif path.startswith("/blend/"):
                code = self._blend(path)
            else:
                code = self._send(404, b"not found")
        except ConnectionError:  # client gone (incl. Windows' ConnectionAbortedError)
            code = 499
            self.close_connection = True
        except Exception as e:
            log.exception("GET %s", path)
            if self._headers_sent:
                self._abort()  # mid-body: a new status line would corrupt the stream
            else:
                self.close_connection = True
                try:
                    code = self._send(500, str(e).encode()[-300:])
                except OSError:
                    pass
        finally:
            log.info("GET %s -> %s (%.2fs)%s", self.path, code, time.time() - t0,
                     "" if self.headers.get("X-Blender") else " [no X-Blender]")

    def _listing(self, path: str) -> int:
        body = self.lib.listing().get(path)
        if body is None:
            return self._send(404, b"no such page")
        etag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
        if self.headers.get("If-None-Match") == etag:
            return self._not_modified(etag)
        return self._send(200, body, "application/json", {"ETag": etag, "Cache-Control": "no-cache"})

    def _thumb(self, asset: str) -> int:
        idx, _ = self.lib.snapshot()
        rec = idx.assets.get(asset)
        if not rec or not rec.thumb:
            return self._send(404, b"unknown asset")
        try:
            p, ctype = self.lib.thumbs.get(rec.rev or idx.revision, rec)
        except Exception as e:
            log.warning("thumbnail %s: %s", asset, e)
            return self._send(502, f"thumbnail failed: {e}".encode()[-300:])
        return self._send_file(p, ctype, etag='"' + rec.thumb[2][:32] + '"')

    def _blend(self, path: str) -> int:
        if self.require_x_blender and not self.headers.get("X-Blender"):
            return self._send(403, b"only Blender may request conversions")
        lib = self.lib
        idx, by_blend = lib.snapshot()
        rec = by_blend.get(path)
        if not rec:
            return self._send(404, b"unknown asset")
        fut = lib.converter.ensure(rec.rev or idx.revision, rec)
        gzip_ok = "gzip" in (self.headers.get("Accept-Encoding") or "")
        try:
            out = fut.result(timeout=PLAIN_WAIT if gzip_ok else lib.s.timeout)
        except FutureTimeout:
            return self._blend_keepalive(fut, rec.id)
        except Exception as e:
            return self._send(502, f"conversion failed: {e}".encode()[-500:])
        st = lib.converter.current(rec)
        return self._send_file(out, "application/x-blender", etag=f'"{st["hash"][:32]}"' if st else None)

    def _blend_keepalive(self, fut, asset: str) -> int:
        self.send_response(200)
        self.send_header("Content-Type", "application/x-blender")
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        comp = zlib.compressobj(1, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
        self._chunk(comp.flush(zlib.Z_SYNC_FLUSH))  # gzip header + empty block, byte aligned
        while True:
            try:
                out = fut.result(timeout=KEEPALIVE_EVERY)
                break
            except FutureTimeout:
                self._chunk(KEEPALIVE)
            except Exception as e:
                log.warning("%s: conversion failed during keep-alive, aborting the response: %s", asset, e)
                self._abort()  # no terminating chunk, no gzip trailer: the client sees an error
                return 502
        with out.open("rb") as f:
            while chunk := f.read(1 << 20):
                self._chunk(comp.compress(chunk))
        self._chunk(comp.flush())
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()
        return 200


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # Linux: SO_REUSEADDR only skips TIME_WAIT, a second listener still fails.
    # Windows: SO_REUSEADDR would let two servers share the port, so keep it off.
    allow_reuse_address = os.name != "nt"


def serve(lib: Library, port: int, require_x_blender: bool = True) -> None:
    Handler.lib = lib
    Handler.port = port
    Handler.require_x_blender = require_x_blender
    httpd = Server(("127.0.0.1", port), Handler)
    log.info("serving on http://127.0.0.1:%d/ (state %s, max_res %d)", port, lib.s.state_dir, lib.s.max_res)
    httpd.serve_forever()
