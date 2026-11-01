# SPDX-License-Identifier: GPL-3.0-or-later
"""Put Blender's cache of the SimReady library inside the user's data folder.

Blender stores a remote library's downloads in a fixed place:
`<OS cache dir>/Blender Foundation/Blender/Cache/remote-assets/<md5(url)[:16]>` on Windows
(`appdir.cc`, `remote_library.cc`); there is no preference to move it. So that folder
becomes a link (a directory junction on Windows: no admin rights needed; a symlink
elsewhere) to `<data folder>/blender-cache`.

No bpy here: pure filesystem logic, unit-tested.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
from pathlib import Path

LINK_DIRNAME = "blender-cache"
MIGRATING = ".simready-migrating"


def blender_cache_root() -> Path:
    """`BKE_appdir_folder_caches()` + "remote-assets"."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
        return base / "Blender Foundation" / "Blender" / "Cache" / "remote-assets"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "Blender" / "remote-assets"
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "blender" / "remote-assets"


def library_cache_dir(url: str, root: Path | None = None) -> Path:
    """Blender's folder for a remote library: md5 of the exact URL text, 16 hex digits."""
    return (root or blender_cache_root()) / hashlib.md5(url.encode("utf-8")).hexdigest()[:16]


def is_link(p: Path) -> bool:
    return p.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(p))


def link_target(p: Path) -> Path | None:
    if not is_link(p):
        return None
    try:
        return Path(os.readlink(p))
    except OSError:
        return None


def _same(a: Path, b: Path) -> bool:
    def norm(p: Path) -> str:
        s = os.path.normcase(os.path.abspath(str(p)))
        return s[4:] if s.startswith("\\\\?\\") else s  # readlink on Windows may add \\?\
    return norm(a).rstrip("\\/") == norm(b).rstrip("\\/")


def _make_link(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def _remove_link(link: Path) -> None:
    """Remove only the link, never what it points to."""
    if sys.platform == "win32" and not link.is_symlink():
        os.rmdir(link)  # a junction: rmdir removes the reparse point, not the target
    else:
        os.unlink(link)


def _move_children(src: Path, dst: Path) -> list[str]:
    """Move everything from src into dst (existing entries in dst win). Returns failures."""
    failed = []
    for child in list(src.iterdir()):
        dest = dst / child.name
        try:
            if dest.exists():
                if child.is_dir() and not is_link(child):
                    failed += _move_children(child, dest)
                    child.rmdir()
                else:
                    child.unlink()  # keep the copy already in the data folder
            else:
                shutil.move(str(child), str(dest))
        except OSError as e:
            failed.append(f"{child}: {e}")
    return failed


def ensure(link: Path, target: Path) -> tuple[str, str]:
    """Make `link` point to `target`. Returns (state, message).

    state: "ok" (already linked), "linked" (created now), "moved" (existing cache moved
    into the data folder), "busy" (the folder is in use: retry at the next start),
    "error".
    """
    try:
        target.mkdir(parents=True, exist_ok=True)
        link.parent.mkdir(parents=True, exist_ok=True)
        pending = link.with_name(link.name + MIGRATING)

        if is_link(link):
            current = link_target(link)
            if current is not None and _same(current, target):
                state, msg = "ok", ""
            else:
                # The data folder changed: point to the new one, leave the old cache where it is.
                _remove_link(link)
                _make_link(link, target)
                state, msg = "linked", f"previous Blender cache left in {current}"
        elif link.exists():
            # A real folder (cache made before the add-on): rename it first, which fails while
            # Blender keeps files open in it (Windows), so nothing is half moved.
            try:
                link.rename(pending)
            except OSError as e:
                return "busy", f"Blender is using {link}: restart Blender to move it ({e})"
            _make_link(link, target)
            state, msg = "moved", ""
        else:
            _make_link(link, target)
            state, msg = "linked", ""

        if pending.exists():  # finish a migration (also one interrupted earlier)
            failed = _move_children(pending, target)
            if failed:
                return "busy", f"{len(failed)} files still to move from {pending}; retried at the next start"
            pending.rmdir()
        return state, msg
    except OSError as e:
        return "error", str(e)
