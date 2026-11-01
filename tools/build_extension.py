# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the Blender extension .zip: addon/ + the simready package.

python3 tools/build_extension.py [--blender PATH] [--out dist]

Layout inside the zip:
  blender_manifest.toml, __init__.py, runtime.py, library.py, importer.py, cache_link.py
  simready/   (common, convert/, server/ with catalogs.json and data/seed_index.json.gz)
  LICENSE-ASSETS.txt
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", "*.blend1")
ASSET_NOTICE = """NVIDIA SimReady assets
=======================

The 3D assets shown by this add-on are NOT included: they are downloaded from
https://huggingface.co/datasets/nvidia/simready-assets on the user's computer.

Assets © NVIDIA Corporation, licensed under CC-BY-4.0
(https://creativecommons.org/licenses/by/4.0/). Trademarks and logos visible on
the assets are not covered by the license.

simready/server/data/seed_index.json.gz contains only metadata derived from that
dataset (folder names, file sizes, SHA-256 digests), same license and attribution.
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--blender", default=str(Path.home() / "blender/blender-5.2.2-linux-x64/blender"))
    ap.add_argument("--out", type=Path, default=ROOT / "dist")
    a = ap.parse_args()
    with tempfile.TemporaryDirectory(prefix="simready_ext_") as tmp:
        src = Path(tmp) / "simready_library"
        shutil.copytree(ROOT / "addon", src, ignore=SKIP)
        shutil.copytree(ROOT / "simready", src / "simready", ignore=SKIP)
        (src / "LICENSE-ASSETS.txt").write_text(ASSET_NOTICE, encoding="utf-8")
        a.out.mkdir(parents=True, exist_ok=True)
        for cmd in (["validate", str(src)], ["build", "--source-dir", str(src), "--output-dir", str(a.out)]):
            p = subprocess.run([a.blender, "--factory-startup", "--command", "extension", *cmd],
                               capture_output=True, text=True)
            print(p.stdout.strip())
            if p.returncode != 0:
                print(p.stderr, file=sys.stderr)
                sys.exit(p.returncode)


if __name__ == "__main__":
    main()
