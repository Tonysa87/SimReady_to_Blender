# SPDX-License-Identifier: GPL-3.0-or-later
"""Import mode "Append, editable" (Tonysa87, 1/11/2026).

After a SimReady asset is dropped, whatever import method Blender used (Link,
Pack, Append), make it fully editable: Make Instances Real on the new empties
that instance a SimReady collection, then make local the data that comes from
the SimReady cache, and nothing else (other linked libraries of the user's file
stay linked).

Detection uses Blender 5.2's `blend_import_pre/post` handlers: the pre handler
remembers which objects already existed, the post handler checks that the
imported items come from a .blend inside the SimReady library's cache folder.
The work runs a moment later in a timer, after the drop operator has created
the instance empty, and only touches objects that did not exist before.
"""

from __future__ import annotations

import os

import bpy
from bpy.app.handlers import persistent

# Set by __init__: () -> (mode, cache folder of the SimReady library or None)
settings = lambda: ("PACK", None)  # noqa: E731
_before: set[int] = set()       # object pointers before the current import
_pending = {"active": False}

# Make-local order: users before their data, so every ID becomes local in place
# instead of being copied (Blender copies an ID that still has linked users).
_LOCAL_ORDER = ("collections", "objects", "meshes", "curves", "materials", "node_groups", "images", "textures")


def _norm(p) -> str:
    return os.path.normcase(os.path.realpath(bpy.path.abspath(str(p))))


def _inside(path: str, folder: str) -> bool:
    try:
        return os.path.commonpath([_norm(path), folder]) == folder
    except ValueError:  # different drives
        return False


def _source(lib) -> str:
    """File a library (or a packed 'archive' library) really comes from."""
    while lib is not None and getattr(lib, "is_archive", False) and lib.archive_parent_library:
        lib = lib.archive_parent_library
    return lib.filepath if lib is not None else ""


def _ours(lib, cache: str) -> bool:
    path = _source(lib)
    return bool(path) and _inside(path, cache)


@persistent
def on_import_pre(context, *_args) -> None:
    mode, cache_dir = settings()
    if mode == 'APPEND_EDITABLE' and cache_dir and not _pending["active"]:
        _before.clear()
        _before.update(o.as_pointer() for o in bpy.data.objects)


@persistent
def on_import_post(context, *_args) -> None:
    mode, cache_dir = settings()
    if mode != 'APPEND_EDITABLE' or not cache_dir:
        return
    cache = _norm(cache_dir)
    for item in context.import_items:
        # At the post stage `source_libraries` is empty; `source_library` is set (link and append).
        sources = [item.source_library.filepath] if item.source_library else []
        sources += [lib.filepath for lib in item.source_libraries]
        if any(_inside(s, cache) for s in sources if s):
            _pending["active"] = True
            break
    if _pending["active"] and not bpy.app.timers.is_registered(_process):
        bpy.app.timers.register(_process, first_interval=0.05)


def _context_override():
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                region = next((r for r in area.regions if r.type == 'WINDOW'), None)
                if region:
                    return dict(window=win, screen=win.screen, area=area, region=region)
    wins = bpy.context.window_manager.windows
    return dict(window=wins[0], screen=wins[0].screen) if wins else {}


def _make_local_ours(cache: str) -> int:
    """Make local every ID linked (or packed) from the SimReady cache; leave other libraries alone."""
    n = 0
    for attr in _LOCAL_ORDER:
        for id_ in list(getattr(bpy.data, attr)):
            if id_.library is not None and _ours(id_.library, cache):
                try:
                    id_.make_local()
                    n += 1
                except RuntimeError as e:
                    print(f"SimReady: cannot make {id_.name!r} local: {e}")
    return n


def _process():
    _pending["active"] = False
    mode, cache_dir = settings()
    if mode != 'APPEND_EDITABLE' or not cache_dir:
        return None
    cache = _norm(cache_dir)
    try:
        with bpy.context.temp_override(**_context_override()):
            view_layer = bpy.context.view_layer
            new = [o for o in view_layer.objects if o.as_pointer() not in _before]
            # Only the new empties that instance a SimReady collection (linked or appended from the cache).
            empties = [o for o in new if o.instance_type == 'COLLECTION' and o.instance_collection is not None
                       and (_ours(o.instance_collection.library, cache) or o.instance_collection.library is None)]
            empties = [o for o in empties if _from_cache(o.instance_collection, cache)]
            if empties:
                for o in view_layer.objects.selected:
                    o.select_set(False)
                for o in empties:
                    o.select_set(True)
                view_layer.objects.active = empties[0]
                bpy.ops.object.duplicates_make_real(use_base_parent=True, use_hierarchy=True)
            _make_local_ours(cache)
    except Exception as e:  # never break the user's drop
        print(f"SimReady: making the asset editable failed: {e}")
    finally:
        _before.clear()
    return None


def _from_cache(coll, cache: str) -> bool:
    """A linked collection from the cache, or an appended one whose objects came from it
    (appended data has no library any more: it was imported in this operation)."""
    if coll.library is not None:
        return _ours(coll.library, cache)
    return bool(coll.objects) and all(o.as_pointer() not in _before for o in coll.all_objects)


def register() -> None:
    if on_import_pre not in bpy.app.handlers.blend_import_pre:
        bpy.app.handlers.blend_import_pre.append(on_import_pre)
    if on_import_post not in bpy.app.handlers.blend_import_post:
        bpy.app.handlers.blend_import_post.append(on_import_post)


def unregister() -> None:
    for lst, fn in ((bpy.app.handlers.blend_import_pre, on_import_pre),
                    (bpy.app.handlers.blend_import_post, on_import_post)):
        if fn in lst:
            lst.remove(fn)
    if bpy.app.timers.is_registered(_process):
        bpy.app.timers.unregister(_process)
