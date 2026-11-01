# SPDX-License-Identifier: GPL-3.0-or-later
"""Reproduce: changing a remote library's import_method empties it in 'All Libraries'."""
import sys, time
import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
URL, VIEW = argv[0], argv[1]          # VIEW: ALL or library name
LIBNAME = "NVIDIA SimReady"
win = bpy.context.window_manager.windows[0]
area = max(win.screen.areas, key=lambda a: a.width * a.height)

def ctx():
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return bpy.context.temp_override(window=win, area=area, region=region, space_data=area.spaces.active)

def fake_assets():
    area.tag_redraw()
    with ctx():
        bpy.ops.file.select_all(action='SELECT')
        return sorted(a.name for a in (bpy.context.selected_assets or []) if a.name.startswith("fake_"))

def steps():
    libs = bpy.context.preferences.filepaths.asset_libraries
    if not libs.get(LIBNAME):
        bpy.ops.preferences.asset_library_add(type='REMOTE', name=LIBNAME, remote_url=URL)
    lib = libs[LIBNAME]
    area.ui_type = 'ASSETS'
    yield 0.5
    area.spaces.active.params.asset_library_reference = VIEW
    for i in range(20):
        if fake_assets(): break
        yield 0.5
    print("PROBE view", VIEW, "before:", fake_assets(), "lib.import_method", lib.import_method)
    for method in ("PACK", "APPEND"):
        with ctx():
            lib.import_method = method       # RNA update -> rna_userdef_asset_libraries_refresh
        for t in (0.5, 2, 5):
            yield t
            print(f"PROBE after import_method={method} +{t}s:", fake_assets())
    with ctx():
        print("PROBE library_refresh ->", bpy.ops.asset.library_refresh())
    yield 3
    print("PROBE after library_refresh:", fake_assets())

gen = steps()
def tick():
    try:
        return next(gen)
    except StopIteration:
        pass
    except Exception:
        import traceback; traceback.print_exc()
    bpy.ops.wm.quit_blender()
bpy.app.timers.register(tick, first_interval=1.0)
