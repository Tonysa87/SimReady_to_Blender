# SPDX-License-Identifier: GPL-3.0-or-later
"""On-demand conversions: filtered download from Hugging Face (verified with
the BOM sha256) -> headless Blender running simready/convert/convert_asset.py
-> .blend in the cache, with its real hash and size recorded in state.json.

Concurrent requests for the same asset share one job (single flight).
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .. import common
from .catalogs import catalog_uuid
from .hf import HFClient, sha256_file
from .index import AssetRecord

log = logging.getLogger("simready.jobs")

REPO_ROOT = Path(__file__).resolve().parents[2]
CONVERTER = REPO_ROOT / "simready/convert/convert_asset.py"


@dataclass
class Settings:
    blender: Path
    state_dir: Path
    max_res: int = 2048          # 0 = original
    workers: int = 1             # parallel conversions
    download_threads: int = 4
    keep_raw: bool = False       # keep downloaded USD/textures after a successful conversion
    keep_blend: bool = False     # keep the .blend after delivering it (Blender keeps its own copy)
    timeout: int = 3600          # per conversion, seconds


def _child_kwargs() -> dict:
    """Converter processes: own process group (Linux) so they can be killed together."""
    import os
    if os.name == "nt":
        # blender.exe is a console program: without CREATE_NO_WINDOW every conversion opens a console.
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


def _replace(src: Path, dst: Path, tries: int = 10) -> None:
    """os.replace with retries: on Windows antivirus/indexers briefly lock new files."""
    for i in range(tries):
        try:
            src.replace(dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.2 * (i + 1))


def placeholder_hash(rec: AssetRecord, max_res: int) -> str:
    key = f"{rec.id}:{rec.content_digest}:{max_res}:{common.CONVERTER_VERSION}:pending"
    return hashlib.sha256(key.encode()).hexdigest()


class State:
    """Converted files: {"<asset>@<max_res>": {hash, size, id_type, name, content_digest, converter, time}}."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        try:
            self.data: dict = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def get(self, rec: AssetRecord, max_res: int) -> dict | None:
        """The entry if it is current (same content, same converter). The file itself may be
        gone (delivered and dropped): the listing still announces the real hash."""
        with self.lock:
            st = self.data.get(f"{rec.id}@{max_res}")
        if not (st and st.get("file") and st.get("content_digest") == rec.content_digest
                and st.get("converter") == common.CONVERTER_VERSION):
            return None
        return st

    def put(self, rec: AssetRecord, max_res: int, entry: dict) -> None:
        with self.lock:
            self.data[f"{rec.id}@{max_res}"] = entry
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(self.data, indent=1, sort_keys=True), encoding="utf-8")
            tmp.replace(self.path)


class Converter:
    def __init__(self, settings: Settings, hf: HFClient, catalog_path_of, on_event, preview_of=None) -> None:
        self.s = settings
        self.preview_of = preview_of               # (rev, rec) -> Path of the 256² preview, optional
        self.hf = hf
        self.catalog_path_of = catalog_path_of     # asset id -> catalog path
        self.on_event = on_event                   # callback(dict), e.g. "converted"/"error"
        self.state = State(settings.state_dir / "state.json")
        self.pool = ThreadPoolExecutor(settings.workers, thread_name_prefix="convert")
        self.dl_pool = ThreadPoolExecutor(settings.download_threads, thread_name_prefix="download")
        self.jobs: dict[str, Future] = {}
        self.children: set[subprocess.Popen] = set()
        self.senders: dict[str, int] = {}
        self.progress: dict[str, dict] = {}
        self.lock = threading.Lock()

    # ----------------------------------------------------------------- paths

    def raw_dir(self, rec: AssetRecord) -> Path:
        return self.s.state_dir / "raw" / rec.id

    def blend_dir(self, rec: AssetRecord) -> Path:
        return self.s.state_dir / "blend" / f"res{self.s.max_res}" / rec.prefix

    def current(self, rec: AssetRecord) -> dict | None:
        """Real hash/size/type/name of the converted asset (for the listing), or None."""
        return self.state.get(rec, self.s.max_res)

    def blend_path(self, rec: AssetRecord) -> Path | None:
        """The converted file, if it is still on disk."""
        st = self.current(rec)
        if not st:
            return None
        p = self.blend_dir(rec) / st["file"]
        try:
            return p if p.stat().st_size == st.get("size") else None
        except OSError:
            return None

    # -------------------------------------------------------------- delivery

    def acquire(self, rec: AssetRecord, path: Path) -> bool:
        """Mark `path` as being sent; False if it has already been dropped."""
        with self.lock:
            if not path.is_file():
                return False
            self.senders[rec.id] = self.senders.get(rec.id, 0) + 1
            return True

    def release(self, rec: AssetRecord, path: Path, delivered: bool) -> None:
        """End of a send. After a complete delivery the file is dropped (unless keep_blend)
        once no other request is still sending it."""
        with self.lock:
            n = self.senders.get(rec.id, 1) - 1
            if n > 0:
                self.senders[rec.id] = n
            else:
                self.senders.pop(rec.id, None)
                if delivered and not self.s.keep_blend:
                    try:
                        path.unlink()
                    except OSError:  # Windows: still open somewhere; dropped at the next delivery
                        pass
        if delivered:
            self.on_event({"type": "delivered", "asset": rec.id})

    # ------------------------------------------------------------------ jobs

    def ensure(self, rev: str, rec: AssetRecord) -> Future:
        """Future resolving to the .blend path (single flight per asset)."""
        with self.lock:
            fut = self.jobs.get(rec.id)
            if fut and not fut.done():
                return fut
            done = self.blend_path(rec)
            if done:
                fut = Future()
                fut.set_result(done)
                return fut
            self.progress[rec.id] = {"stage": "queued", "since": time.time()}
            fut = self.pool.submit(self._run, rev, rec)
            self.jobs[rec.id] = fut
            return fut

    def running(self) -> dict[str, dict]:
        with self.lock:
            return {a: dict(p) for a, p in self.progress.items()}

    def _stage(self, rec: AssetRecord, stage: str, **extra) -> None:
        with self.lock:
            self.progress[rec.id] = {"stage": stage, "since": time.time(), **extra}

    def _run(self, rev: str, rec: AssetRecord) -> Path:
        t0 = time.time()
        try:
            done = self.blend_path(rec)
            if done:  # converted while queued
                return done
            self._download(rev, rec)
            out = self._convert(rec, self._preview(rev, rec))
            dt = time.time() - t0
            self.on_event({"type": "converted", "asset": rec.id, "seconds": round(dt, 1),
                           "size": out.stat().st_size})
            log.info("converted %s in %.1f s (%.1f MB)", rec.id, dt, out.stat().st_size / 1e6)
            if not self.s.keep_raw:
                shutil.rmtree(self.raw_dir(rec), ignore_errors=True)
            return out
        except Exception as e:
            log.error("conversion of %s failed: %s", rec.id, e)
            self.on_event({"type": "error", "asset": rec.id, "message": str(e)[-400:]})
            raise
        finally:
            with self.lock:
                self.progress.pop(rec.id, None)

    def _download(self, rev: str, rec: AssetRecord) -> None:
        raw = self.raw_dir(rec)
        total = rec.useful_bytes
        done = [0]
        lock = threading.Lock()

        def tick(n: int) -> None:
            with lock:
                done[0] += n
            self._stage(rec, "download", bytes=done[0], total=total)

        self._stage(rec, "download", bytes=0, total=total)
        failed = threading.Event()

        def one(rel, size, sha):
            if failed.is_set():
                return
            try:
                self.hf.download(rev, f"{rec.id}/{rel}", raw / rel, size, sha, tick, cancel=failed.is_set)
            except Exception:
                failed.set()
                raise

        futs = [self.dl_pool.submit(one, *f) for f in rec.files]
        errors = []
        for f in futs:  # wait for every file, so no thread keeps writing after we return
            try:
                f.result()
            except Exception as e:
                errors.append(e)
        if errors:
            raise errors[0]

    def _preview(self, rev: str, rec: AssetRecord) -> Path | None:
        if not self.preview_of or not rec.thumb:
            return None
        try:
            return self.preview_of(rev, rec)
        except Exception as e:  # a missing preview must not block the conversion
            log.warning("%s: no preview for the .blend: %s", rec.id, e)
            return None

    def _convert(self, rec: AssetRecord, preview: Path | None) -> Path:
        self._stage(rec, "convert")
        bdir = self.blend_dir(rec)
        bdir.mkdir(parents=True, exist_ok=True)
        job = f"{rec.id}.{uuid.uuid4().hex[:8]}"
        out, report, log_path = bdir / f"{job}.tmp.blend", bdir / f"{rec.id}.json", bdir / f"{rec.id}.log"
        cmd = [str(self.s.blender), "-b", "--factory-startup", "--python", str(CONVERTER), "--",
               "--asset-dir", str(self.raw_dir(rec)), "--asset-id", rec.id, "--root", rec.root, "--out", str(out),
               "--max-res", str(self.s.max_res), "--catalog-id", catalog_uuid(self.catalog_path_of(rec.id)),
               "--tags", ",".join(common.asset_tags(rec.id)), "--report", str(report)]
        if preview:
            cmd += ["--preview", str(preview)]
        try:
            with log_path.open("w", encoding="utf-8", errors="replace") as lf:
                proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                        **_child_kwargs())
                with self.lock:
                    self.children.add(proc)
                try:
                    code = proc.wait(timeout=self.s.timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                    raise RuntimeError(f"conversion took more than {self.s.timeout} s")
                finally:
                    with self.lock:
                        self.children.discard(proc)
            text = log_path.read_text(encoding="utf-8", errors="replace")
            if code != 0 or "SIMREADY_CONVERT_OK" not in text or not out.is_file():
                raise RuntimeError(f"converter failed (exit {code}): {text[-600:]}")
            info = json.loads(report.read_text(encoding="utf-8"))
            h = sha256_file(out)
            final = bdir / f"{rec.id}.{h[:16]}.blend"  # new name per content: never replace a file being served
            _replace(out, final)
        finally:
            out.unlink(missing_ok=True)
        entry = {"file": final.name, "hash": h, "size": final.stat().st_size, "id_type": info["asset_type"],
                 "name": info["asset_name"], "content_digest": rec.content_digest,
                 "converter": common.CONVERTER_VERSION, "time": time.time()}
        if (entry["id_type"], entry["name"]) != (rec.id_type, rec.name):
            log.warning("%s: predicted %s %r, converted %s %r", rec.id, rec.id_type, rec.name,
                        entry["id_type"], entry["name"])
        self.state.put(rec, self.s.max_res, entry)
        for stale in bdir.glob(f"{rec.id}.*.blend"):  # older versions (may fail on Windows if open: retried later)
            if stale != final:
                try:
                    stale.unlink()
                except OSError:
                    pass
        return final

    def kill_children(self) -> None:
        """Stop running converters (server exit)."""
        with self.lock:
            procs = list(self.children)
        for p in procs:
            try:
                p.kill()
            except OSError:
                pass
