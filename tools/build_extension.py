# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the Blender extension .zip: addon/ + the simready package.

python3 tools/build_extension.py [--blender PATH] [--out dist] [--no-seed-index]

--no-seed-index: zip for extensions.blender.org, whose rules want every bundled data file
under CC0; the seed index is derived from the CC-BY-4.0 dataset, so it is left out and the
server builds the index from Hugging Face at the first start (~13 minutes, the library
fills up while it runs). Output: simready_library-<version>-no-seed.zip.

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
import tomllib
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
NO_SEED_NOTE = """
(This build does not include simready/server/data/seed_index.json.gz: the index of the
assets is built from Hugging Face the first time the server starts.)
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--blender", default=str(Path.home() / "blender/blender-5.2.2-linux-x64/blender"))
    ap.add_argument("--out", type=Path, default=ROOT / "dist")
    ap.add_argument("--no-seed-index", action="store_true", help="leave out the CC-BY seed index")
    a = ap.parse_args()
    with tempfile.TemporaryDirectory(prefix="simready_ext_") as tmp:
        src = Path(tmp) / "simready_library"
        shutil.copytree(ROOT / "addon", src, ignore=SKIP)
        shutil.copytree(ROOT / "simready", src / "simready", ignore=SKIP)
        notice = ASSET_NOTICE
        if a.no_seed_index:
            (src / "simready/server/data/seed_index.json.gz").unlink()
            notice = ASSET_NOTICE.split("simready/server/data/seed_index")[0].rstrip() + "\n" + NO_SEED_NOTE
        (src / "LICENSE-ASSETS.txt").write_text(notice, encoding="utf-8")
        shutil.copy2(ROOT / "LICENSE", src / "LICENSE")
        a.out.mkdir(parents=True, exist_ok=True)
        build = ["build", "--source-dir", str(src)]
        if a.no_seed_index:
            version = tomllib.loads((src / "blender_manifest.toml").read_text(encoding="utf-8"))["version"]
            build += ["--output-filepath", str(a.out / f"simready_library-{version}-no-seed.zip")]
        else:
            build += ["--output-dir", str(a.out)]
        for cmd in (["validate", str(src)], build):
            p = subprocess.run([a.blender, "--factory-startup", "--command", "extension", *cmd],
                               capture_output=True, text=True)
            print(p.stdout.strip())
            if p.returncode != 0:
                print(p.stderr, file=sys.stderr)
                sys.exit(p.returncode)


if __name__ == "__main__":
    main()
