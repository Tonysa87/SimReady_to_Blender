# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase-3 stand-in converter: can produce an OBJECT or a COLLECTION asset.

blender -b --factory-startup --python fake_convert2.py -- \
    <out.blend> <id_type OBJECT|COLLECTION> <asset_name> <mesh_name> <kind cube|monkey> \
    <variant> <catalog_uuid> <thumbnail.png> <tags,comma>

OBJECT:     one mesh object named <asset_name> marked as asset (mesh_name ignored).
COLLECTION: collection <asset_name> marked as asset, holding object <mesh_name>.
"""

import sys
from pathlib import Path

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
out, id_type, asset_name, mesh_name, kind = Path(argv[0]), argv[1], argv[2], argv[3], argv[4]
variant, catalog_id, thumb, tags = int(argv[5]), argv[6], Path(argv[7]), argv[8].split(",")

bpy.ops.wm.read_factory_settings(use_empty=True)
if kind == "cube":
    bpy.ops.mesh.primitive_cube_add(size=0.1 + 0.01 * variant)
else:
    bpy.ops.mesh.primitive_monkey_add(size=0.2 + 0.01 * variant)
obj = bpy.context.active_object

if id_type == "OBJECT":
    obj.name = asset_name
    asset_id = obj
else:
    obj.name = mesh_name
    coll = bpy.data.collections.new(asset_name)
    bpy.context.scene.collection.children.link(coll)
    for c in obj.users_collection:
        c.objects.unlink(obj)
    coll.objects.link(obj)
    asset_id = coll

asset_id.asset_mark()
ad = asset_id.asset_data
ad.author = "NVIDIA"
ad.license = "CC-BY-4.0"
ad.description = f"phase-3 fake {id_type} {asset_name} (variant {variant})"
ad.catalog_id = catalog_id
for t in tags:
    ad.tags.new(t)
if thumb.exists():
    img = bpy.data.images.load(str(thumb))
    w, h = img.size
    px = [0.0] * (w * h * 4)
    img.pixels.foreach_get(px)
    prev = asset_id.preview_ensure()
    prev.image_size = (w, h)
    prev.image_pixels_float.foreach_set(px)
    bpy.data.images.remove(img)

out.parent.mkdir(parents=True, exist_ok=True)
tmp = out.with_suffix(".blend.tmp")
bpy.ops.wm.save_as_mainfile(filepath=str(tmp), compress=True, copy=True)
tmp.replace(out)
print("FAKE_CONVERT2_OK", out, id_type, asset_name)
