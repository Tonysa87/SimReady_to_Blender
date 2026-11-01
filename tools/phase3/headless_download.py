# SPDX-License-Identifier: GPL-3.0-or-later
"""Download one URL with Blender's own ConditionalDownloader (same class the asset
downloader runs in its background process), with a configurable read timeout.

blender -b --factory-startup --python headless_download.py -- \
    <url> <dest_file> <timeout_s> <retries: default|0> <result.json>
"""

import json
import sys
import time
import traceback
from pathlib import Path

import requests
import requests.adapters

from _bpy_internal.http import downloader as http_dl

argv = sys.argv[sys.argv.index("--") + 1:]
url, dest, timeout, retries, result_path = argv[0], Path(argv[1]), float(argv[2]), argv[3], Path(argv[4])
t0 = time.time()
events = []


def ev(kind, **kw):
    events.append({"t": round(time.time() - t0, 2), "ev": kind, **kw})


class Reporter:
    def download_starts(self, r):
        ev("starts")

    def already_downloaded(self, r, p):
        ev("already_downloaded")

    def download_error(self, r, p, e):
        ev("error", type=type(e).__name__, msg=str(e)[:300])

    def download_progress(self, r, prog):
        ev("progress", net=prog.network_bytes_streamed, disk=prog.disk_bytes_written)

    def download_finished(self, r, p):
        ev("finished", headers=r.response_headers)


checks = []
dest.parent.mkdir(parents=True, exist_ok=True)
dl = http_dl.ConditionalDownloader(http_dl.MetadataProviderFilesystem(dest.parent / "_http_meta"))
if retries == "0":
    s = requests.session()
    s.mount("http://", requests.adapters.HTTPAdapter(max_retries=0))
    dl.http_session = s
dl.http_session.headers.update({"X-Blender": "5.2", "user-agent": "phase3-headless"})
dl.timeout = timeout
dl.add_reporter(Reporter())


def periodic(_r):
    checks.append(round(time.time() - t0, 2))
    return True


dl.periodic_check = periodic
ok = True
try:
    dl.download_to_file(url, dest)
except Exception as ex:
    ok = False
    ev("exception", type=type(ex).__name__, msg=str(ex)[:300], tb=traceback.format_exc()[-800:])

progress = [e for e in events if e["ev"] == "progress"]
res = {
    "ok": ok, "seconds": round(time.time() - t0, 2), "dest_exists": dest.exists(),
    "leftover_parts": sorted(p.name for p in dest.parent.glob("*.part")),
    "events": [e for e in events if e["ev"] != "progress"],
    "n_progress": len(progress), "first_progress": progress[:2], "last_progress": progress[-1:],
    "periodic_check_times": checks[:40], "n_periodic_checks": len(checks),
}
if dest.exists():
    import hashlib
    res["sha256"] = hashlib.sha256(dest.read_bytes()).hexdigest()
    res["size"] = dest.stat().st_size
result_path.write_text(json.dumps(res, indent=1, default=str))
print("HEADLESS_RESULT", json.dumps({k: res[k] for k in ("ok", "seconds", "dest_exists")}), flush=True)
