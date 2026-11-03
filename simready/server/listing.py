# SPDX-License-Identifier: GPL-3.0-or-later
"""Remote asset library listing (Blender 5.2 API v1): meta -> index -> pages.

For every asset the .blend entry carries either the real hash/size (already
converted) or a deterministic placeholder (content digest + texture size +
converter version), so a new dataset revision, another texture size or a new
converter make Blender offer the update.
"""

from __future__ import annotations

import hashlib
import json
import re

from .. import common
from .catalogs import Catalogs, catalog_uuid
from .index import AssetRecord, Index
from .jobs import placeholder_hash
from .thumbs import listing_hash

LIBRARY_NAME = "NVIDIA SimReady"
PAGE_SIZE = 500


def _dumps(o) -> bytes:
    return json.dumps(o, indent=1, sort_keys=True, ensure_ascii=False).encode("utf-8")


def _sha(b: bytes) -> str:
    return "SHA256:" + hashlib.sha256(b).hexdigest()


def blend_relpath(rec: AssetRecord) -> str:
    return f"blend/{rec.prefix}/{rec.id}.blend"


def thumb_relpath(rec: AssetRecord) -> str:
    return f"thumbs/{rec.id}.webp"


COLOR_TEXTURE = re.compile(r"(alb|albedo|basecolor|base_color|diffuse|emissive|emission)[^/]*\.png$", re.I)


def estimate_size(rec: AssetRecord, max_res: int, color_jpeg: bool = False) -> int:
    """Rough size of the .blend before converting (only drives Blender's progress bar).
    Texture file size tells their resolution: big PNGs are 4K and shrink ~3x at 2K;
    color textures as JPEG are about 30% of the PNG."""
    total = 0
    for path, size, _ in rec.files:
        if "/textures/" in path.lower() and path.lower().endswith((".png", ".jpg", ".jpeg")):
            if color_jpeg and COLOR_TEXTURE.search(path) and size > 100_000:
                size = int(size * 0.3)
            if max_res and max_res <= 1024:
                factor = 1.0 if size < 1_500_000 else 0.3 if size < 6_000_000 else 0.1
            elif max_res and max_res <= 2048:
                factor = 1.0 if size < 6_000_000 else 0.35
            else:
                factor = 1.0
            total += int(size * factor)
        elif path.lower().endswith((".usd", ".usdc", ".usda")):
            total += size
    return max(total, 1)


def build(index: Index, catalog_paths: dict[str, str], converted, max_res: int,
          color_jpeg: bool = False) -> dict[str, bytes]:
    """URL path -> body. `converted(rec)` returns the state entry or None."""
    profile = f"{max_res}-jpeg" if color_jpeg else str(max_res)  # same as Settings.profile
    assets, files = [], []
    for rec in sorted(index.assets.values(), key=lambda r: r.id):
        st = converted(rec)
        id_type, name = (st["id_type"], st["name"]) if st else (rec.id_type, rec.name or rec.id)
        h = st["hash"] if st else placeholder_hash(rec, profile)
        size = st["size"] if st else estimate_size(rec, max_res, color_jpeg)
        entry = {
            "name": name, "id_type": id_type, "files": [blend_relpath(rec)],
            "bl_versions": {"min": common.BLENDER_VERSION},
            "meta": {"catalog_id": catalog_uuid(catalog_paths[rec.id]), "tags": common.asset_tags(rec.id),
                     "author": common.AUTHOR, "license": common.LICENSE, "copyright": common.COPYRIGHT,
                     "description": common.asset_description(rec.id)},
        }
        if rec.thumb:
            entry["thumbnail"] = {"url": thumb_relpath(rec), "hash": listing_hash(rec)}
        assets.append(entry)
        files.append({"path": blend_relpath(rec), "size_in_bytes": size, "hash": "SHA256:" + h,
                      "blender_version": common.BLENDER_VERSION})

    out: dict[str, bytes] = {}
    pages = []
    for n, start in enumerate(range(0, max(len(assets), 1), PAGE_SIZE)):
        a, f = assets[start:start + PAGE_SIZE], files[start:start + PAGE_SIZE]
        body = _dumps({"asset_count": len(a), "file_count": len(f), "assets": a, "files": f})
        url = f"_v1/assets-{n:05d}.json"
        out["/" + url] = body
        pages.append({"url": url, "hash": _sha(body)})
    idx = _dumps({"schema_version": "1.0.0", "asset_size_bytes": sum(f["size_in_bytes"] for f in files),
                  "asset_count": len(assets), "file_count": len(files), "pages": pages,
                  "catalogs": Catalogs.listing_entries(catalog_paths[r.id] for r in index.assets.values())})
    out["/_v1/asset-index.json"] = idx
    out["/_asset-library-meta.json"] = _dumps({
        "name": LIBRARY_NAME,
        "contact": {"name": "SimReady to Blender (assets: NVIDIA, CC-BY-4.0)",
                    "url": "https://huggingface.co/datasets/nvidia/simready-assets"},
        "api_versions": {"v1": {"url": "_v1/asset-index.json", "hash": _sha(idx)}},
    })
    return out
