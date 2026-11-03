# SPDX-License-Identifier: GPL-3.0-or-later
"""NVIDIA SimReady assets in Blender's Asset Browser.

The add-on runs a local server (127.0.0.1) that publishes the ~2,700 assets of
the Hugging Face dataset `nvidia/simready-assets` as a remote asset library and
converts each asset (USD -> .blend) the first time it is downloaded.
Assets: © NVIDIA Corporation, CC-BY-4.0.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty

from . import cache_link, importer, library
from .runtime import RUNTIME

TIMER_EVERY = 1.0
ERROR_SHOWN_STATUSBAR = 60   # seconds a failed conversion stays in the status bar
ERROR_SHOWN_PREFS = 3600     # ... and in the add-on preferences
_state = {"cache": ("", ""), "library_problem": "", "registered_url": ""}


# ------------------------------------------------------------------ helpers


def default_data_dir() -> str:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
        return str(base / "SimReady to Blender")
    if sys.platform == "darwin":
        return str(Path.home() / "Library/Application Support/SimReady to Blender")
    return str(Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "simready-to-blender")


def prefs():
    addon = bpy.context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


def data_dir(p=None) -> Path:
    p = p or prefs()
    raw = bpy.path.abspath(p.data_dir) if p and p.data_dir else default_data_dir()
    return Path(raw)


def server_dir(p=None) -> Path:
    return data_dir(p) / "server"


def our_library():
    p = prefs()
    if not p:
        return None
    libs = library.find(library.url_for(p.port))
    return libs[0] if libs else None


def _importer_settings():
    p = prefs()
    lib = our_library()
    return (p.import_mode if p else "PACK", lib.path if lib else None)


def _restart_server(_self=None, _context=None) -> None:
    if RUNTIME.owns_server():
        RUNTIME.stop_server()
    RUNTIME.last_start = 0.0  # the timer starts it again right away


def _port_changed(self, _context) -> None:
    RUNTIME.message = "Port changed: restart Blender to use it (Blender keeps the library URL per session)"


def _data_dir_changed(self, _context) -> None:
    _restart_server()
    RUNTIME.message = "Data folder changed: restart Blender to move Blender's cache there too"


# -------------------------------------------------------------- preferences


class SIMREADY_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    data_dir: StringProperty(
        name="Data Folder", subtype='DIR_PATH', default="",
        description="Index, thumbnails, server state and Blender's cache of the downloaded assets. "
                    "Empty: " + default_data_dir(),
        update=_data_dir_changed)
    max_res: EnumProperty(
        name="Texture Size",
        items=[('0', "Max", "Original NVIDIA textures (up to 4K)"),
               ('2048', "2K", "Textures larger than 2048 px are reduced"),
               ('1024', "1K", "Textures larger than 1024 px are reduced")],
        default='2048', update=_restart_server,
        description="Applied when an asset is converted. Changing it marks downloaded assets as updatable")
    color_jpeg: BoolProperty(
        name="JPEG Color Textures", default=False, update=_restart_server,
        description="Pack opaque color textures (albedo, emission) as high quality JPEG: .blend files "
                    "about 25% smaller, no visible difference. Normal, roughness and other data "
                    "textures always stay PNG. Changing it marks downloaded assets as updatable")
    import_mode: EnumProperty(
        name="Import",
        items=[('PACK', "Pack", "Blender's default: linked data packed into the file"),
               ('APPEND_EDITABLE', "Append, editable",
                "After dropping a SimReady asset: Make Instances Real and Make Local (All)")],
        default='PACK')
    prefetch_thumbs: BoolProperty(
        name="Prepare all thumbnails", default=True, update=_restart_server,
        description="Make every thumbnail in the background (~1.3 GB from Hugging Face once, ~25 MB on disk)")
    port: IntProperty(name="Port", default=8765, min=1024, max=65535, update=_port_changed,
                      description="Local server port. Changing it creates a new library (restart Blender)")
    autostart: BoolProperty(name="Start automatically", default=True,
                            description="Start the local server with Blender")
    hf_token: StringProperty(name="Hugging Face Token", subtype='PASSWORD', default="",
                             description="Optional: higher download rate limits", update=_restart_server)

    def draw(self, context):
        layout = self.layout
        st = RUNTIME.snapshot()

        box = layout.box()
        row = box.row()
        if st:
            srv, idx = st.get("server", {}), st.get("index", {})
            own = RUNTIME.owns_server()
            row.label(text=f"Server running (v{srv.get('version')}, {'started by this Blender' if own else 'external'})",
                      icon='CHECKMARK')
            if not own:
                box.label(text=f"External server data: {srv.get('state_dir')}", icon='INFO')
            text = f"{idx.get('assets', 0)} assets, dataset {str(idx.get('revision', ''))[:10]}"
            if idx.get("building"):
                text += f" — updating index {idx.get('done', 0)}/{idx.get('total', 0)}"
            box.label(text=text)
            if idx.get("error") and not idx.get("building"):
                box.label(text="Could not check Hugging Face for updates: the saved list is used", icon='INFO')
            for asset, job in (st.get("jobs") or {}).items():
                box.label(text=_job_text(asset, job), icon='TIME')
            if RUNTIME.recent_error[0] and time.time() - RUNTIME.recent_error[1] < ERROR_SHOWN_PREFS:
                box.label(text="Last problem: " + RUNTIME.recent_error[0], icon='ERROR')
        else:
            row.label(text="Server not running", icon='ERROR')
        row = box.row(align=True)
        row.operator("simready.server_start", icon='PLAY')
        row.operator("simready.server_stop", icon='PAUSE')
        row.operator("simready.check_updates", icon='FILE_REFRESH')
        row.operator("simready.open_folder", icon='FILE_FOLDER')

        for msg, icon in ((RUNTIME.message, 'INFO'), (_state["library_problem"], 'ERROR'),
                          (_state["cache"][1], 'INFO')):
            if msg:
                box.label(text=msg, icon=icon)
        if not bpy.app.online_access:
            box.label(text="Allow Online Access (Preferences > System) to use the library", icon='ERROR')

        col = layout.column()
        col.prop(self, "data_dir")
        col.prop(self, "max_res")
        col.prop(self, "color_jpeg")
        col.prop(self, "import_mode")
        col.prop(self, "prefetch_thumbs")
        col.prop(self, "autostart")
        col.prop(self, "port")
        col.prop(self, "hf_token")
        layout.label(text="Assets © NVIDIA Corporation, CC-BY-4.0 (huggingface.co/datasets/nvidia/simready-assets)")


def _job_text(asset: str, job: dict) -> str:
    stage = job.get("stage", "")
    if stage == "download" and job.get("wait"):
        return f"{asset}: Hugging Face rate limit, waiting {job['wait']} s"
    if stage == "download" and job.get("total"):
        return f"{asset}: downloading {100 * job.get('bytes', 0) / job['total']:.0f}%"
    if stage == "convert":
        return f"{asset}: converting ({time.time() - job.get('since', time.time()):.0f} s)"
    return f"{asset}: {stage}"


# ---------------------------------------------------------------- operators


class SIMREADY_OT_server_start(bpy.types.Operator):
    bl_idname = "simready.server_start"
    bl_label = "Start"
    bl_description = "Start the local SimReady server"

    def execute(self, context):
        if RUNTIME.snapshot():
            self.report({'INFO'}, "The server is already running")
            return {'CANCELLED'}
        RUNTIME.start_server(prefs(), server_dir())
        return {'FINISHED'}


class SIMREADY_OT_server_stop(bpy.types.Operator):
    bl_idname = "simready.server_stop"
    bl_label = "Stop"
    bl_description = "Stop the local SimReady server (also one started elsewhere on this port)"

    def execute(self, context):
        p = prefs()
        if RUNTIME.owns_server():
            RUNTIME.stop_server()
        else:
            RUNTIME.shutdown_foreign()
        p.autostart = False  # otherwise the timer would start it again
        return {'FINISHED'}


class SIMREADY_OT_check_updates(bpy.types.Operator):
    bl_idname = "simready.check_updates"
    bl_label = "Check for New Assets"
    bl_description = "Ask Hugging Face for a new revision of the dataset now"

    def execute(self, context):
        RUNTIME.refresh_index()
        return {'FINISHED'}


class SIMREADY_OT_open_folder(bpy.types.Operator):
    bl_idname = "simready.open_folder"
    bl_label = "Open Data Folder"
    bl_description = "Open the SimReady data folder"

    def execute(self, context):
        d = data_dir()
        d.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.path_open(filepath=str(d))
        return {'FINISHED'}


# --------------------------------------------------------------- status bar


def draw_statusbar(self, context):
    st = RUNTIME.snapshot()
    if not st:
        return
    jobs = st.get("jobs") or {}
    if jobs:
        asset, job = next(iter(jobs.items()))
        more = f" (+{len(jobs) - 1})" if len(jobs) > 1 else ""
        self.layout.label(text="SimReady: " + _job_text(asset, job) + more, icon='IMPORT')
    elif RUNTIME.recent_error[0] and time.time() - RUNTIME.recent_error[1] < ERROR_SHOWN_STATUSBAR:
        self.layout.label(text="SimReady: " + RUNTIME.recent_error[0], icon='ERROR')


# -------------------------------------------------------------------- timer


def _tick():
    p = prefs()
    if p is None:
        return None
    try:
        _tick_inner(p)
    except Exception as e:  # keep the timer alive
        print(f"SimReady: {e}")
    return TIMER_EVERY


def _tick_inner(p) -> None:
    if not _state["registered_url"]:
        # The port is read once per session: Blender keeps a remote library per URL until
        # restart, so a new port only applies at the next start (and so does the server).
        _state["registered_url"] = library.url_for(p.port)
        RUNTIME.start_polling(p.port)
    url = _state["registered_url"]

    lib, problem = our_library_by(url), ""
    if lib is None:
        if not _state.get("library_checked"):
            lib, problem = library.ensure(url)  # creates it if missing, reports duplicates
        else:
            # Removed (or edited) during the session: adding it again now breaks Blender's
            # asset lookup until restart (phase-1 report), so only tell the user.
            problem = "The SimReady asset library was removed or changed: restart Blender to add it again"
    _state["library_checked"] = True
    _state["library_problem"] = problem

    st = RUNTIME.snapshot()
    if st is not None and not _state.get("server_seen"):
        _state["server_seen"] = True
        RUNTIME.reload_at = time.time() + 1.0  # Blender may have tried the listing before the server was up
    elif st is None:
        _state["server_seen"] = False
    code = RUNTIME.proc.poll() if RUNTIME.proc is not None else None
    if code == 2 and st is None:
        RUNTIME.message = (f"Port {RUNTIME.port} is used by another program: retrying every 15 s "
                           "(or choose another port and restart Blender)")
    elif st is not None and RUNTIME.message.startswith("Port "):
        RUNTIME.message = ""
    if (st is None and p.autostart and bpy.app.online_access and not RUNTIME.owns_server()
            and time.time() - RUNTIME.last_start > 15.0):
        RUNTIME.start_server(p, server_dir(p))  # also after a "port in use" exit: the port may be free now

    for ev in RUNTIME.take_events():
        if ev.get("type") in ("delivered", "index"):
            RUNTIME.reload_at = time.time() + 1.0
        elif ev.get("type") == "error":
            RUNTIME.recent_error = (f"{ev.get('asset')}: {ev.get('message') or 'conversion failed'}", time.time())
    if RUNTIME.reload_at and time.time() >= RUNTIME.reload_at and lib is not None:
        try:
            started = library.reload_listing(lib)
        except Exception as e:
            print(f"SimReady: listing reload failed: {e}")
            started = True  # do not retry forever on a real error
        if started or _state.get("reload_tries", 0) >= 60:
            RUNTIME.reload_at, _state["reload_tries"] = 0.0, 0
        else:  # a sync is already running: try again shortly
            RUNTIME.reload_at = time.time() + 2.0
            _state["reload_tries"] = _state.get("reload_tries", 0) + 1

    for win in bpy.context.window_manager.windows:  # status bar text
        for area in win.screen.areas:
            if area.type == 'STATUSBAR':
                area.tag_redraw()


def our_library_by(url: str):
    libs = library.find(url)
    return libs[0] if len(libs) == 1 and libs[0].remote_url == url else None


# ------------------------------------------------------------- registration


def _link_blender_cache() -> None:
    """Point Blender's cache of this library into the data folder (before Blender uses it)."""
    try:
        p = prefs()
        if p is None:
            return
        if not p.data_dir:
            # Only once the user has chosen a folder: otherwise Blender's cache would be moved
            # into the default folder first and left behind when a real one is chosen.
            _state["cache"] = ("", "Choose a Data Folder to keep Blender's downloads there too")
            return
        url = library.url_for(p.port)
        lib = our_library_by(url)
        link = Path(lib.path) if lib is not None and lib.path else cache_link.library_cache_dir(url)
        # One sub-folder per library URL: two URLs (e.g. after a port change) never share a listing.
        _state["cache"] = cache_link.ensure(link, data_dir(p) / cache_link.LINK_DIRNAME / link.name)
    except Exception as e:
        _state["cache"] = ("error", f"Blender cache not moved: {e}")


classes = (SIMREADY_AddonPreferences, SIMREADY_OT_server_start, SIMREADY_OT_server_stop,
           SIMREADY_OT_check_updates, SIMREADY_OT_open_folder)


@bpy.app.handlers.persistent
def _on_exit(*_args):
    RUNTIME.shutdown()


def register():
    for c in classes:
        bpy.utils.register_class(c)
    _link_blender_cache()
    importer.settings = _importer_settings
    importer.register()
    bpy.types.STATUSBAR_HT_header.append(draw_statusbar)
    bpy.app.timers.register(_tick, first_interval=0.5, persistent=True)
    bpy.app.handlers.exit_pre.append(_on_exit)


def unregister():
    if _on_exit in bpy.app.handlers.exit_pre:
        bpy.app.handlers.exit_pre.remove(_on_exit)
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    bpy.types.STATUSBAR_HT_header.remove(draw_statusbar)
    importer.unregister()
    RUNTIME.shutdown()
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
