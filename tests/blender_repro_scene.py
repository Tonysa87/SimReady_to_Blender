# SPDX-License-Identifier: GPL-3.0-or-later
"""Helper for test_blend_normalize (runs inside Blender).

  blender -b --factory-startup --python tests/blender_repro_scene.py -- build <out.blend>
  blender -b --factory-startup --python tests/blender_repro_scene.py -- check <file.blend> <result.json>

build: a small asset-like scene (two meshes, node materials, a packed image) saved with
convert_asset.save_reproducible. check: open the file and report what a reader sees.
"""

import json
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from simready.convert.convert_asset import save_reproducible  # noqa: E402

mode, path = sys.argv[sys.argv.index("--") + 1:][:2]

if mode == "build":
    bpy.ops.wm.read_factory_settings(use_empty=True)
    gen = bpy.data.images.new("grid", 64, 64)
    gen.generated_type = 'COLOR_GRID'
    png = Path(path).with_name("albedo.png")
    gen.save(filepath=str(png))  # a real file, packed like the converter does
    bpy.data.images.remove(gen)
    img = bpy.data.images.load(str(png))
    img.name = "albedo"
    img.pack()
    n = 20  # mesh built directly (like the USD importer): BMesh operators are not reproducible
    verts = [(x, y, (x * y) % 3 * 0.1) for y in range(n) for x in range(n)]
    faces = [(y * n + x, y * n + x + 1, (y + 1) * n + x + 1, (y + 1) * n + x) for y in range(n - 1) for x in range(n - 1)]
    for i in range(2):
        me = bpy.data.meshes.new(f"part_{i}")
        me.from_pydata(verts, [], faces)
        uv = me.uv_layers.new(name="st")
        for k, loop in enumerate(me.loops):
            uv.data[k].uv = (verts[loop.vertex_index][0] / n, verts[loop.vertex_index][1] / n)
        ob = bpy.data.objects.new(f"part_{i}", me)
        ob.location.x = i * 3
        bpy.context.scene.collection.objects.link(ob)
        mat = bpy.data.materials.new(f"mat_{i}")
        nt = mat.node_tree
        bsdf = nt.nodes["Principled BSDF"]
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = img
        nmap = nt.nodes.new("ShaderNodeNormalMap")
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
        ob.data.materials.append(mat)
    info = {"warnings": [], "timings": {}}
    save_reproducible(Path(path), info)
    print("REPRO_INFO", json.dumps(info))
else:
    bpy.ops.wm.open_mainfile(filepath=path)
    out = {"objects": sorted(o.name for o in bpy.data.objects),
           "verts": sorted(len(m.vertices) for m in bpy.data.meshes),
           "images": [(i.name, bool(i.packed_file), tuple(i.size)) for i in bpy.data.images],
           "materials": {}}
    for m in bpy.data.materials:
        nt = m.node_tree
        out["materials"][m.name] = {
            "nodes": sorted(n.bl_idname for n in nt.nodes),
            "links": sorted(f"{l.from_node.bl_idname}.{l.from_socket.identifier}>{l.to_node.bl_idname}."
                            f"{l.to_socket.identifier}" for l in nt.links),
        }
    Path(sys.argv[sys.argv.index("--") + 3]).write_text(json.dumps(out))
