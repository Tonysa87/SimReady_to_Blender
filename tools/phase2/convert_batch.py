# SPDX-License-Identifier: GPL-3.0-or-later
"""Convert the phase-2 test assets at Max / 2K / 1K and summarise the reports.

python3 tools/phase2/convert_batch.py [--res 0,2048,1024] [asset ...]
"""

import argparse
import json
import subprocess
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BLENDER = Path.home() / "blender/blender-5.2.2-linux-x64/blender"
ASSETS = ["food_banana_a01", "kitchenware_bowl_b01", "ppe_eyeProtection_goggles_a01",
          "fixture_cabinet_a01", "rack_composition_a04", "warehouse_conveyorBelt_a01"]
CATALOG_NS = uuid.UUID("2b9c1f3e-6a57-4f0e-9c1d-5e1d2e9a5172")  # stable namespace for SimReady catalogs
RES_NAME = {0: "max", 2048: "2k", 1024: "1k"}


def catalog_for(asset: str) -> tuple[str, str]:
    prefix = asset.split("_")[0]
    path = f"SimReady/{prefix[0].upper()}{prefix[1:]}"
    return str(uuid.uuid5(CATALOG_NS, path)), path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--res", default="0,2048,1024")
    ap.add_argument("assets", nargs="*", default=ASSETS)
    args = ap.parse_args()
    rows = []
    for res in [int(r) for r in args.res.split(",")]:
        for a in args.assets:
            out = ROOT / f"out/blend/{RES_NAME[res]}/{a}.blend"
            rep = out.with_suffix(".json")
            cat_id, cat_path = catalog_for(a)
            t = time.time()
            p = subprocess.run([str(BLENDER), "-b", "--factory-startup", "--python",
                                str(ROOT / "simready/convert/convert_asset.py"), "--",
                                "--asset-dir", str(ROOT / "cache/raw" / a), "--out", str(out),
                                "--max-res", str(res), "--catalog-id", cat_id,
                                "--tags", f"{a.split('_')[0]},simready", "--report", str(rep)],
                               capture_output=True, text=True)
            wall = time.time() - t
            ok = "SIMREADY_CONVERT_OK" in p.stdout
            if not ok:
                print(f"FAILED {a} res={res}\n{p.stdout[-2000:]}\n{p.stderr[-2000:]}")
                continue
            d = json.loads(rep.read_text())
            notes = [n for h in d["materials"]["hook"] for n in h["notes"]]
            rows.append((RES_NAME[res], a, d["asset_type"], d["mesh_count"], len(d["materials"]["hook"]),
                         len([m for m in d["materials"]["native"] if "physics_mat" not in m]),
                         d["out_bytes"] / 1e6, wall, len(d["warnings"]), notes))
    print(f"{'res':4} {'asset':32} {'type':10} {'mesh':>4} {'hook':>4} {'nat':>3} {'MB':>7} {'sec':>5} warn notes")
    for r in rows:
        print(f"{r[0]:4} {r[1]:32} {r[2]:10} {r[3]:4} {r[4]:4} {r[5]:3} {r[6]:7.1f} {r[7]:5.1f} {r[8]:4} {sorted(set(r[9]))}")


if __name__ == "__main__":
    main()
