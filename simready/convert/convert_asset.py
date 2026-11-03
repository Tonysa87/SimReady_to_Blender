# SPDX-License-Identifier: GPL-3.0-or-later
"""Convert one downloaded nvidia/simready-assets package to an Asset Browser .blend.

Run inside Blender (headless):

  blender -b --factory-startup --python simready/convert/convert_asset.py -- \
      --asset-dir cache/raw/<asset> --out out/<asset>.blend [--max-res 0|2048|1024] \
      [--catalog-id UUID] [--tags a,b] [--report out/<asset>.json] [--normal-flip auto|on|off] \
      [--color-jpeg]

Steps: disable physics variants (wrapper layer) -> native USD import with our
material hook -> single mesh => Object asset, several => Collection asset ->
downscale + pack textures -> asset metadata + embedded preview -> save.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pxr import Sdf, Usd, UsdGeom  # noqa: E402

from simready import common  # noqa: E402
from simready.convert import blend_normalize, usd_hook  # noqa: E402

PREVIEW_SIZE = 256
PHYSICS_VARIANT_OFF = "Disabled"


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--asset-id", default="")
    ap.add_argument("--max-res", type=int, default=0, help="0 = original resolution")
    ap.add_argument("--color-jpeg", action="store_true",
                    help="pack opaque color (sRGB) textures as JPEG q95 when that is clearly smaller")
    ap.add_argument("--catalog-id", default="")
    ap.add_argument("--tags", default="")
    ap.add_argument("--report", type=Path)
    ap.add_argument("--root", default="", help="root USD relative to --asset-dir (default: from root_usds.json)")
    ap.add_argument("--preview", type=Path, help="preview image to embed (default: the NVIDIA .thumbs PNG)")
    ap.add_argument("--no-hook", action="store_true", help="native USD material import only (for comparison)")
    ap.add_argument("--normal-flip", choices=("auto", "on", "off"), default="auto",
                    help="override the MDL flip_tangent_v (for visual tests)")
    return ap.parse_args(argv)


# --------------------------------------------------------------------------- USD


def inside(path: str | Path, base: Path) -> bool:
    """True if `path` resolves to a location inside `base` (downloaded data is untrusted)."""
    try:
        return Path(path).resolve().is_relative_to(base.resolve())
    except (OSError, ValueError):
        return False


def root_usd(asset_dir: Path, rel: str = "") -> Path:
    if not rel:
        rel = json.loads((asset_dir / ".metadata/com.nvidia.simready.root_usds.json").read_text())["entries"][0]
    root = asset_dir / rel
    if not inside(root, asset_dir):
        raise RuntimeError(f"root USD outside the asset folder: {rel!r}")
    return root


def write_wrapper(root: Path, tmpdir: Path, info: dict) -> Path:
    """A tiny layer referencing the asset with all physics variants disabled."""
    src = Usd.Stage.Open(str(root), Usd.Stage.LoadNone)  # metadata only: no physics payloads
    dp = src.GetDefaultPrim()
    info["stage"] = {
        "defaultPrim": dp.GetName(),
        "metersPerUnit": UsdGeom.GetStageMetersPerUnit(src),
        "upAxis": UsdGeom.GetStageUpAxis(src),
        "variants_original": {n: dp.GetVariantSets().GetVariantSet(n).GetVariantSelection()
                              for n in dp.GetVariantSets().GetNames()},
    }
    meta = src.GetRootLayer().customLayerData.get("SimReady_Metadata")
    if meta:
        info["simready_metadata"] = {k: (v if isinstance(v, (str, int, float, bool)) else str(v))
                                     for k, v in dict(meta).items()}

    path = tmpdir / "wrapper.usda"
    layer = Sdf.Layer.CreateNew(str(path))
    stage = Usd.Stage.Open(layer)
    prim = stage.DefinePrim(dp.GetPath(), dp.GetTypeName())
    prim.GetReferences().AddReference(str(root))
    for name in prim.GetVariantSets().GetNames():
        vs = prim.GetVariantSets().GetVariantSet(name)
        if PHYSICS_VARIANT_OFF in vs.GetVariantNames():
            vs.SetVariantSelection(PHYSICS_VARIANT_OFF)
    stage.SetDefaultPrim(prim)
    # De-instance everything: Blender's material-hook discovery uses
    # stage->Traverse(), which does not enter instance proxies, and the
    # SimReady visual materials live inside instanceable parts.
    deinstanced = 0
    while True:
        found = [p for p in stage.Traverse() if p.IsInstanceable()]
        if not found:
            break
        for p in found:
            p.SetInstanceable(False)
        deinstanced += len(found)
    info["stage"]["deinstanced_prims"] = deinstanced
    UsdGeom.SetStageMetersPerUnit(stage, info["stage"]["metersPerUnit"])
    UsdGeom.SetStageUpAxis(stage, info["stage"]["upAxis"])
    layer.Save()
    return path


# ---------------------------------------------------------------------- images


class ImageCache:
    def __init__(self, asset_dir: Path, info: dict) -> None:
        self.images: dict[tuple[str, str], bpy.types.Image] = {}
        self.asset_dir = asset_dir
        self.info = info

    def __call__(self, path: str, colorspace: str) -> bpy.types.Image | None:
        key = (path, colorspace)
        if key in self.images:
            return self.images[key]
        if not inside(path, self.asset_dir):
            self.info["warnings"].append(f"texture outside the asset folder rejected: {path}")
            return None
        try:
            img = bpy.data.images.load(path, check_existing=False)
        except RuntimeError:
            return None
        img.colorspace_settings.name = colorspace  # name = NVIDIA file name (Blender adds .001 if loaded twice)
        self.images[key] = img
        return img


JPEG_QUALITY = 95          # 4:4:4; on SimReady albedos: median PSNR 46 dB, ~27% of the PNG (docs/fase5-report.md)
JPEG_WORTH = 0.8           # keep the PNG unless the JPEG is at most 80% of it (flat textures compress better as PNG)


def _color_jpeg(src: Path, size: tuple[int, int], png_bytes: int, tmp: Path) -> Path | None:
    """`src` as a JPEG of `size` in `tmp`, or None if it is not worth it: real transparency,
    or not clearly smaller than `png_bytes` (the PNG that would be packed). Written from the
    pixels only (no metadata), so the same input gives the same bytes."""
    import OpenImageIO as oiio
    buf = oiio.ImageBuf(str(src))
    spec = buf.spec()
    if spec.nchannels not in (3, 4) or buf.has_error:
        return None
    if spec.nchannels == 4 and oiio.ImageBufAlgo.computePixelStats(buf).min[3] < 1.0:
        return None
    if (spec.width, spec.height) != size:
        buf = oiio.ImageBufAlgo.resize(buf, roi=oiio.ROI(0, size[0], 0, size[1], 0, 1, 0, spec.nchannels))
    px = buf.get_pixels(oiio.UINT8)[..., :3]
    out_spec = oiio.ImageSpec(size[0], size[1], 3, oiio.UINT8)
    out_spec.attribute("Compression", f"jpeg:{JPEG_QUALITY}")
    out_spec.attribute("jpeg:subsampling", "4:4:4")
    path = tmp / (src.stem + ".jpg")
    out = oiio.ImageOutput.create(str(path))
    if not out or not out.open(str(path), out_spec) or not out.write_image(px):
        return None
    out.close()
    if path.stat().st_size > JPEG_WORTH * png_bytes:
        path.unlink()
        return None
    return path


def process_images(max_res: int, asset_dir: Path, info: dict, color_jpeg: bool = False) -> None:
    """Downscale file images above max_res and pack everything into the .blend.

    With `color_jpeg`, opaque color textures (sRGB: albedo, emission) are packed as JPEG
    when that is clearly smaller; data textures (normal, ORM, opacity) always stay PNG.
    Images whose file lies outside the asset folder (e.g. loaded by the native
    importer from a crafted absolute path) are removed, never packed.
    """
    with tempfile.TemporaryDirectory(prefix="simready_tex_") as tmp:
        _process_images(max_res, asset_dir, info, color_jpeg, Path(tmp))


def _process_images(max_res: int, asset_dir: Path, info: dict, color_jpeg: bool, tmp: Path) -> None:
    out = []
    for img in list(bpy.data.images):
        if img.source != 'FILE' or img.packed_file:
            continue
        if not inside(bpy.path.abspath(img.filepath), asset_dir):
            info["warnings"].append(f"image outside the asset folder removed: {img.filepath}")
            bpy.data.images.remove(img)
            continue
        if not Path(bpy.path.abspath(img.filepath)).exists():
            info["warnings"].append(f"missing image file: {img.filepath}")
            continue
        w, h = img.size
        entry = {"name": img.name, "file": Path(img.filepath).name, "orig": [w, h],
                 "colorspace": img.colorspace_settings.name, "bytes_orig": Path(bpy.path.abspath(img.filepath)).stat().st_size}
        new = (w, h)
        if max_res and max(w, h) > max_res:
            f = max_res / max(w, h)
            new = (max(1, round(w * f)), max(1, round(h * f)))
        src = Path(bpy.path.abspath(img.filepath))
        jpg = None
        if color_jpeg and img.colorspace_settings.name == "sRGB":
            # PNG size it replaces: the original file, or about that per pixel once reduced
            png_bytes = entry["bytes_orig"] * (new[0] * new[1]) / max(1, w * h)
            try:
                jpg = _color_jpeg(src, new, png_bytes, tmp)
            except Exception as e:  # unreadable for OIIO: keep Blender's PNG path
                info["warnings"].append(f"JPEG skipped for {img.name}: {e}")
        if jpg:
            img.filepath = str(jpg)
            img.reload()
            img.pack()
            # same place as the PNG, not the (random) temp folder: keeps the file reproducible
            img.filepath_raw = str(src.with_suffix(".jpg"))
            for pf in img.packed_files:
                pf.filepath = img.filepath_raw
            entry["jpeg"] = True
            if new != (w, h):
                entry["new"] = list(new)
        else:
            if new != (w, h):
                img.scale(*new)
                entry["new"] = list(img.size)
            img.pack()
        entry["bytes_packed"] = img.packed_file.size if img.packed_file else None
        out.append(entry)
    info["textures"] = out


# ---------------------------------------------------------------------- assets


_BL_SUFFIX = re.compile(r"\.\d{3}$")


def restore_nvidia_names(ids) -> None:
    """Drop Blender's '.001' suffixes where the original NVIDIA name is free again
    (e.g. after removing the Xform empty that shared the mesh prim's name)."""
    for id_ in sorted(ids, key=lambda i: i.name):
        base = _BL_SUFFIX.sub("", id_.name)
        if base != id_.name and base not in _collection_of(id_):
            id_.name = base


def _collection_of(id_):
    return bpy.data.objects if isinstance(id_, bpy.types.Object) else bpy.data.meshes


def organize(asset_id: str, info: dict) -> bpy.types.ID:
    """Keep only meshes (unparented, world transform preserved), NVIDIA names untouched.

    One mesh -> Object asset, object and mesh data named as the NVIDIA asset (folder);
    several -> Collection asset named as the folder, parts keep the NVIDIA mesh names.
    """
    scene = bpy.context.scene
    meshes = [o for o in bpy.data.objects if o.type == 'MESH']
    for o in meshes:
        mw = o.matrix_world.copy()
        o.parent = None
        o.matrix_world = mw
    removed = [o.name for o in bpy.data.objects if o.type != 'MESH']
    for o in [o for o in bpy.data.objects if o.type != 'MESH']:
        bpy.data.objects.remove(o)
    for c in list(bpy.data.collections):
        bpy.data.collections.remove(c)
    restore_nvidia_names(meshes)
    restore_nvidia_names({o.data for o in meshes})
    info["removed_objects"] = removed
    info["mesh_count"] = len(meshes)

    if len(meshes) == 1:
        ob = meshes[0]
        info["mesh_name"] = ob.data.name if ob.data else ""  # original NVIDIA mesh prim name (report only)
        # Named as the NVIDIA asset: mesh names like "body_01_mesh_00" say nothing (Tonysa87, 1/11/2026).
        ob.name = asset_id
        if ob.data and ob.data.users == 1:
            ob.data.name = asset_id
        for c in list(ob.users_collection):
            c.objects.unlink(ob)
        scene.collection.objects.link(ob)
        info["asset_type"] = "OBJECT"
        info["asset_name"] = ob.name
        return ob

    coll = bpy.data.collections.new(asset_id)
    scene.collection.children.link(coll)
    for o in meshes:
        for c in list(o.users_collection):
            c.objects.unlink(o)
        coll.objects.link(o)
    info["asset_type"] = "COLLECTION"
    info["asset_name"] = coll.name
    return coll


def set_metadata(id_: bpy.types.ID, asset_id: str, catalog_id: str, tags: list[str], info: dict) -> None:
    id_.asset_mark()
    ad = id_.asset_data
    ad.author = common.AUTHOR
    ad.license = common.LICENSE
    ad.copyright = common.COPYRIGHT
    ad.description = common.asset_description(asset_id)
    if catalog_id:
        ad.catalog_id = catalog_id
    for t in tags:
        if t:
            ad.tags.new(t, skip_if_exists=True)


def set_preview(id_: bpy.types.ID, thumb: Path, info: dict) -> None:
    if not thumb.exists():
        info["warnings"].append(f"thumbnail not found: {thumb}")
        return
    img = bpy.data.images.load(str(thumb))
    img.scale(PREVIEW_SIZE, PREVIEW_SIZE)
    pixels = [0.0] * (PREVIEW_SIZE * PREVIEW_SIZE * 4)
    img.pixels.foreach_get(pixels)
    prev = id_.preview_ensure()
    prev.image_size = (PREVIEW_SIZE, PREVIEW_SIZE)
    prev.image_pixels_float.foreach_set(pixels)
    bpy.data.images.remove(img)


# ------------------------------------------------------------------------ main


def save_reproducible(path: Path, info: dict) -> None:
    """Save compressed and byte-for-byte reproducible (same input -> same hash, see
    blend_normalize). Falls back to Blender's own compressed save if that fails."""
    raw = path.with_suffix(".raw")
    t = time.time()
    try:
        bpy.ops.wm.save_as_mainfile(filepath=str(raw), compress=False, copy=True)
        info["normalize"] = blend_normalize.normalize(raw)
        blend_normalize.compress(raw, path)
    except Exception as e:
        info["warnings"].append(f"reproducible save failed, plain save used: {e}")
        bpy.ops.wm.save_as_mainfile(filepath=str(path), compress=True, copy=True)
    finally:
        raw.unlink(missing_ok=True)
    info["timings"]["save"] = round(time.time() - t, 2)


def main() -> int:
    args = parse_args()
    asset_dir = args.asset_dir.resolve()
    asset_id = args.asset_id or asset_dir.name
    info: dict = {"asset": asset_id, "max_res": args.max_res, "color_jpeg": args.color_jpeg, "warnings": [], "timings": {}}
    t0 = time.time()

    bpy.ops.wm.read_factory_settings(use_empty=True)
    images = ImageCache(asset_dir, info)
    usd_hook.image_loader = images
    usd_hook.report = {"hook": [], "native": []}
    usd_hook.normal_flip_override = None if args.normal_flip == "auto" else args.normal_flip == "on"
    if not args.no_hook:
        usd_hook.register()

    try:
        with tempfile.TemporaryDirectory(prefix="simready_") as tmp:
            wrapper = write_wrapper(root_usd(asset_dir, args.root), Path(tmp), info)
            t = time.time()
            res = bpy.ops.wm.usd_import(
                filepath=str(wrapper),
                support_scene_instancing=False,   # real meshes, no instance empties
                import_visible_only=True,
                import_guide=False, import_proxy=False, import_render=True,
                import_cameras=False, import_lights=False,
                import_all_materials=False,
                import_usd_preview=True,          # fallback for non-MDL materials
                mtl_purpose='MTL_FULL',
                mtl_name_collision_mode='REFERENCE_EXISTING',  # dedupe per-prototype copies
                import_textures_mode='IMPORT_NONE',            # we pack ourselves
                property_import_mode='NONE',
                create_world_material=False,
                set_frame_range=False,
            )
            info["timings"]["usd_import"] = round(time.time() - t, 2)
            if res != {'FINISHED'}:
                raise RuntimeError(f"usd_import returned {res}")
    finally:
        if not args.no_hook:
            usd_hook.unregister()

    info["materials"] = {"hook": usd_hook.report["hook"],
                         "native": sorted(set(usd_hook.report["native"]))}
    asset = organize(asset_id, info)

    t = time.time()
    process_images(args.max_res, asset_dir, info, color_jpeg=args.color_jpeg)
    info["timings"]["textures"] = round(time.time() - t, 2)

    tags = [t for t in args.tags.split(",") if t] or common.asset_tags(asset_id)
    set_metadata(asset, asset_id, args.catalog_id, tags, info)
    root = root_usd(asset_dir, args.root)
    set_preview(asset, args.preview or root.parent / ".thumbs/256x256" / (root.name + ".png"), info)

    bpy.data.orphans_purge(do_recursive=True)
    info["dimensions_m"] = {o.name: [round(v, 4) for v in o.dimensions] for o in bpy.data.objects}

    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = args.out.with_suffix(".blend.tmp")
    save_reproducible(tmp_out, info)
    tmp_out.replace(args.out)
    info["out_bytes"] = args.out.stat().st_size
    info["timings"]["total"] = round(time.time() - t0, 2)

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(info, indent=1, default=str))
    print("SIMREADY_CONVERT_OK", json.dumps({k: info[k] for k in ("asset", "asset_type", "asset_name", "mesh_count", "out_bytes", "timings")}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        print("SIMREADY_CONVERT_FAILED")
        sys.exit(1)
