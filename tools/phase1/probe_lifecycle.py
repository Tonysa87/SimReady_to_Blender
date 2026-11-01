# SPDX-License-Identifier: GPL-3.0-or-later
"""Reproduce Tonysa87's Windows sequence on Linux GUI Blender.

blender --factory-startup --online-mode --python probe_lifecycle.py -- <url> <local_lib_dir>

After every step: list fake_* assets in the browser (ALL view) and in the global
"All" list, and try ASSET_OT_asset_download the way the tile button does (by
library *name* + relative identifier).
"""
import sys
import time
import traceback
from pathlib import Path

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
URL, LOCAL_DIR = argv[0], argv[1]
win = bpy.context.window_manager.windows[0]
area = max(win.screen.areas, key=lambda a: a.width * a.height)
prefs = bpy.context.preferences


def log(*a):
    print("PROBE", *a, flush=True)


def ctx():
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return bpy.context.temp_override(window=win, area=area, region=region, space_data=area.spaces.active)


def browser_assets():
    area.tag_redraw()
    with ctx():
        bpy.ops.file.select_all(action='SELECT')
        return sorted((a.name, a.is_online) for a in (bpy.context.selected_assets or []) if a.name.startswith("fake_"))


def remote_libs():
    return [(i, l.name, l.use_remote_url, Path(l.path).name) for i, l in enumerate(prefs.filepaths.asset_libraries)
            if l.use_remote_url]


def try_download(libname, asset):
    rel = f"blend/fake/{asset}.blend/Collection/{asset}"
    try:
        r = bpy.ops.asset.asset_download(asset_library_type='CUSTOM', asset_library_identifier=libname,
                                         relative_asset_identifier=rel)
        log(f"  download {asset} via name {libname!r}: {r}")
    except Exception as ex:
        log(f"  download {asset} via name {libname!r}: FAILED {str(ex).strip()}")


def wait_assets(n=2, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if len(browser_assets()) >= n:
            break
        yield 0.5


def report(step):
    log(f"--- {step}")
    log("  remote libs:", remote_libs())
    log("  browser(ALL):", browser_assets())


def steps():
    # A real local library next to the remote one, like on Tonysa87's machine.
    bpy.ops.preferences.asset_library_add(type='LOCAL', directory=LOCAL_DIR)
    area.ui_type = 'ASSETS'
    yield 0.5
    area.spaces.active.params.asset_library_reference = 'ALL'
    yield 1

    # 1. Add remote library from the UI operator, without a name (as the dialog does).
    bpy.ops.preferences.asset_library_add(type='REMOTE', remote_url=URL)
    lib = next(l for l in prefs.filepaths.asset_libraries if l.use_remote_url)
    name1 = lib.name
    yield from wait_assets()
    report(f"1 added remote lib (auto name {name1!r})")
    try_download(name1, "fake_monkey_01")
    yield 6
    report("1b after monkey download")

    # 2. Change import method (what made assets vanish on Windows).
    for m in ("APPEND", "PACK", "APPEND"):
        lib.import_method = m
        with ctx():
            pass
        yield 3
        report(f"2 import_method={m}")
    try_download(name1, "fake_cube_01")
    yield 6
    report("2b after cube download")

    # 3. Remove the remote library and add it again.
    idx = next(i for i, l in enumerate(prefs.filepaths.asset_libraries) if l.use_remote_url)
    log("  remove ->", bpy.ops.preferences.asset_library_remove(index=idx))
    yield 3
    report("3 removed")
    bpy.ops.preferences.asset_library_add(type='REMOTE', remote_url=URL)
    lib = next(l for l in prefs.filepaths.asset_libraries if l.use_remote_url)
    yield from wait_assets()
    yield 2
    report(f"3b re-added (name {lib.name!r})")

    # 3c. Workarounds within the same session.
    area.spaces.active.params.asset_library_reference = lib.name
    yield 3
    log("  browser(lib view):", browser_assets())
    with ctx():
        log("  library_refresh ->", bpy.ops.asset.library_refresh())
    yield 4
    log("  browser(lib view) after refresh:", browser_assets())
    area.spaces.active.params.asset_library_reference = 'ALL'
    yield 3
    log("  browser(ALL) after switching back:", browser_assets())
    with ctx():
        log("  library_refresh(ALL) ->", bpy.ops.asset.library_refresh())
    yield 4
    log("  browser(ALL) after refresh:", browser_assets())
    try_download(lib.name, "fake_cube_01")
    return

    # 4. Rename the library.
    lib.name = "NVIDIA SimReady"
    yield 3
    report("4 renamed to 'NVIDIA SimReady'")
    try_download("NVIDIA SimReady", "fake_cube_01")
    try_download(name1, "fake_cube_01")


gen = steps()


def tick():
    try:
        return next(gen)
    except StopIteration:
        log("DONE")
    except Exception:
        traceback.print_exc()
        log("FAILED")
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(tick, first_interval=1.0)
