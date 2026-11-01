# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal Hugging Face Hub client for the nvidia/simready-assets dataset (stdlib only).

- every request is pinned to a revision (commit sha), so an index and the files
  it describes always match;
- pacing from the `RateLimit` response header (fixed windows: "api" ~500 and
  "resolvers" ~3000 requests per 300 s for anonymous users), backoff on 429/5xx;
- downloads are streamed to `<dst>.part`, verified (size + sha256) and renamed;
- the Authorization header (optional token) is never forwarded to the CDN.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import ssl
import threading
import time
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
    pass


class IntegrityError(HFError):
    pass


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
    def __init__(self, token: str | None = None, repo: str = REPO, retries: int = 6, timeout: float = 120) -> None:
        self.repo = repo
        self.token = token or os.environ.get("HF_TOKEN") or None
        self.retries = retries
        self.timeout = timeout
        self.limiter = RateLimiter()
        self.opener = urllib.request.build_opener(_Redirects(self.limiter),
                                                  urllib.request.HTTPSHandler(context=_ssl_context()))
        self.requests = 0

    # ------------------------------------------------------------------ http

    def _open(self, url: str, bucket: str):
        headers = {"User-Agent": USER_AGENT}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        last: Exception | None = None
        for attempt in range(self.retries):
            self.limiter.wait(bucket)
            self.requests += 1
            try:
                resp = self.opener.open(urllib.request.Request(url, headers=headers), timeout=self.timeout)
                self.limiter.update(resp.headers.get("RateLimit"))
                return resp
            except urllib.error.HTTPError as e:
                self.limiter.update(e.headers.get("RateLimit") if e.headers else None)
                if e.code == 429:
                    retry = e.headers.get("Retry-After") if e.headers else None
                    wait = float(retry) if retry and retry.isdigit() else 30 * (attempt + 1)
                    self.limiter.block(bucket, wait)
                    last = e
                    continue
                if e.code >= 500:
                    last = e
                    time.sleep(2 ** attempt)
                    continue
                raise HFError(f"HTTP {e.code} for {url}") from e
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                last = e
                time.sleep(2 ** attempt)
        raise HFError(f"giving up on {url}: {last}")

    def _api_json(self, url: str):
        with self._open(url, "api") as r:
            return json.load(r), r.headers.get("Link", "")

    # ------------------------------------------------------------------- api

    def revision(self, ref: str = "main") -> dict:
        """{'sha': commit, 'lastModified': ...} of the dataset at `ref`."""
        data, _ = self._api_json(f"{HUB}/api/datasets/{self.repo}/revision/{urllib.parse.quote(ref)}")
        return {"sha": data["sha"], "lastModified": data.get("lastModified")}

    def tree(self, rev: str, path: str = "", recursive: bool = False) -> list[dict]:
        """Entries ({type, path, oid, size, lfs?}) of a folder, following `Link: rel=next` pagination."""
        q = {"limit": "1000", "expand": "false"}
        if recursive:
            q["recursive"] = "true"
        url = (f"{HUB}/api/datasets/{self.repo}/tree/{rev}/{urllib.parse.quote(path)}".rstrip("/")
               + "?" + urllib.parse.urlencode(q))
        out: list[dict] = []
        while url:
            data, link = self._api_json(url)
            out += data
            m = _LINK_NEXT.search(link or "")
            url = m.group(1) if m else ""
        return out

    def file_url(self, rev: str, path: str) -> str:
        return f"{HUB}/datasets/{self.repo}/resolve/{rev}/{urllib.parse.quote(path)}"

    def get_bytes(self, rev: str, path: str, max_bytes: int = 64 << 20, sha256: str | None = None) -> bytes:
        with self._open(self.file_url(rev, path), "resolvers") as r:
            data = r.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise HFError(f"{path}: larger than {max_bytes} bytes")
        if sha256 and hashlib.sha256(data).hexdigest() != sha256:
            raise IntegrityError(f"{path}: sha256 mismatch")
        return data

    def download(self, rev: str, path: str, dst: Path, size: int | None = None, sha256: str | None = None,
                 progress=None) -> int:
        """Stream `path` to `dst` (verified, atomic). Skips the download if `dst` already matches."""
        if dst.exists() and (size is None or dst.stat().st_size == size) and (sha256 is None or sha256_file(dst) == sha256):
            return 0
        dst.parent.mkdir(parents=True, exist_ok=True)
        part = dst.with_name(dst.name + ".part")
        for attempt in range(3):
            h, n = hashlib.sha256(), 0
            try:
                with self._open(self.file_url(rev, path), "resolvers") as r, part.open("wb") as f:
                    while chunk := r.read(1 << 20):
                        f.write(chunk)
                        h.update(chunk)
                        n += len(chunk)
                        if progress:
                            progress(len(chunk))
            except (OSError, TimeoutError) as e:  # connection dropped mid-body
                if attempt == 2:
                    raise HFError(f"{path}: {e}") from e
                continue
            if (size is not None and n != size) or (sha256 and h.hexdigest() != sha256):
                part.unlink(missing_ok=True)
                if attempt == 2:
                    raise IntegrityError(f"{path}: got {n} bytes, sha256 {h.hexdigest()[:12]}…")
                continue
            part.replace(dst)
            return n
        raise HFError(f"{path}: download failed")
