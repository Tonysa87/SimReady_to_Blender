# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase-1 stand-in for the real USD -> .blend converter.

Run as:  blender -b --factory-startup --python fake_convert.py -- <asset_id> <kind> <out.blend> <variant> <catalog_uuid> <catalog_simple_name> <tags,comma,separated> <thumbnail.png>

Creates a Collection named <asset_id>, marked as asset, holding one primitive
mesh, and saves it as a compressed .blend. <variant> changes the geometry so
the resulting file (and hash) differs, to simulate a re-conversion.
"""

import sys
from pathlib import Path

import bpy


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:]
    asset_id, kind, out = argv[0], argv[1], Path(argv[2])
    variant = int(argv[3]) if len(argv) > 3 else 0
    catalog_id = argv[4] if len(argv) > 4 else ""
    catalog_simple_name = argv[5] if len(argv) > 5 else ""
    tags = argv[6].split(",") if len(argv) > 6 else ["fake"]
    thumbnail = Path(argv[7]) if len(argv) > 7 else None

    bpy.ops.wm.read_factory_settings(use_empty=True)

    if kind == "cube":
        bpy.ops.mesh.primitive_cube_add(size=0.1 + 0.01 * variant)
    else:
        bpy.ops.mesh.primitive_monkey_add(size=0.2 + 0.01 * variant)
    obj = bpy.context.active_object
    obj.name = f"{asset_id}_mesh"

    coll = bpy.data.collections.new(asset_id)
    bpy.context.scene.collection.children.link(coll)
    for c in obj.users_collection:
        c.objects.unlink(obj)
    coll.objects.link(obj)

    coll.asset_mark()
    ad = coll.asset_data
    ad.author = "NVIDIA"
    ad.license = "CC-BY-4.0"
    ad.copyright = "© NVIDIA Corporation"
    ad.description = f"Phase-1 fake asset {asset_id} (variant {variant})"
    # The asset metadata inside the .blend MUST match the listing: once the file is
    # on disk, Blender shows the asset with the metadata read from the .blend
    # (catalog, tags, ...), not the one from the listing.
    if catalog_id:
        ad.catalog_id = catalog_id
        # catalog_simple_name is read-only in RNA; Blender derives it.
    for t in tags:
        ad.tags.new(t)

    # Once downloaded, Blender shows the preview stored in the .blend, not the
    # listing thumbnail: embed the same image.
    if thumbnail and thumbnail.exists():
        img = bpy.data.images.load(str(thumbnail))
        w, h = img.size
        pixels = [0.0] * (w * h * 4)
        img.pixels.foreach_get(pixels)
        prev = coll.preview_ensure()
        prev.image_size = (w, h)
        prev.image_pixels_float.foreach_set(pixels)
        bpy.data.images.remove(img)

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".blend.tmp")
    bpy.ops.wm.save_as_mainfile(filepath=str(tmp), compress=True, copy=True)
    tmp.replace(out)
    print(f"FAKE_CONVERT_OK {out}")


main()
