# SPDX-License-Identifier: GPL-3.0-or-later
"""Index of the dataset: one record per asset folder, built from Hugging Face.

Per asset we read `.metadata/com.nvidia.simready.root_usds.json`, the BOM
(size + sha256 of every file) and a few small USD layers used to predict the
datablock type and name (structure.py). Records are keyed by the folder's git
tree oid: when the dataset revision changes, only folders whose oid changed are
fetched again.

Downloaded data is untrusted: asset ids and relative paths are validated
before they are used to build local paths.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from .hf import HFClient

log = logging.getLogger("simready.index")

INDEX_FORMAT = 1
ROOT_USDS = ".metadata/com.nvidia.simready.root_usds.json"
BOM = ".metadata/com.nvidia.simready.packaging.bom.json"
LICENSE = ".metadata/LICENSE.txt"
_TOP_USD = re.compile(r"simready_usd/[^/]+\.usd[ac]?")
_ASSET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def valid_asset_id(name: str) -> bool:
    return bool(_ASSET_ID.match(name)) and ".." not in name


def safe_relpath(rel: str) -> bool:
    """A relative POSIX path that stays inside the asset folder."""
    if not rel or "\\" in rel or "\x00" in rel or ":" in rel:
        return False
    parts = rel.split("/")
    return not rel.startswith("/") and all(x not in ("", ".", "..") for x in parts)


def wanted_for_conversion(rel: str, root: str) -> bool:
    """Files the converter needs: the whole `simready_usd/` tree except the
    source assets, thumbnails and validation reports; plus the license."""
    if rel == root or rel == LICENSE:
        return True
    if not rel.startswith("simready_usd/") or rel.startswith(("simready_usd/source_assets/", "simready_usd/.simready/")):
        return False
    return ".thumbs" not in PurePosixPath(rel).parts


@dataclass
class AssetRecord:
    id: str
    oid: str                      # git tree oid of the asset folder on HF
    root: str                     # root USD, relative to the asset folder
    files: list[list] = field(default_factory=list)   # [relpath, size, sha256] to download
    thumb: list | None = None     # [relpath, size, sha256] of the NVIDIA preview PNG
    content_digest: str = ""      # sha256 over (path, sha256) of `files`
    id_type: str = "COLLECTION"   # predicted, see structure.py
    name: str = ""
    mesh_count: int = 0
    confidence: str = "low"
    method: str = ""
    rev: str = ""                 # dataset commit the files are downloaded from

    @property
    def prefix(self) -> str:
        return self.id.split("_")[0]

    @property
    def useful_bytes(self) -> int:
        return sum(f[1] for f in self.files)


@dataclass
class Index:
    revision: str = ""
    last_modified: str = ""
    built: float = 0.0
    assets: dict[str, AssetRecord] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)
    complete: bool = False

    @classmethod
    def load(cls, path: Path) -> Index | None:
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if d.get("format") != INDEX_FORMAT:
            return None
        return cls(revision=d["revision"], last_modified=d.get("last_modified", ""), built=d.get("built", 0),
                   assets={k: AssetRecord(**{f: v[f] for f in _RECORD_FIELDS if f in v})
                           for k, v in d["assets"].items()},
                   failed=d.get("failed", {}), complete=d.get("complete", False))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        d = {"format": INDEX_FORMAT, "revision": self.revision, "last_modified": self.last_modified,
             "built": self.built, "complete": self.complete, "failed": self.failed,
             "assets": {k: asdict(v) for k, v in sorted(self.assets.items())}}
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(d, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)


_RECORD_FIELDS = set(AssetRecord.__dataclass_fields__)


class Source:
    """Where index files come from: Hugging Face, optionally a local mirror first
    (a folder laid out like the dataset, e.g. cache/raw, at the same revision)."""

    def __init__(self, hf: HFClient, rev: str, mirror: Path | None = None) -> None:
        self.hf, self.rev, self.mirror = hf, rev, mirror

    def get(self, asset: str, rel: str, sha256: str | None = None, max_bytes: int = 64 << 20) -> bytes:
        if self.mirror:
            p = self.mirror / asset / rel
            if p.is_file() and p.stat().st_size <= max_bytes:
                data = p.read_bytes()
                if sha256 is None or hashlib.sha256(data).hexdigest() == sha256:
                    return data
        return self.hf.get_bytes(self.rev, f"{asset}/{rel}", max_bytes=max_bytes, sha256=sha256)


def build_record(src: Source, asset: str, oid: str) -> AssetRecord:
    from . import structure  # predictor (pxr optional)

    items = json.loads(src.get(asset, BOM, max_bytes=32 << 20)).get("items")
    if not isinstance(items, list):
        raise ValueError("BOM without an item list")
    by_path = {}
    for i in items:
        if not isinstance(i, dict) or not isinstance(i.get("hash"), dict) or not isinstance(i.get("relative_path"), str):
            continue
        rel = i.get("relative_path", "")
        sha = (i.get("hash") or {}).get("sha256", "")
        if safe_relpath(rel) and isinstance(i.get("size"), int) and re.fullmatch(r"[0-9a-f]{64}", sha):
            by_path[rel] = [rel, i["size"], sha]
    # The root USD is the only .usd/.usda/.usdc directly in simready_usd/ (true for every
    # asset of the 2026-10 revision); read root_usds.json only when that is ambiguous.
    roots = [p for p in by_path if _TOP_USD.fullmatch(p)]
    if len(roots) != 1:
        roots = json.loads(src.get(asset, ROOT_USDS, max_bytes=1 << 20)).get("entries")
    root = roots[0] if isinstance(roots, list) and roots and isinstance(roots[0], str) else ""
    if not safe_relpath(root) or not root.lower().endswith((".usd", ".usda", ".usdc")):
        raise ValueError(f"bad root USD {root!r}")
    if root not in by_path:
        raise ValueError(f"root USD {root!r} not in the BOM")
    files = sorted(v for k, v in by_path.items() if wanted_for_conversion(k, root))
    thumb_rel = str(PurePosixPath(root).parent / ".thumbs/256x256" / (PurePosixPath(root).name + ".png"))
    rec = AssetRecord(id=asset, oid=oid, root=root, files=files, rev=src.rev, thumb=by_path.get(thumb_rel),
                      content_digest=hashlib.sha256(json.dumps([[f[0], f[2]] for f in files]).encode()).hexdigest())

    bom_items = [{"relative_path": v[0], "size": v[1], "hash": {"sha256": v[2]}} for v in by_path.values()]
    wanted = {rel: src.get(asset, rel, sha256=by_path[rel][2]) for rel in structure.wanted_files(asset, bom_items, root)
              if rel in by_path}
    pred = structure.predict(asset, wanted)
    rec.id_type, rec.name = pred["id_type"], pred["name"]
    rec.mesh_count, rec.confidence, rec.method = pred.get("mesh_count", 0), pred.get("confidence", "low"), pred.get("method", "")
    return rec


class IndexBuilder:
    """Incremental (re)build with progress reporting; safe to run in a thread."""

    def __init__(self, hf: HFClient, path: Path, mirror: Path | None = None, workers: int = 6,
                 on_progress=None, save_every: int = 100) -> None:
        self.hf, self.path, self.mirror, self.workers = hf, path, mirror, workers
        self.on_progress = on_progress or (lambda idx, done, total: None)
        self.save_every = save_every
        self.lock = threading.Lock()

    def refresh(self, current: Index | None, force: bool = False, revision: str | None = None) -> Index:
        rev = {"sha": revision, "lastModified": ""} if revision else self.hf.revision()
        if current and current.revision == rev["sha"] and current.complete and not force:
            return current
        log.info("index: revision %s (was %s)", rev["sha"][:12], current.revision[:12] if current else "-")
        top = self.hf.tree(rev["sha"])
        dirs = {e["path"]: e["oid"] for e in top if e.get("type") == "directory" and valid_asset_id(e["path"])}
        new = Index(revision=rev["sha"], last_modified=rev.get("lastModified") or "")
        old = current.assets if current else {}
        for a, oid in dirs.items():
            if a in old and old[a].oid == oid and not force:
                new.assets[a] = old[a]
        todo = sorted(a for a in dirs if a not in new.assets)
        log.info("index: %d assets, %d unchanged, %d to fetch", len(dirs), len(new.assets), len(todo))
        src = Source(self.hf, rev["sha"], self.mirror)
        done = 0
        self.on_progress(new, done, len(todo))
        with ThreadPoolExecutor(self.workers) as ex:
            futs = {ex.submit(build_record, src, a, dirs[a]): a for a in todo}
            for fut in as_completed(futs):
                a = futs[fut]
                try:
                    rec = fut.result()
                    with self.lock:
                        new.assets[a] = rec
                        new.failed.pop(a, None)
                except Exception as e:  # one bad asset must never stop the build
                    log.warning("index: %s failed: %s", a, e)
                    with self.lock:
                        new.failed[a] = f"{type(e).__name__}: {e}"[:300]
                        if a in old:  # keep serving the previous version (its files stay downloadable at rec.rev)
                            new.assets[a] = old[a]
                done += 1
                if done % self.save_every == 0:
                    new.built = time.time()
                    new.save(self.path)
                self.on_progress(new, done, len(todo))
        new.complete = not new.failed
        new.built = time.time()
        new.save(self.path)
        log.info("index: done, %d assets, %d failed, %d HF requests", len(new.assets), len(new.failed), self.hf.requests)
        return new
