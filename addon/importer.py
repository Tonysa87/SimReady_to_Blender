# SPDX-License-Identifier: GPL-3.0-or-later
"""Import mode "Append, editable" (Tonysa87, 1/11/2026).

After a SimReady asset is dropped, whatever import method Blender used (Link,
Pack, Append), make it fully editable: Make Instances Real on the empties that
instance a SimReady collection, then Make Local (All) on the SimReady data.

Detection uses the `blend_import_post` handler (Blender 5.2): every imported
item knows the .blend it comes from; only files inside the SimReady library's
cache folder count. The work runs a moment later in a timer, after the drop
operator has created the instance empty.
"""

from __future__ import annotations

import os
from pathlib import Path

import bpy
from bpy.app.handlers import persistent

# Set by __init__: () -> (mode, cache folder of the SimReady library or None)
settings = lambda: ("PACK", None)  # noqa: E731
_pending: list[tuple[str, str, str | None]] = []  # (id_type, name, library filepath)


def _norm(p) -> str:
    return os.path.normcase(os.path.realpath(bpy.path.abspath(str(p))))


def _inside(path: str, folder: str) -> bool:
    try:
        return os.path.commonpath([_norm(path), folder]) == folder
    except ValueError:  # different drives
        return False


def _lib_path(lib) -> str:
    """File a library (or a packed 'archive' library) really comes from."""
    while lib is not None and getattr(lib, "is_archive", False) and lib.archive_parent_library:
        lib = lib.archive_parent_library
    return lib.filepath if lib is not None else ""


def _ours(lib, cache: str) -> bool:
    path = _lib_path(lib)
    return bool(path) and _inside(path, cache)


@persistent
def on_import_post(context, *_args) -> None:
    mode, cache_dir = settings()
    if mode != 'APPEND_EDITABLE' or not cache_dir:
        return
    cache = _norm(cache_dir)
    found = False
    for item in context.import_items:
        # At the post stage `source_libraries` is empty; `source_library` is set (link and append).
        sources = [item.source_library.filepath] if item.source_library else []
        sources += [lib.filepath for lib in item.source_libraries]
        if not any(_inside(s, cache) for s in sources if s):
            continue
        id_ = item.id
        if id_ is not None:
            _pending.append((id_.id_type, id_.name, _lib_path(id_.library) or None))
            found = True
    if found and not bpy.app.timers.is_registered(_process):
        bpy.app.timers.register(_process, first_interval=0.05)


def _resolve(id_type: str, name: str, libpath: str | None):
    coll = {"OBJECT": bpy.data.objects, "COLLECTION": bpy.data.collections}.get(id_type)
    if coll is None:
        return None
    for id_ in coll:
        if id_.name == name and (_lib_path(id_.library) or None) == libpath:
            return id_
    return None


def _view3d_override():
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                region = next((r for r in area.regions if r.type == 'WINDOW'), None)
                if region:
                    return dict(window=win, screen=win.screen, area=area, region=region)
    win = bpy.context.window_manager.windows[0] if bpy.context.window_manager.windows else None
    return dict(window=win, screen=win.screen) if win else {}


def _process():
    items, _pending[:] = list(_pending), []
    mode, cache_dir = settings()
    if mode != 'APPEND_EDITABLE' or not cache_dir:
        return None
    cache = _norm(cache_dir)
    colls = {c for c in (_resolve(t, n, l) for t, n, l in items if t == 'COLLECTION') if c}
    objs = {o for o in (_resolve(t, n, l) for t, n, l in items if t == 'OBJECT') if o}
    view_layer = bpy.context.view_layer
    in_scene = set(view_layer.objects)

    # Empties instancing a SimReady collection that were just dropped.
    empties = [o for o in in_scene if o.instance_type == 'COLLECTION' and o.instance_collection is not None
               and (o.instance_collection in colls or _ours(o.instance_collection.library, cache))]
    try:
        with bpy.context.temp_override(**_view3d_override()):
            if empties:
                for o in view_layer.objects.selected:
                    o.select_set(False)
                for o in empties:
                    o.select_set(True)
                view_layer.objects.active = empties[0]
                bpy.ops.object.duplicates_make_real(use_base_parent=True, use_hierarchy=True)
            # Everything from the SimReady cache that is still linked (Link / Pack imports).
            linked = [o for o in view_layer.objects if o.library is not None and _ours(o.library, cache)]
            linked += [o for o in objs if o in in_scene and o.library is not None]
            dropped = set(view_layer.objects.selected) | set(linked)
            if any(_has_linked_data(o, cache) for o in dropped):
                for o in view_layer.objects.selected:
                    o.select_set(False)
                for o in dropped:
                    o.select_set(True)
                bpy.ops.object.make_local(type='ALL')
    except Exception as e:  # never break the user's drop
        print(f"SimReady: making the asset editable failed: {e}")
    return None


def _has_linked_data(ob, cache: str) -> bool:
    if ob.library is not None:
        return True
    data = ob.data
    if data is not None and data.library is not None and _ours(data.library, cache):
        return True
    return any(s.material and s.material.library is not None for s in ob.material_slots)


def register() -> None:
    if on_import_post not in bpy.app.handlers.blend_import_post:
        bpy.app.handlers.blend_import_post.append(on_import_post)


def unregister() -> None:
    if on_import_post in bpy.app.handlers.blend_import_post:
        bpy.app.handlers.blend_import_post.remove(on_import_post)
    if bpy.app.timers.is_registered(_process):
        bpy.app.timers.unregister(_process)
