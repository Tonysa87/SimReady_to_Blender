import bpy, sys, json, time, collections, os
from mathutils import Vector
args=sys.argv[sys.argv.index("--")+1:]
mode=args[0]; files=args[1:]
OPTS={"default":{}, 
      "alt":dict(support_scene_instancing=False, import_all_materials=True, mtl_purpose="MTL_PREVIEW", import_guide=True, create_collection=True, import_textures_mode="IMPORT_NONE"),
      "full":dict(mtl_purpose="MTL_FULL", import_textures_mode="IMPORT_NONE"),
      "allpurpose":dict(mtl_purpose="MTL_ALL_PURPOSE", import_textures_mode="IMPORT_NONE", import_visible_only=False, import_proxy=True)}[mode]
def world_bbox(objs):
    mn=Vector((1e9,)*3); mx=Vector((-1e9,)*3); n=0
    deps=bpy.context.evaluated_depsgraph_get()
    for inst in deps.object_instances:
        o=inst.object
        if o.type!="MESH": continue
        M=inst.matrix_world
        for c in o.bound_box:
            v=M@Vector(c); mn=Vector(map(min,mn,v)); mx=Vector(map(max,mx,v)); n+=1
    return [round(x,4) for x in (mx-mn)] if n else None
def tree_summary(mat):
    if not mat.node_tree: return None
    nt=mat.node_tree; nodes=collections.Counter(n.bl_idname for n in nt.nodes)
    links=[]
    for l in nt.links:
        fn=l.from_node; s=f"{fn.bl_idname.replace('ShaderNode','')}"
        if fn.bl_idname=="ShaderNodeTexImage" and fn.image: s+=f"[{os.path.basename(fn.image.filepath)}|{fn.image.colorspace_settings.name}|{fn.interpolation}|{fn.extension}]"
        links.append(f"{s}.{l.from_socket.name} -> {l.to_node.bl_idname.replace('ShaderNode','')}.{l.to_socket.name}")
    return {"nodes":dict(nodes),"links":links, "blend":getattr(mat,"surface_render_method",None)}
for f in files:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    print("=====BEGIN", os.path.basename(f), mode, flush=True)
    t=time.time(); r=bpy.ops.wm.usd_import(filepath=f, **OPTS); dt=time.time()-t
    print("=====END", flush=True)
    objs=list(bpy.data.objects)
    out={"file":os.path.basename(f),"mode":mode,"result":list(r),"secs":round(dt,2),
         "obj_types":dict(collections.Counter(o.type for o in objs)),
         "instancers":sum(1 for o in objs if o.instance_type=="COLLECTION" and o.instance_collection),
         "meshes":len(bpy.data.meshes),"collections":[c.name for c in bpy.data.collections][:15],"n_collections":len(bpy.data.collections),
         "materials":[m.name for m in bpy.data.materials],"images":[(i.name,i.colorspace_settings.name,i.packed_file is not None) for i in bpy.data.images][:12],
         "world_bbox":world_bbox(objs),
         "top_objects":[(o.name,o.type,[round(x,3) for x in o.dimensions],[round(x,3) for x in o.scale]) for o in objs if o.parent is None][:5],
         "sample_objs":[(o.name,o.type,o.parent.name if o.parent else None,[round(x,3) for x in o.dimensions], o.data.name if o.data else None, [s.material.name if s.material else None for s in o.material_slots]) for o in objs if o.type=="MESH"][:4],
         "mesh_uv_layers":dict(collections.Counter(tuple(u.name for u in m.uv_layers) for m in bpy.data.meshes).most_common()) if bpy.data.meshes else {},
         "mesh_attrs":sorted({a.name for m in bpy.data.meshes for a in m.attributes}),
         "custom_props_sample":{k:str(v)[:60] for o in objs[:3] for k,v in o.items()},
         "scene_unit":(bpy.context.scene.unit_settings.system, bpy.context.scene.unit_settings.scale_length)}
    mts={}
    for m in bpy.data.materials:
        s=tree_summary(m)
        if s: mts[m.name]=s
    sig={}
    for k,v in mts.items(): sig.setdefault(json.dumps(sorted(v["nodes"].items())),(k,v))
    out["material_trees_by_sig"]=dict(sig.values())
    out["mat_custom_props"]={k:str(v)[:80] for m in bpy.data.materials[:2] for k,v in m.items()}
    print("JSONOUT"+json.dumps({k:(list(map(str,v)) if k=="mesh_uv_layers" else v) for k,v in out.items()},default=str), flush=True)
