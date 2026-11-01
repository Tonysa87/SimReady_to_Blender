# SPDX-License-Identifier: GPL-3.0-or-later
"""Asset Browser thumbnails: the NVIDIA preview (a 1024² PNG, despite the
`.thumbs/256x256` folder name) reduced to 256² WebP, on demand and cached.

Uses OpenImageIO, bundled with Blender's Python (also standalone, also on
Windows). Without it the original PNG is served as is.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from pathlib import Path

from .hf import HFClient
from .index import AssetRecord

log = logging.getLogger("simready.thumbs")

THUMB_VERSION = "webp256-1"  # part of the listing hash: bump when the output changes
SIZE = 256

try:
    import OpenImageIO as oiio
except ImportError:  # not running on Blender's Python
    oiio = None


def listing_hash(rec: AssetRecord) -> str:
    """Hash announced in the listing. Blender only uses it as a cache key (`?hash=`)."""
    src = rec.thumb[2] if rec.thumb else rec.id
    return "SHA256:" + hashlib.sha256(f"{src}:{THUMB_VERSION if oiio else 'png'}".encode()).hexdigest()


def reduce(src: Path, dst: Path) -> None:
    buf = oiio.ImageBuf(str(src))
    spec = buf.spec()
    if spec.width == 0:
        raise RuntimeError(f"cannot read {src.name}: {buf.geterror()}")
    scale = SIZE / max(spec.width, spec.height)
    w, h = max(1, round(spec.width * scale)), max(1, round(spec.height * scale))
    out = oiio.ImageBufAlgo.resize(buf, roi=oiio.ROI(0, w, 0, h, 0, 1, 0, spec.nchannels))
    if spec.format != oiio.UINT8:
        out = out.copy(oiio.UINT8)  # 16-bit PNG previews
    tmp = dst.with_name(dst.name + ".tmp.webp")
    if not out.write(str(tmp)):
        raise RuntimeError(f"cannot write {dst.name}: {out.geterror()}")
    tmp.replace(dst)


class Thumbnails:
    def __init__(self, hf: HFClient, cache_dir: Path) -> None:
        self.hf = hf
        self.dir = cache_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.locks: dict[str, threading.Lock] = {}
        self.guard = threading.Lock()
        self.slots = threading.BoundedSemaphore(4)

    def path_for(self, rec: AssetRecord) -> tuple[Path, str]:
        if oiio:
            return self.dir / f"{rec.id}.{rec.thumb[2][:16]}.webp", "image/webp"
        return self.dir / f"{rec.id}.{rec.thumb[2][:16]}.png", "image/png"

    def prefetch(self, rev: str, records, threads: int = 3, stop=None) -> int:
        """Make every missing thumbnail (background, low priority). Returns how many were made."""
        from concurrent.futures import ThreadPoolExecutor

        todo = [r for r in records if r.thumb and not self.path_for(r)[0].exists()]
        log.info("prefetching %d thumbnails", len(todo))

        def one(r):
            if stop and stop():
                return 0
            try:
                self.get(rev, r)
                return 1
            except Exception as e:
                log.warning("thumbnail %s: %s", r.id, e)
                return 0

        with ThreadPoolExecutor(threads, thread_name_prefix="thumbs") as ex:
            made = sum(ex.map(one, todo))
        log.info("prefetch done: %d thumbnails", made)
        return made

    def get(self, rev: str, rec: AssetRecord) -> tuple[Path, str]:
        """Path and content type of the thumbnail, downloading/reducing it if needed."""
        if not rec.thumb:
            raise FileNotFoundError(f"{rec.id}: no preview in the dataset")
        dst, ctype = self.path_for(rec)
        if dst.exists():
            return dst, ctype
        with self.guard:
            lock = self.locks.setdefault(rec.id, threading.Lock())
        with lock, self.slots:
            if dst.exists():
                return dst, ctype
            rel, size, sha = rec.thumb
            png = self.dir / f"{rec.id}.{sha[:16]}.png"
            self.hf.download(rev, f"{rec.id}/{rel}", png, size=size, sha256=sha)
            if oiio:
                try:
                    reduce(png, dst)
                finally:
                    png.unlink(missing_ok=True)
            log.info("thumbnail %s ready", rec.id)
            return dst, ctype
