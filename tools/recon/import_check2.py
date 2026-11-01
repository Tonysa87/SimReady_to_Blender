import bpy, sys, os
f=sys.argv[sys.argv.index("--")+1:]
for p in f:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.wm.usd_import(filepath=p)
    sc=bpy.context.scene
    print("CHK",os.path.basename(p),"scene_children",[c.name for c in sc.collection.children],"objs_in_scene",len(sc.objects),"objs_total",len(bpy.data.objects))
    for c in bpy.data.collections:
        if c.name=="prototypes": print("CHK  prototypes users",c.users,"in scene?",c.name in sc.collection.children, "children",len(c.children))
    uv={tuple(u.name for u in m.uv_layers) for m in bpy.data.meshes}; print("CHK  uv sets",uv)
    m=bpy.data.materials.get("m_opaque__metal__paintedBlue_glossy_a01")
    if m:
        b=m.node_tree.nodes.get("Principled BSDF") or [n for n in m.node_tree.nodes if n.type=="BSDF_PRINCIPLED"][0]
        print("CHK  paintedBlue base color linked:",b.inputs["Base Color"].is_linked, [l.from_node.image.name for l in m.node_tree.links if l.to_socket==b.inputs["Base Color"]])
    g=bpy.data.materials.get("m_trans__glass__scratchedGlossy_a01")
    if g:
        b=[n for n in g.node_tree.nodes if n.type=="BSDF_PRINCIPLED"][0]
        print("CHK  glass alpha",b.inputs["Alpha"].default_value,"transmission",b.inputs["Transmission Weight"].default_value,"base",tuple(b.inputs["Base Color"].default_value))
    e=[o for o in bpy.data.objects if o.type=="EMPTY"]; print("CHK  empty sample",[(o.name,o.empty_display_type,o.instance_type) for o in e[:8]])
