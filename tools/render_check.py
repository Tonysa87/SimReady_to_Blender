# SPDX-License-Identifier: GPL-3.0-or-later
"""Render a converted asset .blend for visual comparison with the NVIDIA thumbnail.

blender -b --factory-startup --python render_check.py -- --blend X.blend --out X.png
        [--res 512] [--samples 64] [--az 225] [--el 35] [--light studio|grazing]
"""

import argparse
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
ap = argparse.ArgumentParser()
ap.add_argument("--blend", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--res", type=int, default=512)
ap.add_argument("--samples", type=int, default=64)
ap.add_argument("--az", type=float, default=135.0, help="camera azimuth in degrees (0 = from -Y)")
ap.add_argument("--el", type=float, default=35.0)
ap.add_argument("--light", choices=("studio", "grazing"), default="studio")
ap.add_argument("--zoom", type=float, default=1.15)
ap.add_argument("--strength", type=float, default=2.0, help="HDRI strength")
args = ap.parse_args(argv)

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene

# Append the asset (object or collection) like a drop would.
with bpy.data.libraries.load(args.blend, link=False, assets_only=True) as (src, dst):
    dst.objects = list(src.objects)
    dst.collections = list(src.collections)
if dst.collections:
    for c in dst.collections:
        scene.collection.children.link(c)
else:
    for o in dst.objects:
        scene.collection.objects.link(o)
bpy.context.view_layer.update()

pts = [o.matrix_world @ Vector(c) for o in scene.objects if o.type == 'MESH' for c in o.bound_box]
lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
center, radius = (lo + hi) / 2, (hi - lo).length / 2

cam_data = bpy.data.cameras.new("cam")
cam_data.lens = 50
cam = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam
az, el = math.radians(args.az), math.radians(args.el)
direction = Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))
fov = 2 * math.atan(18 / cam_data.lens)
dist = radius / math.sin(fov / 2) * 1.05 / args.zoom
cam.location = center + direction * dist
cam.rotation_euler = (-direction).to_track_quat('-Z', 'Y').to_euler()
cam_data.clip_start, cam_data.clip_end = dist / 100, dist * 10

world = bpy.data.worlds.new("w")
scene.world = world
nt = world.node_tree
nt.nodes.clear()
out = nt.nodes.new("ShaderNodeOutputWorld")
bg_light = nt.nodes.new("ShaderNodeBackground")
bg_cam = nt.nodes.new("ShaderNodeBackground")
bg_cam.inputs["Color"].default_value = (0.022, 0.021, 0.021, 1)  # NVIDIA-like dark grey
mix = nt.nodes.new("ShaderNodeMixShader")
lp = nt.nodes.new("ShaderNodeLightPath")
nt.links.new(lp.outputs["Is Camera Ray"], mix.inputs["Fac"])
nt.links.new(bg_light.outputs["Background"], mix.inputs[1])
nt.links.new(bg_cam.outputs["Background"], mix.inputs[2])
nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
if args.light == "studio":
    env = nt.nodes.new("ShaderNodeTexEnvironment")
    hdri = Path(bpy.utils.system_resource('DATAFILES')) / "studiolights/world/studio.exr"
    env.image = bpy.data.images.load(str(hdri))
    nt.links.new(env.outputs["Color"], bg_light.inputs["Color"])
    bg_light.inputs["Strength"].default_value = args.strength
else:
    bg_light.inputs["Color"].default_value = (0.01, 0.01, 0.01, 1)
    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", 'SUN'))
    sun.data.energy = 4.0
    sun.rotation_euler = (math.radians(80), 0, math.radians(args.az + 90))  # low, from the side
    scene.collection.objects.link(sun)

scene.render.engine = 'CYCLES'
scene.cycles.device = 'CPU'
scene.cycles.samples = args.samples
scene.cycles.use_denoising = True
scene.render.resolution_x = scene.render.resolution_y = args.res
scene.render.film_transparent = False
scene.view_settings.view_transform = 'AgX'
scene.render.filepath = args.out
bpy.ops.render.render(write_still=True)
print("RENDER_OK", args.out)
