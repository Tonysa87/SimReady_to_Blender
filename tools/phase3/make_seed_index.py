# SPDX-License-Identifier: GPL-3.0-or-later
"""Write the seed index shipped in the repo from a server's complete index.

python3 tools/phase3/make_seed_index.py <state-dir>/index/index.json

The output (simready/server/data/seed_index.json.gz) is deterministic: same
index -> same bytes, so git only changes when the dataset changes.
Content: file lists, sizes and sha256 of the NVIDIA dataset (CC-BY-4.0).
"""

import gzip
import io
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "simready/server/data/seed_index.json.gz"


def main() -> None:
    d = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if not d.get("complete") or d.get("failed"):
        sys.exit("index is not complete: refusing to ship it")
    d["built"] = 0  # no timestamps: stable bytes
    body = json.dumps(d, separators=(",", ":"), sort_keys=True).encode("utf-8")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9, mtime=0) as gz:
        gz.write(body)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(buf.getvalue())
    print(f"{OUT}: {len(d['assets'])} assets, revision {d['revision'][:12]}, {OUT.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
