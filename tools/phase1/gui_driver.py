# SPDX-License-Identifier: GPL-3.0-or-later
"""Drive a GUI Blender 5.2 through the remote-library cycle (phase-1 test).

blender --factory-startup --online-mode --python gui_driver.py -- \
    --url http://127.0.0.1:8765/ --libdir DIR --shots DIR --scenario SCEN

Scenarios:
  full      register lib, list, download fake_cube_01, reload listing, append
  status    register lib, just report the status of every asset (after restart)
"""

import hashlib
import json
import sys
import time
import traceback
import urllib.request
from pathlib import Path

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
opts = dict(zip(argv[0::2], argv[1::2]))
URL = opts["--url"]
LIBDIR = Path(opts["--libdir"])
SHOTS = Path(opts["--shots"])
SCEN = opts.get("--scenario", "full")
LIBNAME = "NVIDIA SimReady"
CUBE_REL = "blend/fake/fake_cube_01.blend"

SHOTS.mkdir(parents=True, exist_ok=True)


def say(*a):
    print("DRIVER", *a, flush=True)


def shot(name):
    # Screenshots are not capturable under WSLg/XWayland with llvmpipe (black image,
    # root-window grab fails); keep the call sites as checkpoints in the log.
    say("checkpoint", name)


def browser():
    win = bpy.context.window_manager.windows[0]
    for area in win.screen.areas:
        if area.type == 'FILE_BROWSER' and area.ui_type == 'ASSETS':
            region = next(r for r in area.regions if r.type == 'WINDOW')
            return win, area, region
    return None


def override():
    win, area, region = browser()
    return bpy.context.temp_override(window=win, area=area, region=region, space_data=area.spaces.active)


def all_assets():
    with override():
        bpy.ops.file.select_all(action='SELECT')
        assets = list(bpy.context.selected_assets or [])
    return assets


def asset_status():
    """Return {name: (is_online, needs_download, full_library_path)}."""
    out = {}
    for a in all_assets():
        with override():
            ctx = bpy.context.copy()
        with bpy.context.temp_override(**{k: v for k, v in ctx.items() if k in ("window", "area", "region", "space_data")},
                                       selected_assets=[a]):
            needs = bpy.ops.asset.assets_download.poll()
        out[a.name] = (a.is_online, needs, a.full_library_path)
    return out


def listed_file(relpath):
    page = LIBDIR / "_v1" / "assets-00000.json"
    if not page.exists():
        return None
    for f in json.loads(page.read_text())["files"]:
        if f["path"] == relpath:
            return f
    return None


def local_vs_listing(relpath):
    p = LIBDIR / relpath
    lf = listed_file(relpath)
    if not p.exists():
        return f"{relpath}: not on disk; listed hash={lf and lf['hash'][7:19]}"
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    return (f"{relpath}: on disk sha={h[:12]} size={p.stat().st_size}; listed sha={lf['hash'][7:19]} "
            f"size={lf['size_in_bytes']} -> {'EQUAL' if lf['hash'][7:] == h and lf['size_in_bytes'] == p.stat().st_size else 'DIFFERENT'}")


def server_status():
    with urllib.request.urlopen(URL + "_status") as r:
        return json.load(r)


def wait_assets(timeout=30):
    """Wait until the asset browser lists assets, redrawing and refreshing as needed."""
    t0 = time.time()
    refreshed = False
    while time.time() - t0 < timeout:
        _, area, _ = browser()
        area.tag_redraw()
        assets = all_assets()
        if assets:
            say(f"assets visible after {time.time() - t0:.1f}s (refresh used: {refreshed})")
            return True
        if not refreshed and time.time() - t0 > 8:
            with override():
                say("library_refresh ->", bpy.ops.asset.library_refresh())
            refreshed = True
        yield 0.5
    say("no assets visible after", timeout)
    return False


def wait_for(cond, timeout, step=0.25):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        yield step
    return False


def setup():
    prefs = bpy.context.preferences
    say("online_access", bpy.app.online_access, "experimental remote", prefs.experimental.use_remote_asset_libraries)
    libs = prefs.filepaths.asset_libraries
    global LIBDIR
    lib = libs.get(LIBNAME)
    if lib is None:
        # libs.new() creates a LOCAL library; only this operator sets the
        # (read-only in RNA) use_remote_url flag.
        say("asset_library_add ->", bpy.ops.preferences.asset_library_add(type='REMOTE', name=LIBNAME, remote_url=URL))
        lib = libs.get(LIBNAME)
    # For remote libraries Blender picks its own local cache dir: use that.
    LIBDIR = Path(lib.path)
    say("library", lib.name, lib.path, lib.remote_url, lib.use_remote_url)

    win = bpy.context.window_manager.windows[0]
    area = max(win.screen.areas, key=lambda a: a.width * a.height)
    area.ui_type = 'ASSETS'
    yield 0.5
    space = area.spaces.active
    items = [i.identifier for i in space.params.bl_rna.properties["asset_library_reference"].enum_items]
    say("library enum items", items)
    space.params.asset_library_reference = LIBNAME
    say("asset_library_reference now", space.params.asset_library_reference)
    if "--import-method" in opts:
        lib.import_method = opts["--import-method"]
        space.params.import_method = opts["--import-method"]
        say("import_method lib/browser:", lib.import_method, space.params.import_method)
    space.params.display_size_discrete = 'LARGE' if hasattr(space.params, "display_size_discrete") else None
    yield 0.5


def scenario_full():
    yield from setup()
    ok = yield from wait_for(lambda: listed_file(CUBE_REL) is not None, 30)
    say("listing on disk:", ok, sorted(p.name for p in (LIBDIR / "_v1").glob("*")) if ok else None)
    yield from wait_assets()
    shot("01_listing")
    say("status after listing", asset_status())
    say(local_vs_listing(CUBE_REL))
    gen0 = server_status()["generation"]

    # Download one asset, the way the "Download" button / context menu does.
    t0 = time.time()
    try:
        r = bpy.ops.asset.asset_download(asset_library_type='CUSTOM', asset_library_identifier=LIBNAME,
                                         relative_asset_identifier=CUBE_REL + "/Collection/fake_cube_01")
        say("asset_download ->", r)
    except Exception as ex:
        say("asset_download failed:", ex, "- falling back to assets_download")
        cube = next(a for a in all_assets() if a.name == "fake_cube_01")
        with override():
            ctx = {k: v for k, v in bpy.context.copy().items() if k in ("window", "area", "region", "space_data")}
        with bpy.context.temp_override(**ctx, selected_assets=[cube]):
            say("assets_download ->", bpy.ops.asset.assets_download())
    yield 0.5
    shot("02_downloading")
    ok = yield from wait_for(lambda: (LIBDIR / CUBE_REL).exists(), 120)
    say(f"download landed: {ok} after {time.time() - t0:.1f}s")
    yield 1.0
    shot("03_downloaded")
    say("status after download (before reload)", asset_status())
    say(local_vs_listing(CUBE_REL))

    # What the add-on would do: poll /_status, then reload the listing.
    st = server_status()
    say("server generation", gen0, "->", st["generation"], "events", st["events"])
    with override():
        say("reload poll", bpy.ops.asset.library_reload_listing.poll())
        say("reload ->", bpy.ops.asset.library_reload_listing())
    ok = yield from wait_for(lambda: "EQUAL" in local_vs_listing(CUBE_REL), 30)
    say("listing updated with real hash:", ok)
    yield 3.0
    shot("04_after_reload")
    say("status after reload", asset_status())
    say(local_vs_listing(CUBE_REL))

    # Use the asset: link+instance the collection like a drop would (append).
    p = LIBDIR / CUBE_REL
    with bpy.data.libraries.load(str(p), link=False, assets_only=True) as (src, dst):
        dst.collections = [c for c in src.collections if c == "fake_cube_01"]
    coll = dst.collections[0]
    bpy.context.scene.collection.children.link(coll)
    say("appended", coll.name, [o.name for o in coll.objects], "asset_data:", bool(coll.asset_data))


def scenario_status():
    yield from setup()
    ok = yield from wait_for(lambda: listed_file(CUBE_REL) is not None, 30)
    yield from wait_assets()
    shot("10_status_after_restart")
    say("status after restart", asset_status())
    say(local_vs_listing(CUBE_REL))


def run():
    gen = {"full": scenario_full, "status": scenario_status}[SCEN]()

    def tick():
        try:
            return next(gen)
        except StopIteration:
            say("DONE")
        except Exception:
            traceback.print_exc()
            say("FAILED")
        bpy.ops.wm.quit_blender()
        return None

    bpy.app.timers.register(tick, first_interval=1.0)


run()
