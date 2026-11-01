# SPDX-License-Identifier: GPL-3.0-or-later
"""The remote asset library entry in Blender's preferences.

Rules learned in phase 1 (docs/fase1-report.md):
- create it only with `preferences.asset_library_add(type='REMOTE')`;
- the URL text must be exactly `http://127.0.0.1:<port>/` and must never change
  during a session (Blender keeps the library per URL until restart);
- never two libraries with the same URL (downloads break).
"""

from __future__ import annotations

from pathlib import Path

import bpy

LIBRARY_NAME = "NVIDIA SimReady"


def url_for(port: int) -> str:
    return f"http://127.0.0.1:{port}/"


def _norm(url: str) -> str:
    return url.strip().rstrip("/").lower()


def find(url: str) -> list:
    return [lib for lib in bpy.context.preferences.filepaths.asset_libraries
            if lib.use_remote_url and _norm(lib.remote_url) == _norm(url)]


def ensure(url: str) -> tuple[object | None, str]:
    """The library for `url`, created if missing. Returns (library, problem message)."""
    libs = find(url)
    if len(libs) > 1:
        return libs[0], (f"{len(libs)} asset libraries use {url}: keep only one "
                         "(Preferences > File Paths), then restart Blender")
    if libs:
        lib = libs[0]
        if lib.remote_url != url:
            return lib, f"The library URL should be exactly {url} (now {lib.remote_url!r})"
        return lib, ""
    try:
        bpy.ops.preferences.asset_library_add(type='REMOTE', name=LIBRARY_NAME, remote_url=url)
    except RuntimeError as e:
        return None, f"Could not add the asset library: {e}"
    libs = find(url)
    if not libs:
        return None, "Could not add the asset library"
    bpy.context.preferences.is_dirty = True  # saved with the preferences (auto-save or Save Preferences)
    return libs[0], ""


# The library's import method is never changed by the add-on: editing a remote library
# during a session confused Blender's asset lookup ("Asset could not be found").
# "Append, editable" works on top of whatever method Blender uses (see importer.py).


def reload_listing(lib) -> None:
    """Download the listing again (same as Shift+Refresh), without needing an Asset Browser."""
    import bl_pkg
    bl_pkg.remote_asset_library_sync(lib.remote_url, Path(lib.path))
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == 'FILE_BROWSER':
                area.tag_redraw()
