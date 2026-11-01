# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase-3 GUI driver (experiments 1b-d and 2). Derived from tools/phase1/gui_driver.py.

blender --factory-startup --online-mode --python gui_driver3.py -- --url http://127.0.0.1:8792/ --scenario SCEN

Scenarios:
  long      exp 1: download fake_cube_01 (slow, gzip keep-alive) + queue fake_monkey_01 (fails mid-stream)
            + mis_name_a01 behind it; reload listing while the long download runs; retry monkey; append.
  mismatch  exp 2: download the three mismatch assets, inspect, append, reload listing (corrected), inspect.
  status    after restart: inspect every asset.
"""

import hashlib
import json
import logging
import sys
import time
import traceback
import urllib.request
from pathlib import Path

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
opts = dict(zip(argv[0::2], argv[1::2]))
URL = opts["--url"]
SCEN = opts.get("--scenario", "long")
LIBNAME = "SimReady P3"
LIBDIR = Path()
T0 = time.time()

logging.basicConfig(stream=sys.stdout, level=logging.WARNING,
                    format="PYLOG %(levelname)s %(name)s: %(message)s")
logging.getLogger("_bpy_internal.assets.remote_library.asset_downloader").setLevel(logging.INFO)


def say(*a):
    print(f"DRIVER [{time.time() - T0:7.1f}s]", *a, flush=True)


# Spy on what the downloader tells the C++ side (what drives the UI).
_last_progress = {}
for _fn in ("asset_library_status_ping_asset_file_failed", "asset_library_status_ping_asset_file_succeeded",
            "asset_library_status_ping_asset_file_progress", "asset_library_status_ping_finished_download_queue"):
    try:
        _orig = getattr(bpy.types.WindowManager, _fn)

        def _wrap(*a, _orig=_orig, _fn=_fn):
            if _fn.endswith("progress"):
                key = a[0]
                if key not in _last_progress or a[1] - _last_progress[key] > 20000 or a[1] == 0:
                    say("PING", _fn, a)
                _last_progress[key] = a[1]
            else:
                say("PING", _fn, a)
            return _orig(*a)
        setattr(bpy.types.WindowManager, _fn, _wrap)
    except Exception as ex:
        say("cannot wrap", _fn, ex)


def post(path, obj=None):
    req = urllib.request.Request(URL + path, data=json.dumps(obj or {}).encode(), method="POST")
    with urllib.request.urlopen(req) as r:
        return r.read()


def server_status():
    with urllib.request.urlopen(URL + "_status") as r:
        return json.load(r)


def browser():
    win = bpy.context.window_manager.windows[0]
    for area in win.screen.areas:
        if area.type == 'FILE_BROWSER' and area.ui_type == 'ASSETS':
            return win, area, next(r for r in area.regions if r.type == 'WINDOW')
    return None


def override(**kw):
    win, area, region = browser()
    return bpy.context.temp_override(window=win, area=area, region=region, space_data=area.spaces.active, **kw)


def all_assets():
    browser()[1].tag_redraw()
    with override():
        bpy.ops.file.select_all(action='SELECT')
        return list(bpy.context.selected_assets or [])


def asset_report(tag):
    rows = []
    for a in all_assets():
        with override(selected_assets=[a]):
            needs = bpy.ops.asset.assets_download.poll()
        rows.append(f"{a.id_type}:{a.name} online={a.is_online} needs_dl={needs} "
                    f"lib={Path(a.full_library_path).name if a.full_library_path else ''} local_id={a.local_id}")
    say(f"--- assets [{tag}] ({len(rows)})")
    for r in sorted(rows):
        say("    ", r)
    return rows


def page():
    p = LIBDIR / "_v1" / "assets-00000.json"
    return json.loads(p.read_text()) if p.exists() else None


def listed(rel):
    pg = page()
    if not pg:
        return None, None
    f = next((f for f in pg["files"] if f["path"] == rel), None)
    a = next((a for a in pg["assets"] if rel in a["files"]), None)
    return f, a


def disk_vs_listing(rel):
    p = LIBDIR / rel
    f, a = listed(rel)
    ainfo = f"{a['id_type']}:{a['name']}" if a else "?"
    if not p.exists():
        return f"{rel}: NOT on disk; listing {ainfo} hash={f and f['hash'][7:19]}"
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    eq = f and f["hash"][7:] == h and f["size_in_bytes"] == p.stat().st_size
    return (f"{rel}: disk sha={h[:12]} size={p.stat().st_size}; listing {ainfo} sha={f['hash'][7:19]} "
            f"size={f['size_in_bytes']} -> {'EQUAL' if eq else 'DIFFERENT'}")


def parts():
    return sorted((str(p.relative_to(LIBDIR)), p.stat().st_size) for p in LIBDIR.glob("blend/**/*.part"))


def http_meta_files():
    out = []
    for p in LIBDIR.rglob("*"):
        if p.is_file() and len(p.name) == 64 and "_v1" not in p.parts:
            try:
                out.append((str(p.relative_to(LIBDIR))[:40], json.loads(p.read_text())))
            except Exception:
                pass
    return out


def wait_for(cond, timeout, step=0.5):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        yield step
    return False


def setup():
    prefs = bpy.context.preferences
    global LIBDIR
    lib = prefs.filepaths.asset_libraries.get(LIBNAME)
    if lib is None:
        say("asset_library_add ->", bpy.ops.preferences.asset_library_add(type='REMOTE', name=LIBNAME, remote_url=URL))
        lib = prefs.filepaths.asset_libraries.get(LIBNAME)
    LIBDIR = Path(lib.path)
    say("library", lib.name, lib.path, lib.remote_url)
    win = bpy.context.window_manager.windows[0]
    area = max(win.screen.areas, key=lambda a: a.width * a.height)
    area.ui_type = 'ASSETS'
    yield 0.5
    area.spaces.active.params.asset_library_reference = LIBNAME
    yield 0.5
    yield from wait_for(lambda: page() is not None, 30)
    t0 = time.time()
    while time.time() - t0 < 30 and not all_assets():
        if time.time() - t0 > 8:
            with override():
                bpy.ops.asset.library_refresh()
        yield 1.0


def download(rel_identifier):
    try:
        r = bpy.ops.asset.asset_download(asset_library_type='CUSTOM', asset_library_identifier=LIBNAME,
                                         relative_asset_identifier=rel_identifier)
        say("asset_download", rel_identifier, "->", r)
    except Exception as ex:
        say("asset_download", rel_identifier, "FAILED:", str(ex).strip())


def reload_listing(tag):
    with override():
        say(f"reload_listing [{tag}] ->", bpy.ops.asset.library_reload_listing())


def info_reports():
    """Copy the Info editor reports (what the user sees in the status bar / Info log)."""
    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.ui_type != 'ASSETS')
    old = area.ui_type
    area.ui_type = 'INFO'
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.info.select_all(action='SELECT')
        bpy.ops.info.report_copy()
    area.ui_type = old
    return bpy.context.window_manager.clipboard


CUBE = "blend/fake/fake_cube_01.blend"
MONKEY = "blend/fake/fake_monkey_01.blend"
MISNAME = "blend/mismatch/mis_name_a01.blend"


def scenario_long():
    yield from setup()
    asset_report("listing")
    say(disk_vs_listing(CUBE))
    st = server_status()
    say("server cfg", st["cfg"])

    t_dl = time.time()
    download(CUBE + "/Collection/fake_cube_01")
    yield 3.0
    download(MONKEY + "/Collection/fake_monkey_01")       # queued behind the cube; will fail mid-stream
    yield 1.0
    f, a = listed(MISNAME)
    download(f"{MISNAME}/{'Collection' if a['id_type'] == 'COLLECTION' else 'Object'}/{a['name']}")
    yield 2.0
    asset_report("while downloading")
    reloaded = False
    while not (LIBDIR / CUBE).exists() and time.time() - t_dl < 900:
        el = time.time() - t_dl
        if not reloaded and el > 60:
            # exp 1d: listing reload while the long download is in flight
            gen = server_status()["generation"]
            reload_listing("during long download")
            reloaded = True
            yield 5.0
            say("after mid-download reload: server generation", gen, "listing files:",
                [(x["path"].split("/")[-1], x["hash"][7:15]) for x in page()["files"]])
            asset_report("after mid-download reload")
        if int(el) % 30 == 0:
            say(f"waiting cube: {el:.0f}s parts={parts()}")
        yield 1.0
    say(f"cube landed after {time.time() - t_dl:.1f}s:", (LIBDIR / CUBE).exists())
    # wait for the queue (monkey fails, mis_name succeeds)
    yield from wait_for(lambda: (LIBDIR / MISNAME).exists(), 200)
    yield 5.0
    say("parts left:", parts())
    say("monkey on disk:", (LIBDIR / MONKEY).exists(), "| misname on disk:", (LIBDIR / MISNAME).exists())
    asset_report("after queue done (before reload)")
    for rel in (CUBE, MONKEY, MISNAME):
        say(disk_vs_listing(rel))
    reload_listing("after downloads")
    yield from wait_for(lambda: "EQUAL" in disk_vs_listing(CUBE), 30)
    yield 3.0
    for rel in (CUBE, MONKEY, MISNAME):
        say(disk_vs_listing(rel))
    asset_report("after reload")
    say("http metadata files:", http_meta_files())

    # retry the failed monkey with the conversion now working
    post("_admin/config", {"fail": []})
    download(MONKEY + "/Collection/fake_monkey_01")
    ok = yield from wait_for(lambda: (LIBDIR / MONKEY).exists(), 120)
    say("monkey retry landed:", ok)
    yield 2.0
    reload_listing("after monkey retry")
    yield from wait_for(lambda: "EQUAL" in disk_vs_listing(MONKEY), 30)
    yield 3.0
    say(disk_vs_listing(MONKEY))
    asset_report("final")

    with bpy.data.libraries.load(str(LIBDIR / CUBE), link=False, assets_only=True) as (src, dst):
        dst.collections = [c for c in src.collections if c == "fake_cube_01"]
    coll = dst.collections[0]
    bpy.context.scene.collection.children.link(coll)
    say("appended", coll.name, [o.name for o in coll.objects])
    try:
        say("INFO REPORTS:\n" + info_reports())
    except Exception as ex:
        say("info reports unavailable:", ex)


MIS = {
    "type": "blend/mismatch/mis_type_a01.blend",
    "name": "blend/mismatch/mis_name_a01.blend",
    "rev": "blend/mismatch/mis_rev_a01.blend",
}
GROUP = {"OBJECT": "Object", "COLLECTION": "Collection"}


def try_append(tag, rel, id_type, name):
    """Append by (group, name): what the drop/append code does with the asset identifier."""
    path = LIBDIR / rel
    before = {(c.name) for c in bpy.data.collections} | {o.name for o in bpy.data.objects}
    try:
        r = bpy.ops.wm.append(directory=str(path) + "/" + GROUP[id_type] + "/", filename=name,
                              instance_collections=False)
    except Exception as ex:
        r = f"EXC {str(ex).strip()[:200]}"
    after = {(c.name) for c in bpy.data.collections} | {o.name for o in bpy.data.objects}
    say(f"append[{tag}] {GROUP[id_type]}/{name} from {path.name} -> {r} new={sorted(after - before)}")


def file_content(rel):
    with bpy.data.libraries.load(str(LIBDIR / rel), assets_only=True) as (src, _dst):
        return {"objects": list(src.objects), "collections": list(src.collections)}


def scenario_mismatch():
    yield from setup()
    post("_admin/config", {"keepalive": "gzip", "interval": 5, "fail": []})
    asset_report("listing (predicted)")
    pred = {}
    for k, rel in MIS.items():
        f, a = listed(rel)
        pred[k] = (a["id_type"], a["name"])
        download(f"{rel}/{GROUP[a['id_type']]}/{a['name']}")
    ok = yield from wait_for(lambda: all((LIBDIR / r).exists() for r in MIS.values()), 120)
    say("all mismatch files downloaded:", ok)
    yield 4.0
    asset_report("after download, listing still predicted")
    for k, rel in MIS.items():
        say(k, "file contains", file_content(rel), "| listing says", pred[k])
        try_append(f"{k}-listed", rel, *pred[k])
    # same check from the operator the browser uses for the tile button
    for a in all_assets():
        if a.name.startswith("mis_"):
            with override(selected_assets=[a]):
                say(f"  {a.id_type}:{a.name} assets_download.poll={bpy.ops.asset.assets_download.poll()} "
                    f"full_path={a.full_path}")
    reload_listing("server corrected the listing")
    yield from wait_for(lambda: all("EQUAL" in disk_vs_listing(r) for r in MIS.values()), 30)
    yield 4.0
    for rel in MIS.values():
        say(disk_vs_listing(rel))
    asset_report("after reload (corrected listing)")
    with override():
        say("library_refresh ->", bpy.ops.asset.library_refresh())
    yield 4.0
    asset_report("after library_refresh")
    for k, rel in MIS.items():
        f, a = listed(rel)
        try_append(f"{k}-corrected", rel, a["id_type"], a["name"])
    for a in all_assets():
        if a.name.startswith("mis_"):
            with override(selected_assets=[a]):
                say(f"  {a.id_type}:{a.name} assets_download.poll={bpy.ops.asset.assets_download.poll()} "
                    f"full_path={a.full_path}")


def scenario_status():
    yield from setup()
    yield 3.0
    asset_report("after restart")
    for rel in list(MIS.values()) + [CUBE, MONKEY]:
        say(disk_vs_listing(rel))
    for a in all_assets():
        if a.name.startswith("mis_"):
            with override(selected_assets=[a]):
                say(f"  {a.id_type}:{a.name} needs_dl={bpy.ops.asset.assets_download.poll()} full_path={a.full_path}")


def run():
    gen = {"long": scenario_long, "mismatch": scenario_mismatch, "status": scenario_status}[SCEN]()

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
