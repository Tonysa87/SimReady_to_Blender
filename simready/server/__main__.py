# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the SimReady remote asset library server.

Meant for Blender's bundled Python (it has OpenImageIO and pxr):

  <blender>/5.2/python/bin/python3.13 -m simready.server --state-dir DIR [--port 8765]
      [--max-res 2048] [--blender PATH] [--mirror cache/raw] [--parent-pid PID]
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import signal
import sys
import threading
import time
from pathlib import Path

from .app import Library, serve
from .hf import HFClient
from .jobs import Settings

SEED_INDEX = Path(__file__).with_name("data") / "seed_index.json.gz"


def default_blender() -> Path | None:
    """Blender's own executable when running on its bundled Python."""
    exe = Path(sys.executable).resolve()
    # <install>/5.2/python/bin/python3.x  ->  <install>/blender[.exe]
    for cand in (exe.parents[3] / "blender.exe", exe.parents[3] / "blender") if len(exe.parents) > 3 else ():
        if cand.is_file():
            return cand
    return None


def pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not h:
            return False
        try:
            return k32.WaitForSingleObject(h, 0) == 0x102  # WAIT_TIMEOUT: still running
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


_on_exit = []  # callbacks run before the process exits (stop converter subprocesses)


def shutdown(reason: str, code: int = 0) -> None:
    logging.getLogger("simready.server").info("exiting: %s", reason)
    for cb in _on_exit:
        try:
            cb()
        except Exception:
            pass
    os._exit(code)


def watch_parent(pid: int) -> None:
    while pid_alive(pid):
        time.sleep(5)
    shutdown(f"parent process {pid} gone")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m simready.server")
    ap.add_argument("--state-dir", type=Path, required=True)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--max-res", type=int, default=2048, choices=(0, 4096, 2048, 1024))
    ap.add_argument("--blender", type=Path, default=None)
    ap.add_argument("--workers", type=int, default=1, help="parallel conversions")
    ap.add_argument("--keep-raw", action="store_true", help="keep downloaded USD/textures after conversion")
    ap.add_argument("--keep-blend", action="store_true", help="keep converted .blend files after delivering them")
    ap.add_argument("--mirror", type=Path, help="local copy of the dataset metadata at the same revision (dev)")
    ap.add_argument("--revision", help="pin a dataset commit instead of following main")
    ap.add_argument("--no-refresh", action="store_true", help="do not check Hugging Face for a new revision at start")
    ap.add_argument("--seed-index", type=Path, default=SEED_INDEX,
                    help="ready index (.json/.json.gz) used when the state dir has none (default: the one in the repo)")
    ap.add_argument("--check-hours", type=float, default=24, help="look for a new dataset revision every N hours (0 = never)")
    ap.add_argument("--prefetch-thumbs", action="store_true",
                    help="make all thumbnails in the background (~1.3 GB from HF, ~25 MB on disk)")
    ap.add_argument("--parent-pid", type=int, help="exit when this process (Blender) exits")
    ap.add_argument("--allow-any-client", action="store_true", help="accept /blend/ requests without X-Blender (tests)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)

    a.state_dir.mkdir(parents=True, exist_ok=True)
    (a.state_dir / "logs").mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname).1s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if a.verbose else logging.INFO)
    fh = logging.handlers.RotatingFileHandler(a.state_dir / "logs/server.log", maxBytes=5 << 20, backupCount=3,
                                              encoding="utf-8")
    sh = logging.StreamHandler()
    for h in (fh, sh):
        h.setFormatter(fmt)
        root.addHandler(h)

    blender = a.blender or default_blender()
    if not blender or not blender.is_file():
        ap.error("Blender executable not found: pass --blender")
    if a.parent_pid:
        threading.Thread(target=watch_parent, args=(a.parent_pid,), daemon=True).start()

    settings = Settings(blender=blender, state_dir=a.state_dir.resolve(), max_res=a.max_res,
                        workers=a.workers, keep_raw=a.keep_raw, keep_blend=a.keep_blend)
    lib = Library(settings, HFClient(), mirror=a.mirror, revision=a.revision, refresh=not a.no_refresh,
                  prefetch_thumbs=a.prefetch_thumbs, seed_index=a.seed_index, check_hours=a.check_hours)
    _on_exit.append(lib.converter.kill_children)
    signal.signal(signal.SIGTERM, lambda *_: shutdown("SIGTERM"))
    try:
        serve(lib, a.port, require_x_blender=not a.allow_any_client)
    except KeyboardInterrupt:
        shutdown("interrupted")
    except OSError as e:  # port in use
        logging.getLogger("simready.server").error("cannot listen on port %d: %s", a.port, e)
        shutdown("no port", 2)


if __name__ == "__main__":
    main()
