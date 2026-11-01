# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate simready/server/structure.py (type/name prediction) against the real converter.

Run with Blender's bundled Python (it has pxr), from the repository root:

  PY=~/blender/blender-5.2.2-linux-x64/5.2/python/bin/python3.13
  $PY -I tools/phase3/validate_structure.py fetch-text  [--assets a,b | --all]
  $PY -I tools/phase3/validate_structure.py predict     --out PRED.jsonl [--all]
  $PY -I tools/phase3/validate_structure.py sample      --n 130 --out SAMPLE.txt [--seed 1]
  $PY -I tools/phase3/validate_structure.py fetch-payloads --list SAMPLE.txt [--max-mb 300]
  $PY -I tools/phase3/validate_structure.py convert     --list SAMPLE.txt --scratch DIR [--jobs 3]
  $PY -I tools/phase3/validate_structure.py compare     --list SAMPLE.txt --scratch DIR

Downloads go to cache/raw/<asset>/<relpath> (mirror of the dataset at REV),
verified against the BOM sha256, paced well below the Hugging Face "resolvers"
limit (3000 requests / 300 s). Existing files are never deleted. Downloaded
data is untrusted: nothing from it is executed; the converter only reads it.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import random
import re
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from simready.server import structure  # noqa: E402

REV = "4763fbfaa4f8e97658e68b57d01b4c86659e08b5"
RESOLVE = f"https://huggingface.co/datasets/nvidia/simready-assets/resolve/{REV}/"
RAW = REPO_ROOT / "cache/raw"
BLENDER = Path.home() / "blender/blender-5.2.2-linux-x64/blender"
CONVERTER = REPO_ROOT / "simready/convert/convert_asset.py"
ROOT_USDS = ".metadata/com.nvidia.simready.root_usds.json"
BOM = ".metadata/com.nvidia.simready.packaging.bom.json"
_ASSET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


# ------------------------------------------------------------------ local data


def all_assets() -> list[str]:
    return sorted(p.parent.parent.name for p in RAW.glob(f"*/{ROOT_USDS}") if _ASSET_RE.match(p.parent.parent.name))


def meta(asset: str) -> tuple[str, list[dict]]:
    root = json.loads((RAW / asset / ROOT_USDS).read_text())["entries"][0]
    items = json.loads((RAW / asset / BOM).read_text())["items"]
    return root, items


def bom_map(items: list[dict]) -> dict[str, dict]:
    return {i["relative_path"]: i for i in items if structure._safe_rel(i.get("relative_path", ""))}


def local_files(asset: str, rels: list[str]) -> dict[str, bytes]:
    out = {}
    for rel in rels:
        p = RAW / asset / rel
        if p.is_file():
            out[rel] = p.read_bytes()
    return out


def is_layout_a(items: list[dict]) -> bool:
    return any("/runnables/physics/" in "/" + i["relative_path"] for i in items)


def payload_dir(root: str) -> str:
    return structure._payload_dir(root)


# ------------------------------------------------------------------- download


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    try:  # Blender's Python ships certifi but no system CA path
        import certifi  # type: ignore
        ctx.load_verify_locations(certifi.where())
    except ImportError:
        pass
    return ctx


class Pacer:
    """Global pacing: at most `rate` requests/s, pause when the RateLimit header runs low."""

    def __init__(self, rate: float, reserve: int = 300) -> None:
        self.interval = 1.0 / rate
        self.reserve = reserve
        self.lock = threading.Lock()
        self.next_at = 0.0
        self.requests = 0

    def wait(self) -> None:
        with self.lock:
            now = time.time()
            at = max(now, self.next_at)
            self.next_at = at + self.interval
            self.requests += 1
        if at > now:
            time.sleep(at - now)

    def feed(self, header: str | None) -> None:
        m = re.search(r'"?resolvers"?\s*;\s*r=(\d+)\s*;\s*t=(\d+)', header or "")
        if m and int(m.group(1)) < self.reserve:
            with self.lock:
                self.next_at = max(self.next_at, time.time() + int(m.group(2)) + 2)
            print(f"  rate limit low ({m.group(1)} left): pausing {m.group(2)} s", flush=True)

    def block(self, seconds: float) -> None:
        with self.lock:
            self.next_at = max(self.next_at, time.time() + seconds)


class _Redirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, pacer: Pacer) -> None:
        self.pacer = pacer

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.pacer.feed(headers.get("RateLimit"))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Downloader:
    def __init__(self, rate: float = 4.0) -> None:
        self.pacer = Pacer(rate)
        self.opener = urllib.request.build_opener(_Redirect(self.pacer), urllib.request.HTTPSHandler(context=_ssl_context()))
        self.bytes = 0
        self.lock = threading.Lock()

    def get(self, asset: str, rel: str, size: int, sha256: str) -> str:
        dst = RAW / asset / rel
        if not (structure._safe_rel(rel) and _ASSET_RE.match(asset)) or not dst.resolve().is_relative_to(RAW.resolve()):
            return "unsafe"
        if dst.is_file() and dst.stat().st_size == size:
            if hashlib.sha256(dst.read_bytes()).hexdigest() == sha256:
                return "cached"
        url = RESOLVE + urllib.parse.quote(f"{asset}/{rel}")
        for attempt in range(6):
            self.pacer.wait()
            try:
                with self.opener.open(urllib.request.Request(url, headers={"User-Agent": "simready-validate"}), timeout=120) as r:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    part = dst.with_name(dst.name + ".part")
                    h, n = hashlib.sha256(), 0
                    with part.open("wb") as f:
                        while chunk := r.read(1 << 20):
                            f.write(chunk)
                            h.update(chunk)
                            n += len(chunk)
                if n != size or h.hexdigest() != sha256:
                    part.unlink(missing_ok=True)
                    time.sleep(2)
                    continue
                part.replace(dst)
                with self.lock:
                    self.bytes += n
                return "ok"
            except urllib.error.HTTPError as e:
                self.pacer.feed(e.headers.get("RateLimit") if e.headers else None)
                if e.code == 429:
                    ra = e.headers.get("Retry-After") if e.headers else None
                    self.pacer.block(float(ra) if ra and ra.isdigit() else 60 * (attempt + 1))
                    continue
                if e.code >= 500:
                    time.sleep(2 ** attempt)
                    continue
                return f"HTTP{e.code}"
            except (urllib.error.URLError, OSError, TimeoutError):
                time.sleep(2 ** attempt)
        return "fail"

    def run(self, jobs: list[tuple[str, str, int, str]], workers: int = 4) -> collections.Counter:
        t0, res = time.time(), collections.Counter()
        bad = []
        with ThreadPoolExecutor(min(workers, 6)) as ex:
            for i, (job, st) in enumerate(zip(jobs, ex.map(lambda j: self.get(*j), jobs)), 1):
                res[st] += 1
                if st not in ("ok", "cached"):
                    bad.append((job[0], job[1], st))
                if i % 200 == 0 or i == len(jobs):
                    print(f"  {i}/{len(jobs)} {dict(res)} {self.bytes / 1e6:.1f} MB "
                          f"{self.pacer.requests} req {time.time() - t0:.0f} s", flush=True)
        for b in bad[:30]:
            print("  FAILED", *b)
        return res


# ------------------------------------------------------------------- commands


def parse_assets(a) -> list[str]:
    if getattr(a, "list", None):
        return [l.split()[0] for l in Path(a.list).read_text().splitlines() if l.strip() and not l.startswith("#")]
    if getattr(a, "assets", None):
        return [x for x in a.assets.split(",") if x]
    return all_assets()


def cmd_fetch_text(a) -> None:
    jobs = []
    for asset in parse_assets(a):
        root, items = meta(asset)
        bm = bom_map(items)
        rels = structure.wanted_files(asset, items, root, max_bytes=a.max_bytes)
        if a.extra:   # survey: physics sublayer and the "Disabled" variant payloads
            pdir = payload_dir(root) + "/"
            rels += [r for r, i in bm.items() if r.startswith(pdir) and i["size"] <= a.max_bytes and r not in rels
                     and (r.endswith("/Physics/physics.usda") or r.endswith("/disabled.usda"))]
        jobs += [(asset, r, bm[r]["size"], bm[r]["hash"]["sha256"]) for r in rels if r in bm]
    print(f"fetch-text: {len(jobs)} files")
    Downloader(a.rate).run(jobs, a.workers)


def predict_one(asset: str, max_bytes: int = 2_000_000) -> dict:
    root, items = meta(asset)
    rels = structure.wanted_files(asset, items, root, max_bytes=max_bytes)
    files = local_files(asset, rels)
    t = time.time()
    p = structure.predict(asset, files)
    p["asset"] = asset
    p["seconds"] = round(time.time() - t, 3)
    p["files"] = sorted(files)
    p["bytes"] = sum(len(v) for v in files.values())
    p["missing_files"] = sorted(set(rels) - set(files))
    return p


def cmd_predict(a) -> None:
    out = Path(a.out)
    n, by = 0, collections.Counter()
    with out.open("w") as f:
        for asset in parse_assets(a):
            p = predict_one(asset)
            f.write(json.dumps(p) + "\n")
            n += 1
            by[(p["id_type"], p["confidence"], p["method"].split(";")[0][:40])] += 1
    print(f"predicted {n} -> {out}")
    for k, v in by.most_common():
        print(f"  {v:5d} {k}")


def prefix(asset: str) -> str:
    return asset.split("_")[0]


def payload_bytes(root: str, items: list[dict], skip_textures: bool = True) -> int:
    pdir = payload_dir(root) + "/"
    return sum(i["size"] for i in items if i["relative_path"] == root or i["relative_path"].startswith(pdir))


def cmd_sample(a) -> None:
    """Stratified sample: every prefix, single/multi part, layout A, big instances.usda."""
    rng = random.Random(a.seed)
    preds = {json.loads(l)["asset"]: json.loads(l) for l in Path(a.pred).read_text().splitlines()}
    info = {}
    for asset, p in preds.items():
        root, items = meta(asset)
        bm = bom_map(items)
        inst = bm.get(payload_dir(root) + "/instances.usda", {}).get("size", 0)
        info[asset] = dict(pred=p, size=payload_bytes(root, items), layoutA=is_layout_a(items), big=inst > 2_000_000,
                           nobase=(payload_dir(root) + "/base.usda") not in bm)
    chosen: list[str] = []

    def take(cands, k, budget):
        cands = [c for c in cands if c not in chosen and info[c]["size"] <= budget]
        rng.shuffle(cands)
        cands.sort(key=lambda c: info[c]["size"] > budget / 4)   # prefer the small ones
        chosen.extend(cands[:k])

    take([c for c in info if info[c]["layoutA"] and not info[c]["nobase"]], 12, 60e6)
    take([c for c in info if info[c]["big"]], 3, 300e6)
    take([c for c in info if info[c]["nobase"]], 1, 120e6)
    take([c for c in info if info[c]["pred"]["confidence"] == "low"], 5, 120e6)
    take([c for c in info if (info[c]["pred"].get("notes"))], 6, 120e6)
    by_prefix = collections.defaultdict(list)
    for c in info:
        by_prefix[prefix(c)].append(c)
    for pre in sorted(by_prefix):
        take(by_prefix[pre], 1, 60e6)
    singles = [c for c in info if info[c]["pred"]["id_type"] == "OBJECT"]
    multis = [c for c in info if info[c]["pred"]["id_type"] == "COLLECTION"]
    while len(chosen) < a.n:
        take(singles, 1, 40e6)
        take(multis, 1, 40e6)
    total = sum(info[c]["size"] for c in chosen)
    lines = [f"{c} {info[c]['pred']['id_type']} {info[c]['pred']['mesh_count']} {info[c]['size'] / 1e6:.1f}MB"
             f"{' layoutA' if info[c]['layoutA'] else ''}{' bigInstances' if info[c]['big'] else ''}"
             f"{' noBase' if info[c]['nobase'] else ''}" for c in chosen]
    Path(a.out).write_text("\n".join(lines) + "\n")
    print(f"sample: {len(chosen)} assets, {len(by_prefix)} prefixes, payload download <= {total / 1e6:.0f} MB -> {a.out}")


def cmd_fetch_payloads(a) -> None:
    """Root USD + payloads/** (no Textures, thumbnails, source_assets, runnables)."""
    jobs, skipped = [], []
    for asset in parse_assets(a):
        root, items = meta(asset)
        pdir = payload_dir(root) + "/"
        sel = [i for i in items if i["relative_path"] == root or i["relative_path"].startswith(pdir)]
        if sum(i["size"] for i in sel) > a.max_mb * 1e6:
            skipped.append(asset)
            continue
        jobs += [(asset, i["relative_path"], i["size"], i["hash"]["sha256"]) for i in sel]
    print(f"fetch-payloads: {len(jobs)} files, {sum(j[2] for j in jobs) / 1e6:.0f} MB (before cache); skipped {skipped}")
    Downloader(a.rate).run(jobs, a.workers)


def convert_one(asset: str, scratch: Path, timeout: int) -> tuple[str, str]:
    rep = scratch / f"{asset}.json"
    if rep.is_file():
        return asset, "cached"
    root, items = meta(asset)
    if not (RAW / asset / root).is_file():
        return asset, "no-root"
    cmd = [str(BLENDER), "-b", "--factory-startup", "--python", str(CONVERTER), "--",
           "--asset-dir", str(RAW / asset), "--out", str(scratch / f"{asset}.blend"),
           "--report", str(rep), "--max-res", "256"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(scratch))
    except subprocess.TimeoutExpired:
        return asset, "timeout"
    (scratch / f"{asset}.log").write_text(r.stdout[-20000:] + "\n--- stderr\n" + r.stderr[-20000:])
    (scratch / f"{asset}.blend").unlink(missing_ok=True)       # only the report is needed
    return asset, "ok" if "SIMREADY_CONVERT_OK" in r.stdout else "failed"


def cmd_convert(a) -> None:
    scratch = Path(a.scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    assets = parse_assets(a)
    res = collections.Counter()
    with ThreadPoolExecutor(a.jobs) as ex:
        for i, (asset, st) in enumerate(ex.map(lambda x: convert_one(x, scratch, a.timeout), assets), 1):
            res[st] += 1
            print(f"  [{i}/{len(assets)}] {asset}: {st}", flush=True)
    print(dict(res))


def cmd_compare(a) -> None:
    scratch = Path(a.scratch)
    rows, mism = [], []
    stats = collections.Counter()
    for asset in parse_assets(a):
        rep = scratch / f"{asset}.json"
        if not rep.is_file():
            stats["no_report"] += 1
            continue
        r = json.loads(rep.read_text())
        p = predict_one(asset, a.max_bytes)
        if a.full_text:   # same prediction with every local text layer (root, physics, variants)?
            root, items = meta(asset)
            pdir = payload_dir(root) + "/"
            rels = [root] + [i["relative_path"] for i in items if i["relative_path"].startswith(pdir)
                             and i["relative_path"].endswith(".usda") and i["size"] <= a.max_bytes]
            q = structure.predict(asset, local_files(asset, rels))
            if (q["id_type"], q["name"], q["mesh_count"]) != (p["id_type"], p["name"], p["mesh_count"]):
                stats["full_text_diff"] += 1
                print(f"FULL-TEXT DIFF {asset}: lean {p['name']} {p['mesh_count']} / full {q['name']} {q['mesh_count']}")
            stats["full_text_checked"] += 1
        ok_t = p["id_type"] == r["asset_type"]
        ok_n = p["name"] == r["asset_name"]
        ok_c = p["mesh_count"] == r["mesh_count"]
        stats["n"] += 1
        stats["type"] += ok_t
        stats["name"] += ok_n
        stats["count"] += ok_c
        stats["all"] += ok_t and ok_n and ok_c
        real_set = sorted(re.sub(r"\.\d{3}$", "", k) for k in r.get("dimensions_m", {}))
        ok_set = sorted(p.get("meshes", [])) == real_set
        stats["mesh_names"] += ok_set
        if ok_t and ok_n and ok_c and not ok_set:
            print(f"MESH-NAMES {asset}: pred {sorted(p.get('meshes', []))[:6]} real {real_set[:6]}")
        stats[f"conf_{p['confidence']}"] += 1
        stats[f"conf_{p['confidence']}_ok"] += ok_t and ok_n
        stats["bytes"] += p["bytes"]
        stats["files"] += len(p["files"])
        rows.append({"asset": asset, "pred": [p["id_type"], p["name"], p["mesh_count"], p["confidence"], p["method"]],
                     "real": [r["asset_type"], r["asset_name"], r["mesh_count"]]})
        if not (ok_t and ok_n and ok_c):
            real_names = sorted(r.get("dimensions_m", {}))
            mism.append(f"{asset}: pred {p['id_type']} {p['name']} {p['mesh_count']} ({p['confidence']}, {p['method']})"
                        f" | real {r['asset_type']} {r['asset_name']} {r['mesh_count']}"
                        f"\n      pred meshes {sorted(p.get('meshes', []))[:8]}\n      real meshes {real_names[:8]}")
    n = stats["n"] or 1
    print(f"compared {stats['n']} (missing reports: {stats['no_report']})")
    for k in ("type", "name", "count", "all", "mesh_names"):
        print(f"  {k:6s} {stats[k]}/{stats['n']} = {100 * stats[k] / n:.1f}%")
    for c in ("high", "low"):
        if stats[f"conf_{c}"]:
            print(f"  confidence {c}: {stats[f'conf_{c}']} assets, type+name correct {stats[f'conf_{c}_ok']}")
    print(f"  prediction input: {stats['files'] / n:.2f} files, {stats['bytes'] / n / 1e3:.1f} KB per asset")
    if a.full_text:
        print(f"  full-text check: {stats['full_text_checked']} assets, {stats['full_text_diff']} differ from the lean prediction")
    for m in mism:
        print("MISMATCH", m)
    if a.json:
        Path(a.json).write_text(json.dumps(rows, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, lists=True):
        if lists:
            p.add_argument("--assets", help="comma separated asset ids (default: all)")
            p.add_argument("--list", help="file with one asset id per line (first column)")
        return p

    p = common(sub.add_parser("fetch-text"))
    p.add_argument("--max-bytes", type=int, default=2_000_000)
    p.add_argument("--extra", action="store_true", help="also payloads/Physics/physics.usda and */disabled.usda (survey)")
    p.add_argument("--rate", type=float, default=4.0)
    p.add_argument("--workers", type=int, default=4)
    p.set_defaults(fn=cmd_fetch_text)

    p = common(sub.add_parser("predict"))
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_predict)

    p = sub.add_parser("sample")
    p.add_argument("--pred", required=True, help="predictions JSONL from `predict`")
    p.add_argument("--n", type=int, default=130)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_sample)

    p = common(sub.add_parser("fetch-payloads"))
    p.add_argument("--max-mb", type=float, default=300)
    p.add_argument("--rate", type=float, default=4.0)
    p.add_argument("--workers", type=int, default=4)
    p.set_defaults(fn=cmd_fetch_payloads)

    p = common(sub.add_parser("convert"))
    p.add_argument("--scratch", required=True)
    p.add_argument("--jobs", type=int, default=3)
    p.add_argument("--timeout", type=int, default=900)
    p.set_defaults(fn=cmd_convert)

    p = common(sub.add_parser("compare"))
    p.add_argument("--scratch", required=True)
    p.add_argument("--max-bytes", type=int, default=2_000_000)
    p.add_argument("--json", help="write per-asset rows here")
    p.add_argument("--full-text", action="store_true",
                   help="also predict with root + every local payloads/*.usda and report differences")
    p.set_defaults(fn=cmd_compare)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
