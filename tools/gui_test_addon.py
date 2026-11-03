# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI test of the installed add-on (phase 4), run inside a GUI Blender:

  env WAYLAND_DISPLAY= BLENDER_USER_RESOURCES=R XDG_CACHE_HOME=X blender --online-mode \
      --python tools/gui_test_addon.py -- --asset kitchenware_bowl_b01 --collection-asset ppe_eyeProtection_goggles_a01

Checks: server started by the add-on, one library registered, Blender's cache folder is a
link into the data folder, a download is converted and the listing is reloaded by the
add-on (no manual refresh), "Append, editable" makes dropped data local and real.
"""

import hashlib
import json
import os
import sys
import time
import traceback
import urllib.request
from pathlib import Path

import bpy

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
opts = dict(zip(argv[0::2], argv[1::2]))
ASSET = opts.get("--asset", "kitchenware_bowl_b01")
COLL_ASSET = opts.get("--collection-asset", "ppe_eyeProtection_goggles_a01")
MOD = "bl_ext.user_default.simready_library"
results = {}


def say(*a):
    print("DRIVER", *a, flush=True)


def check(name, ok, detail=""):
    results[name] = bool(ok)
    say("CHECK", "PASS" if ok else "FAIL", name, detail)


def prefs():
    return bpy.context.preferences.addons[MOD].preferences


def status():
    try:
        return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{prefs().port}/_status", timeout=2).read())
    except OSError:
        return None


def wait(cond, timeout, step=0.5):
    t = time.time()
    while time.time() - t < timeout:
        r = cond()
        if r:
            return r
        yield step
    return None


def our_lib():
    url = f"http://127.0.0.1:{prefs().port}/"
    return [l for l in bpy.context.preferences.filepaths.asset_libraries if l.use_remote_url and l.remote_url == url]


def local_page_hash(rel):
    """Hash of `rel` in Blender's local copy of the listing (what Blender believes)."""
    lib = our_lib()[0]
    for page in sorted(Path(lib.path, "_v1").glob("assets-*.json")):
        d = json.loads(page.read_text())
        for f in d.get("files", []):
            if f["path"] == rel:
                return f["hash"]
    return None


def sha(p):
    return "SHA256:" + hashlib.sha256(Path(p).read_bytes()).hexdigest()


def browser_override():
    win = bpy.context.window_manager.windows[0]
    area = win.screen.areas[0]
    area.type = 'FILE_BROWSER'
    area.ui_type = 'ASSETS'
    return win, area


def scenario():
    # 1. server + library + cache link
    st = None
    for _ in wait(lambda: status(), 40):
        yield _
    st = status()
    check("server running", st is not None, str(st and st.get("server")))
    from bl_ext.user_default.simready_library.runtime import RUNTIME
    check("server started by the add-on", RUNTIME.owns_server())
    libs = our_lib()
    check("exactly one library", len(libs) == 1, str([l.name for l in libs]))
    lib = libs[0]
    p = Path(lib.path)
    check("cache folder is a link into the data folder",
          p.is_symlink() and Path(os.readlink(p)) == Path(bpy.path.abspath(prefs().data_dir)) / "blender-cache" / p.name,
          f"{p} -> {os.readlink(p) if p.is_symlink() else '-'}")

    # 2. listing downloaded by Blender
    win, area = browser_override()
    for _ in wait(lambda: Path(lib.path, "_v1", "asset-index.json").exists()
                  and area.spaces.active.params is not None, 60):
        yield _
    space = area.spaces.active
    with bpy.context.temp_override(window=win, area=area):
        space.params.asset_library_reference = lib.name
    yield 3.0
    check("listing in Blender's cache", Path(lib.path, "_v1", "asset-index.json").exists())

    # 3. download an Object asset; the add-on must reload the listing by itself
    rel = f"blend/{ASSET.split('_')[0]}/{ASSET}.blend"
    before = local_page_hash(rel)
    with bpy.context.temp_override(window=win, area=area, region=[r for r in area.regions if r.type == 'WINDOW'][0]):
        r = bpy.ops.asset.asset_download(asset_library_type='CUSTOM', asset_library_identifier=lib.name,
                                         relative_asset_identifier=f"{rel}/Object/{ASSET}")
    say("asset_download ->", r)
    target = Path(lib.path) / rel
    for _ in wait(lambda: target.exists(), 300):
        yield _
    check("asset file downloaded", target.exists(), str(target))
    for _ in wait(lambda: target.exists() and local_page_hash(rel) == sha(target), 30):
        yield _
    after = local_page_hash(rel)
    check("listing reloaded automatically with the real hash", target.exists() and after == sha(target),
          f"before={before} after={after}")
    check("server dropped its copy", not any((Path(bpy.path.abspath(prefs().data_dir)) / "server/blend").rglob(f"{ASSET}.*.blend")))

    # 4. Append, editable: link a Collection asset with an instance empty, like a drop with Link/Pack
    prefs().import_mode = 'APPEND_EDITABLE'
    crel = f"blend/{COLL_ASSET.split('_')[0]}/{COLL_ASSET}.blend"
    for attempt in range(20):  # the asset list may be refreshing after the listing reload
        try:
            with bpy.context.temp_override(window=win, area=area,
                                           region=[r for r in area.regions if r.type == 'WINDOW'][0]):
                bpy.ops.asset.asset_download(asset_library_type='CUSTOM', asset_library_identifier=lib.name,
                                             relative_asset_identifier=f"{crel}/Collection/{COLL_ASSET}")
            break
        except RuntimeError as e:
            say("download retry", attempt, str(e).strip())
            yield 1.0
    ctarget = Path(lib.path) / crel
    for _ in wait(lambda: ctarget.exists(), 300):
        yield _
    check("collection asset downloaded", ctarget.exists())
    area.type = 'VIEW_3D'
    yield 1.0
    # a foreign linked library already in the file must stay linked
    other = Path(bpy.app.tempdir) / "other.blend"
    tmpcoll = bpy.data.collections.new("NotSimReady")
    tmpcoll.objects.link(bpy.data.objects.new("plain", bpy.data.meshes.new("plain_mesh")))
    bpy.data.libraries.write(str(other), {tmpcoll})
    bpy.data.objects.remove(bpy.data.objects["plain"])
    bpy.data.meshes.remove(bpy.data.meshes["plain_mesh"])
    bpy.data.collections.remove(tmpcoll)
    prefs().import_mode = 'PACK'
    with bpy.context.temp_override(window=win, area=area, region=[r for r in area.regions if r.type == 'WINDOW'][0]):
        bpy.ops.wm.link(directory=str(other) + "/Collection/", filename="NotSimReady", instance_collections=True)
    prefs().import_mode = 'APPEND_EDITABLE'
    yield 1.0
    n_before = len(bpy.data.objects)
    with bpy.context.temp_override(window=win, area=area, region=[r for r in area.regions if r.type == 'WINDOW'][0]):
        bpy.ops.wm.link(directory=str(ctarget) + "/Collection/", filename=COLL_ASSET, instance_collections=True)
    yield 2.0
    empties = [o for o in bpy.context.view_layer.objects if o.instance_type == 'COLLECTION'
               and o.instance_collection and o.instance_collection.name != "NotSimReady"]
    meshes = [o for o in bpy.context.view_layer.objects if o.type == 'MESH']
    check("instances made real", not empties and meshes, f"empties={[o.name for o in empties]} meshes={len(meshes)}")
    check("data made local", meshes and all(o.library is None and (o.data is None or o.data.library is None) for o in meshes),
          str([(o.name, o.library, o.data.library) for o in meshes][:3]))
    say("objects", n_before, "->", len(bpy.data.objects))

    # 5. same with Append (local data, instance empty of a local collection)
    with bpy.context.temp_override(window=win, area=area, region=[r for r in area.regions if r.type == 'WINDOW'][0]):
        bpy.ops.wm.append(directory=str(ctarget) + "/Collection/", filename=COLL_ASSET, instance_collections=True)
    yield 2.0
    empties = [o for o in bpy.context.view_layer.objects if o.instance_type == 'COLLECTION'
               and o.instance_collection and o.instance_collection.name != "NotSimReady"]
    check("append: instances made real", not empties, f"empties={[o.name for o in empties]}")

    # 6. the foreign library linked before is untouched
    foreign = [o for o in bpy.context.view_layer.objects if o.instance_type == 'COLLECTION'
               and o.instance_collection and o.instance_collection.name == "NotSimReady"]
    plain = next((o for o in bpy.data.objects if o.name == "plain"), None)
    check("foreign instance untouched", len(foreign) == 1, str([o.name for o in foreign]))
    check("foreign data still linked", plain is not None and plain.library is not None
          and plain.data.library is not None, str(plain and (plain.library, plain.data.library)))

    say("RESULTS", json.dumps(results))
    say("DONE", "ALL PASS" if all(results.values()) else "SOME FAILED")


def runner():
    gen = scenario()

    def step():
        try:
            return next(gen)
        except StopIteration:
            bpy.ops.wm.quit_blender()
            return None
        except Exception:
            traceback.print_exc()
            say("DONE", "CRASHED")
            bpy.ops.wm.quit_blender()
            return None
    bpy.app.timers.register(step, first_interval=2.0)


runner()
