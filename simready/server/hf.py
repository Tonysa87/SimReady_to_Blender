# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal Hugging Face Hub client for the nvidia/simready-assets dataset (stdlib only).

- every request is pinned to a revision (commit sha), so an index and the files
  it describes always match;
- pacing from the `RateLimit` response header (fixed windows: "api" ~500 and
  "resolvers" ~3000 requests per 300 s for anonymous users), backoff on 429/5xx;
- downloads are streamed to `<dst>.<random>.part`, resumed with a Range request when the
  connection drops, verified (size + sha256) and renamed;
- every error carries a `kind` ("offline", "rate_limit", "server", "auth", "not_found",
  "http", "integrity", "cancelled") so the add-on can say what went wrong;
- the Authorization header (optional token) is never forwarded to the CDN.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import logging
import os
import re
import socket
import ssl
import threading
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger("simready.hf")

REPO = "nvidia/simready-assets"
HUB = "https://huggingface.co"
USER_AGENT = "SimReady-to-Blender/0.3 (+https://github.com/Tonysa87/SimReady_to_Blender)"
_RL = re.compile(r'"?(?P<bucket>[^";]+)"?\s*;\s*r=(?P<r>\d+)\s*;\s*t=(?P<t>\d+)')
_LINK_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')


class HFError(RuntimeError):
    kind = "http"

    def __init__(self, msg: str, kind: str | None = None) -> None:
        super().__init__(msg)
        if kind:
            self.kind = kind


class IntegrityError(HFError):
    kind = "integrity"


# Errors of an open connection worth a retry (or a resume). Disk errors are not among them.
NETWORK_ERRORS = (urllib.error.URLError, ConnectionError, TimeoutError, socket.timeout,
                  http.client.HTTPException, ssl.SSLError)


def _http_kind(code: int) -> str:
    if code == 429:
        return "rate_limit"
    if code >= 500:
        return "server"
    if code in (401, 403):
        return "auth"
    if code == 404:
        return "not_found"
    return "http"


class RateLimiter:
    """Shared pacing state per bucket, fed by the `RateLimit` response header."""

    def __init__(self, reserve: int = 20) -> None:
        self.reserve = reserve
        self.lock = threading.Lock()
        self.until: dict[str, float] = {}

    def wait(self, bucket: str) -> None:
        while True:
            with self.lock:
                delay = self.until.get(bucket, 0) - time.time()
            if delay <= 0:
                return
            log.info("rate limit %s: waiting %.0f s", bucket, delay)
            time.sleep(min(delay, 30))

    def update(self, header: str | None) -> None:
        if not header:
            return
        m = _RL.search(header)
        if not m:
            return
        remaining, reset = int(m["r"]), int(m["t"])
        if remaining <= self.reserve:
            with self.lock:
                self.until[m["bucket"]] = max(self.until.get(m["bucket"], 0), time.time() + reset + 1)

    def block(self, bucket: str, seconds: float) -> None:
        with self.lock:
            self.until[bucket] = max(self.until.get(bucket, 0), time.time() + seconds)

    def remaining(self, bucket: str) -> float:
        """Seconds still to wait before the next request in `bucket` (0 = none)."""
        with self.lock:
            return max(0.0, self.until.get(bucket, 0) - time.time())


class _Redirects(urllib.request.HTTPRedirectHandler):
    """Record the hub's RateLimit header on the 302 and drop credentials for other hosts."""

    def __init__(self, limiter: RateLimiter) -> None:
        self.limiter = limiter

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.limiter.update(headers.get("RateLimit"))
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).hostname != urllib.parse.urlsplit(req.full_url).hostname:
            new.remove_header("Authorization")
        return new


def _ssl_context() -> ssl.SSLContext:
    """certifi's CA bundle when available (Blender's Python ships it; its
    OpenSSL may not find the system store), otherwise the system default."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class HFClient:
    def __init__(self, token: str | None = None, repo: str = REPO, retries: int = 6, timeout: float = 120,
                 hub: str = HUB, backoff: float = 1.0) -> None:
        self.hub = hub.rstrip("/")
        self.backoff = backoff  # seconds of the first retry pause (doubles each time)
        self.repo = repo
        self.token = token or os.environ.get("HF_TOKEN") or None
        self.retries = retries
        self.timeout = timeout
        self.limiter = RateLimiter()
        self.opener = urllib.request.build_opener(_Redirects(self.limiter),
                                                  urllib.request.HTTPSHandler(context=_ssl_context()))
        self.requests = 0

    # ------------------------------------------------------------------ http

    def _open(self, url: str, bucket: str, extra: dict | None = None):
        headers = {"User-Agent": USER_AGENT, **(extra or {})}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        last: Exception | None = None
        kind = "offline"
        for attempt in range(self.retries):
            if attempt and kind != "rate_limit":
                time.sleep(self.backoff * 2 ** (attempt - 1))
            self.limiter.wait(bucket)
            self.requests += 1
            try:
                resp = self.opener.open(urllib.request.Request(url, headers=headers), timeout=self.timeout)
                self.limiter.update(resp.headers.get("RateLimit"))
                return resp
            except urllib.error.HTTPError as e:
                self.limiter.update(e.headers.get("RateLimit") if e.headers else None)
                last, kind = e, _http_kind(e.code)
                if e.code == 429:
                    retry = e.headers.get("Retry-After") if e.headers else None
                    wait = float(retry) if retry and retry.isdigit() else 30 * (attempt + 1)
                    self.limiter.block(bucket, wait)
                    continue
                if e.code >= 500:
                    continue
                raise HFError(f"HTTP {e.code} for {url}", kind) from e
            except (*NETWORK_ERRORS, OSError) as e:
                last, kind = e, "offline"
                reason = getattr(e, "reason", e)
                if isinstance(reason, (socket.gaierror, ConnectionRefusedError)) and attempt >= 2:
                    break  # no DNS / nothing listening: the network is down, do not wait a minute
        raise HFError(f"giving up on {url}: {last}", kind)

    def _api_json(self, url: str):
        with self._open(url, "api") as r:
            return json.load(r), r.headers.get("Link", "")

    # ------------------------------------------------------------------- api

    def revision(self, ref: str = "main") -> dict:
        """{'sha': commit, 'lastModified': ...} of the dataset at `ref`."""
        data, _ = self._api_json(f"{self.hub}/api/datasets/{self.repo}/revision/{urllib.parse.quote(ref)}")
        return {"sha": data["sha"], "lastModified": data.get("lastModified")}

    def tree(self, rev: str, path: str = "", recursive: bool = False) -> list[dict]:
        """Entries ({type, path, oid, size, lfs?}) of a folder, following `Link: rel=next` pagination."""
        q = {"limit": "1000", "expand": "false"}
        if recursive:
            q["recursive"] = "true"
        url = (f"{self.hub}/api/datasets/{self.repo}/tree/{rev}/{urllib.parse.quote(path)}".rstrip("/")
               + "?" + urllib.parse.urlencode(q))
        out: list[dict] = []
        while url:
            data, link = self._api_json(url)
            out += data
            m = _LINK_NEXT.search(link or "")
            url = m.group(1) if m else ""
            if url and not url.startswith(self.hub + "/"):  # never send the token elsewhere
                raise HFError(f"unexpected pagination URL {url!r}")
        return out

    def file_url(self, rev: str, path: str) -> str:
        return f"{self.hub}/datasets/{self.repo}/resolve/{rev}/{urllib.parse.quote(path)}"

    def get_bytes(self, rev: str, path: str, max_bytes: int = 64 << 20, sha256: str | None = None) -> bytes:
        with self._open(self.file_url(rev, path), "resolvers") as r:
            data = r.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise HFError(f"{path}: larger than {max_bytes} bytes")
        if sha256 and hashlib.sha256(data).hexdigest() != sha256:
            raise IntegrityError(f"{path}: sha256 mismatch")
        return data

    def download(self, rev: str, path: str, dst: Path, size: int | None = None, sha256: str | None = None,
                 progress=None, cancel=None, max_stalls: int = 4) -> int:
        """Stream `path` to `dst` (verified, atomic). Skips the download if `dst` already matches.

        A dropped connection is resumed where it stopped (Range request); `max_stalls` retries
        in a row without new bytes give up. A size or checksum mismatch starts over (twice at most).
        `progress(n)` receives byte deltas (negative when a transfer starts over).
        `cancel()` returning True stops the transfer (another file of the same job failed)."""
        if dst.exists() and (size is None or dst.stat().st_size == size) and (sha256 is None or sha256_file(dst) == sha256):
            return 0
        dst.parent.mkdir(parents=True, exist_ok=True)
        part = dst.with_name(f"{dst.name}.{uuid.uuid4().hex[:8]}.part")  # unique: no two writers share a file
        url = self.file_url(rev, path)
        h, n, stalls, restarts = hashlib.sha256(), 0, 0, 0
        last: Exception | None = None

        def start_over() -> None:
            nonlocal h, n
            f.seek(0)
            f.truncate()
            if progress and n:
                progress(-n)
            h, n = hashlib.sha256(), 0

        try:
            with part.open("wb") as f:
                while True:
                    if cancel and cancel():
                        raise HFError(f"{path}: cancelled", "cancelled")
                    if stalls > max_stalls:
                        raise HFError(f"{path}: connection keeps dropping ({last})", "offline")
                    if stalls:
                        time.sleep(self.backoff * 2 ** (stalls - 1))
                    before, dropped = n, None
                    try:
                        with self._open(url, "resolvers", {"Range": f"bytes={n}-"} if n else None) as r:
                            if n and not (r.status == 206 and
                                          (r.headers.get("Content-Range") or "").startswith(f"bytes {n}-")):
                                start_over()  # the server ignored the range
                            while True:
                                if cancel and cancel():
                                    raise HFError(f"{path}: cancelled", "cancelled")
                                try:
                                    chunk = r.read(1 << 20)
                                except NETWORK_ERRORS as e:
                                    dropped = e
                                    break
                                if not chunk:
                                    # http.client returns b"" (no IncompleteRead) when the
                                    # connection closes before Content-Length: a drop, too.
                                    if getattr(r, "length", None) or (size is not None and n < size):
                                        dropped = http.client.IncompleteRead(b"", getattr(r, "length", None))
                                    break
                                if size is not None and n + len(chunk) > size:
                                    n = size + 1  # longer than announced: fails the size check below
                                    break
                                f.write(chunk)  # disk errors (full, permissions) propagate: no retry
                                h.update(chunk)
                                n += len(chunk)
                                if progress:
                                    progress(len(chunk))
                    except NETWORK_ERRORS as e:  # _open gave up with an HFError already; this is a late failure
                        dropped = e
                    if dropped is not None and (size is None or n < size):
                        last = dropped
                        if n > before:
                            stalls = 0
                            log.info("%s: connection dropped at %d bytes, resuming", path, n)
                        else:
                            stalls += 1
                        continue
                    if (size is not None and n != size) or (sha256 and h.hexdigest() != sha256):
                        restarts += 1
                        if restarts > 2:
                            raise IntegrityError(f"{path}: got {n} bytes, sha256 {h.hexdigest()[:12]}…")
                        log.warning("%s: size/checksum mismatch, downloading again", path)
                        start_over()
                        continue
                    break
            _replace_file(part, dst)
            return n
        finally:
            part.unlink(missing_ok=True)


def _replace_file(src: Path, dst: Path, tries: int = 10) -> None:
    """os.replace with retries: on Windows antivirus/indexers briefly lock new files."""
    for i in range(tries):
        try:
            src.replace(dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.2 * (i + 1))
