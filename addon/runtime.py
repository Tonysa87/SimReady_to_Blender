# SPDX-License-Identifier: GPL-3.0-or-later
"""Server process, status polling and automatic listing reload.

- the server runs as a child process on Blender's own Python
  (`sys.executable -s -E -m simready.server`), watching Blender's PID;
- a background thread polls `/_status` (never blocks the UI);
- a main-thread timer starts the server when needed, reloads the remote
  listing a moment after each delivered .blend (so Blender sees the real hash
  without Shift+Refresh) and keeps the status bar text up to date.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import bpy

ADDON_DIR = Path(__file__).resolve().parent
POLL_EVERY = 1.0
RELOAD_DELAY = 1.0        # after "delivered": let Blender finish writing the file
START_RETRY_AFTER = 15.0  # seconds between two automatic start attempts


def _http(url: str, method: str = "GET", timeout: float = 1.0):
    req = urllib.request.Request(url, method=method, headers={"X-SimReady-Control": "1"} if method == "POST" else {})
    # Never use a system proxy for 127.0.0.1.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as r:
        return r.read()


class Runtime:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.status: dict | None = None
        self.status_error = ""
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.poller: threading.Thread | None = None
        self.port = 0
        self.last_event_id = None
        self.server_pid = None
        self.reload_at = 0.0
        self.last_start = 0.0
        self.message = ""          # shown in the preferences
        self.recent_error = ("", 0.0)
        self.started_args: list[str] = []

    # --------------------------------------------------------------- polling

    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def start_polling(self, port: int) -> None:
        self.port = port
        if self.poller and self.poller.is_alive():
            return
        self.stop.clear()
        self.poller = threading.Thread(target=self._poll_loop, daemon=True, name="simready-status")
        self.poller.start()

    def _poll_loop(self) -> None:
        while not self.stop.is_set():
            try:
                st = json.loads(_http(self.base_url() + "_status", timeout=1.5))
                with self.lock:
                    self.status, self.status_error = st, ""
            except (OSError, ValueError) as e:
                with self.lock:
                    self.status, self.status_error = None, str(e)
            self.stop.wait(POLL_EVERY)

    def snapshot(self) -> dict | None:
        with self.lock:
            return self.status

    # ---------------------------------------------------------------- server

    def owns_server(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start_server(self, prefs, state_dir: Path) -> bool:
        if self.owns_server():
            return True
        if not self.port:
            self.port = prefs.port
        self.last_start = time.time()
        state_dir.mkdir(parents=True, exist_ok=True)
        args = [sys.executable, "-s", "-E", "-m", "simready.server",
                "--state-dir", str(state_dir), "--port", str(self.port), "--quiet",
                "--max-res", prefs.max_res, "--blender", bpy.app.binary_path,
                "--parent-pid", str(os.getpid())]
        if prefs.prefetch_thumbs:
            args.append("--prefetch-thumbs")
        env = dict(os.environ)
        if prefs.hf_token:
            env["HF_TOKEN"] = prefs.hf_token  # environment, never the command line
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        log = state_dir / "logs"
        log.mkdir(exist_ok=True)
        try:
            with (log / "stdout.log").open("wb") as out:  # only crashes end up here (--quiet)
                self.proc = subprocess.Popen(args, cwd=str(ADDON_DIR), env=env, stdin=subprocess.DEVNULL,
                                             stdout=out, stderr=subprocess.STDOUT, **kwargs)
        except OSError as e:
            self.message = f"Cannot start the server: {e}"
            return False
        self.started_args = args
        return True

    def stop_server(self, timeout: float = 5.0) -> None:
        """Stop the server we started; ask a foreign one politely."""
        if self.owns_server():
            try:
                _http(self.base_url() + "_control/shutdown", method="POST", timeout=2)
            except OSError:
                pass
            try:
                self.proc.wait(timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def shutdown_foreign(self) -> None:
        try:
            _http(self.base_url() + "_control/shutdown", method="POST", timeout=2)
        except OSError as e:
            self.message = f"Could not stop the server: {e}"

    def refresh_index(self) -> None:
        try:
            _http(self.base_url() + "_control/refresh-index", method="POST", timeout=2)
        except OSError as e:
            self.message = f"Could not reach the server: {e}"

    def shutdown(self) -> None:
        self.stop.set()
        self.stop_server()

    # ---------------------------------------------------------------- events

    def take_events(self) -> list[dict]:
        """New events since the last call (the first call only sets the baseline)."""
        st = self.snapshot()
        if not st:
            return []
        events = st.get("events", [])
        last_id = max((e["id"] for e in events), default=0)
        pid = st.get("server", {}).get("pid")
        if pid != self.server_pid:  # first poll, or a new server process: its ids start again from 1
            first = self.server_pid is None
            self.server_pid = pid
            self.last_event_id = last_id if first else 0  # replaying old events only costs a reload
            if first:
                return []
        new = [e for e in events if e["id"] > self.last_event_id]
        self.last_event_id = last_id
        return new


RUNTIME = Runtime()
