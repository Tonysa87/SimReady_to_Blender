# SPDX-License-Identifier: GPL-3.0-or-later
"""Experiment 1a: keep-alive strategies vs Blender's downloader, headless, short timeout.

    python3 -P tools/phase3/run_headless.py --work /tmp/claude-1000/phase3/headless [--port 8791] [--case NAME ...]

Starts longconv_server.py, then for every case: reconfigures the server, forces a
fresh conversion of fake_cube_01 (simulated delay 20 s), downloads it with
headless_download.py (Blender's ConditionalDownloader, read timeout 5 s) and
compares the sha256 with the server's file.
"""

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BLENDER = str(Path.home() / "blender/blender-5.2.2-linux-x64/blender")
REL = "blend/fake/fake_cube_01.blend"

# name: (server cfg, timeout, retries)
CASES = {
    "gzip_ka2s":         ({"keepalive": "gzip", "interval": 2, "pad": 0, "fail": []}, 5, "default"),
    "gzip_ka2s_pad8k":   ({"keepalive": "gzip", "interval": 2, "pad": 8195, "fail": []}, 5, "default"),
    "none_noretry":      ({"keepalive": "none", "fail": []}, 5, "0"),
    "none_blender_retry": ({"keepalive": "none", "fail": []}, 5, "default"),
    "continue_ka2s":     ({"keepalive": "continue", "interval": 2, "fail": []}, 5, "default"),
    "gzip_fail_abort":   ({"keepalive": "gzip", "interval": 2, "pad": 0, "fail": ["fake_cube_01"],
                           "fail_style": "abort"}, 5, "default"),
    "gzip_fail_finish":  ({"keepalive": "gzip", "interval": 2, "pad": 0, "fail": ["fake_cube_01"],
                           "fail_style": "finish"}, 5, "default"),
    "continue_fail":     ({"keepalive": "continue", "interval": 2, "fail": ["fake_cube_01"]}, 5, "default"),
}


def post(base, path, obj=None):
    data = json.dumps(obj or {}).encode()
    with urllib.request.urlopen(urllib.request.Request(base + path, data=data, method="POST")) as r:
        return r.read()


def get_json(base, path):
    with urllib.request.urlopen(base + path) as r:
        return json.load(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--case", action="append")
    args = ap.parse_args()
    base = f"http://127.0.0.1:{args.port}/"
    args.work.mkdir(parents=True, exist_ok=True)
    srv = subprocess.Popen([sys.executable, "-P", str(HERE / "longconv_server.py"), "--state-dir",
                            str(args.work / "server"), "--port", str(args.port), "--convert-delay", "20"],
                           stdout=(args.work / "server.out").open("w"), stderr=subprocess.STDOUT)
    try:
        time.sleep(1.5)
        summary = {}
        for name in args.case or CASES:
            cfg, timeout, retries = CASES[name]
            post(base, "_admin/config", cfg)
            post(base, "_admin/reconvert/fake_cube_01")
            page = get_json(base, "_v1/assets-00000.json")
            h = next(f["hash"] for f in page["files"] if f["path"] == REL)
            url = f"{base}{REL}?hash={h.replace(':', '%3A')}"
            dest = args.work / name / "lib" / REL
            res_path = args.work / name / "result.json"
            print(f"== {name}: cfg={cfg} timeout={timeout} retries={retries}", flush=True)
            (args.work / name).mkdir(parents=True, exist_ok=True)
            with (args.work / name / "blender.out").open("w") as out:
                subprocess.run([BLENDER, "-b", "--factory-startup", "--python", str(HERE / "headless_download.py"),
                                "--", url, str(dest), str(timeout), retries, str(res_path)],
                               stdout=out, stderr=subprocess.STDOUT)
            res = json.loads(res_path.read_text())
            time.sleep(4)  # let a still-running conversion finish
            st = get_json(base, "_status")["state"]["fake_cube_01"]
            res["server_sha256"] = st.get("real_hash")
            res["identical"] = res.get("sha256") == st.get("real_hash") and res.get("sha256") is not None
            summary[name] = res
            print(json.dumps({k: res.get(k) for k in ("ok", "seconds", "identical", "dest_exists", "leftover_parts",
                                                         "n_periodic_checks")}), flush=True)
            for e in res["events"]:
                print("   ", json.dumps(e)[:400], flush=True)
        (args.work / "summary.json").write_text(json.dumps(summary, indent=1))
    finally:
        srv.terminate()
        srv.wait(5)
    print("server log:", args.work / "server" / "requests.log")


if __name__ == "__main__":
    main()
