# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI test against tools/phase2/test_server.py with real assets.

blender --factory-startup --online-mode --python probe_real.py -- <url> <asset> [<asset> ...]
"""
import sys
import time
import traceback
import urllib.request
import json

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
URL, ASSETS = argv[0], argv[1:]
LIB = "SimReady test"
win = bpy.context.window_manager.windows[0]
area = max(win.screen.areas, key=lambda a: a.width * a.height)


def log(*a):
    print("PROBE", *a, flush=True)


def ctx(**extra):
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return bpy.context.temp_override(window=win, area=area, region=region, space_data=area.spaces.active, **extra)


def listing() -> dict:
    """{asset name: file path} from the server listing (names may differ from folder ids)."""
    with urllib.request.urlopen(URL + "_v1/assets-00000.json") as r:
        page = json.load(r)
    return {a["name"]: a["files"][0] for a in page["assets"]}


def assets():
    area.tag_redraw()
    with ctx():
        bpy.ops.file.select_all(action='SELECT')
        names = set(listing())
        return {a.name: a for a in (bpy.context.selected_assets or []) if a.name in names}


def status():
    out = {}
    for name, a in assets().items():
        with ctx(selected_assets=[a]):
            out[name] = ("online" if a.is_online else ("NEEDS_DOWNLOAD" if bpy.ops.asset.assets_download.poll() else "MATCH"),
                         a.id_type)
    return out


def generation():
    with urllib.request.urlopen(URL + "_status") as r:
        return json.load(r)["generation"]


def steps():
    bpy.ops.preferences.asset_library_add(type='REMOTE', name=LIB, remote_url=URL)
    lib = bpy.context.preferences.filepaths.asset_libraries[LIB]
    area.ui_type = 'ASSETS'
    for _ in range(20):
        yield 0.5
        try:
            area.spaces.active.params.asset_library_reference = LIB
        except TypeError:
            continue
        if area.spaces.active.params.asset_library_reference == LIB:
            break
    log("browser shows", area.spaces.active.params.asset_library_reference)
    for _ in range(40):
        if len(assets()) >= 6:
            break
        yield 0.5
    log("listed:", status())
    for name in ASSETS:
        a = assets()[name]
        rel = f"{listing()[name]}/{'Object' if a.id_type == 'OBJECT' else 'Collection'}/{name}"
        g0, t0 = generation(), time.time()
        log("download", name, bpy.ops.asset.asset_download(asset_library_type='CUSTOM', asset_library_identifier=LIB,
                                                            relative_asset_identifier=rel))
        while generation() == g0 and time.time() - t0 < 120:
            yield 0.5
        while status()[name][0] != "MATCH" and time.time() - t0 < 180:
            yield 0.5
        log(f"  {name}: converted+downloaded in {time.time() - t0:.1f}s ->", status()[name])
    with ctx():
        log("reload listing ->", bpy.ops.asset.library_reload_listing())
    yield 6
    log("after reload:", status())
    # Import like a drop (append) and report what arrives in the scene.
    for name in ASSETS:
        a = assets()[name]
        with bpy.data.libraries.load(a.full_library_path, link=False, assets_only=True) as (src, dst):
            if a.id_type == 'OBJECT':
                dst.objects = [name]
            else:
                dst.collections = [name]
        ids = dst.objects or dst.collections
        log(f"  appended {name}: {a.id_type} {[i.name for i in ids]}",
            {m.name: m.get('simready_source_model') for m in bpy.data.materials if m.users})


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


bpy.app.timers.register(tick, first_interval=1.0)
