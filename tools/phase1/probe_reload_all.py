# SPDX-License-Identifier: GPL-3.0-or-later
"""Reproduce: in 'All Libraries', Shift+refresh (reload listing) after a download hides the asset."""
import sys, time, traceback
import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
URL, LOCAL_DIR, VIEW = argv[0], argv[1], argv[2]
win = bpy.context.window_manager.windows[0]
area = max(win.screen.areas, key=lambda a: a.width * a.height)
prefs = bpy.context.preferences

def log(*a): print("PROBE", *a, flush=True)

def ctx():
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return bpy.context.temp_override(window=win, area=area, region=region, space_data=area.spaces.active)

def assets():
    area.tag_redraw()
    with ctx():
        bpy.ops.file.select_all(action='SELECT')
        return sorted((a.name, a.is_online) for a in (bpy.context.selected_assets or []) if a.name.startswith("fake_"))

def steps():
    if LOCAL_DIR != "-":
        bpy.ops.preferences.asset_library_add(type='LOCAL', directory=LOCAL_DIR)
    bpy.ops.preferences.asset_library_add(type='REMOTE', remote_url=URL)
    lib = next(l for l in prefs.filepaths.asset_libraries if l.use_remote_url)
    area.ui_type = 'ASSETS'
    yield 0.5
    area.spaces.active.params.asset_library_reference = 'ALL' if VIEW == 'ALL' else lib.name
    # CATALOG_FILTER: show only catalog "Fake" (and children), like Tonysa87's screen.
    import uuid
    params = area.spaces.active.params
    params.catalog_id = str(uuid.uuid5(uuid.UUID("6f0b6f0e-5a1e-4c55-9d6b-5e1d2e9a0001"), "Fake"))
    if hasattr(params, "asset_catalog_visibility"):
        params.asset_catalog_visibility = 'CATALOG'
    log("catalog filter", params.catalog_id, getattr(params, "asset_catalog_visibility", None))
    for _ in range(40):
        if len(assets()) >= 2: break
        yield 0.5
    log("view", VIEW, "start:", assets())
    r = bpy.ops.asset.asset_download(asset_library_type='CUSTOM', asset_library_identifier=lib.name,
                                     relative_asset_identifier="blend/fake/fake_monkey_01.blend/Collection/fake_monkey_01")
    log("download:", r)
    yield 6
    log("after download:", assets())
    with ctx():
        log("Shift+refresh (library_refresh use_remote_listing) ->", bpy.ops.asset.library_refresh(use_remote_listing=True))
    for t in (1, 2, 4, 8):
        yield t
        log(f"after reload +{t}s:", assets())
    with ctx():
        log("plain refresh ->", bpy.ops.asset.library_refresh())
    yield 4
    log("after plain refresh:", assets())

gen = steps()
def tick():
    try:
        return next(gen)
    except StopIteration:
        log("DONE")
    except Exception:
        traceback.print_exc(); log("FAILED")
    bpy.ops.wm.quit_blender()
bpy.app.timers.register(tick, first_interval=1.0)
