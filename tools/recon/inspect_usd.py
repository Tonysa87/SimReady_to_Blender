import sys, json, collections
from pxr import Usd, UsdGeom, UsdShade, Sdf, Kind
args=sys.argv[sys.argv.index("--")+1:]
def val(v):
    if isinstance(v,Sdf.AssetPath): return "@"+v.path+"@"
    s=str(v); return s if len(s)<80 else s[:80]+"..."
for path in args:
    st=Usd.Stage.Open(path, Usd.Stage.LoadAll)
    out={"file":path,"metersPerUnit":UsdGeom.GetStageMetersPerUnit(st),"upAxis":UsdGeom.GetStageUpAxis(st),
         "defaultPrim":str(st.GetDefaultPrim().GetPath()) if st.GetDefaultPrim() else None}
    dp=st.GetDefaultPrim(); out["kind"]=Usd.ModelAPI(dp).GetKind() if dp else None
    out["layers"]=[l.identifier.split("simready_usd/")[-1] for l in st.GetUsedLayers()]
    vs={}; types=collections.Counter(); inst=[]; pis=[]; arcs=collections.Counter(); purposes=collections.Counter(); invis=0
    prims=list(st.Traverse(Usd.TraverseInstanceProxies()))
    for p in st.TraverseAll():
        for n in p.GetVariantSets().GetNames():
            v=p.GetVariantSets().GetVariantSet(n); vs[f"{p.GetPath()}:{n}"]=(v.GetVariantNames(),v.GetVariantSelection())
        if p.IsInstanceable(): inst.append(str(p.GetPath()))
        if p.IsA(UsdGeom.PointInstancer): pis.append(str(p.GetPath()))
        if p.HasAuthoredPayloads(): arcs["payload"]+=1
        if p.HasAuthoredReferences(): arcs["reference"]+=1
    for p in prims:
        types[p.GetTypeName()]+=1
        if p.IsA(UsdGeom.Imageable):
            pu=UsdGeom.Imageable(p).GetPurposeAttr().Get(); purposes[pu]+=1
    out.update(variantSets=vs, instanceable_count=len(inst), instanceable_sample=inst[:3], pointInstancers=pis, arcs=dict(arcs),
               prim_count_with_proxies=len(prims), prim_count_no_proxies=len(list(st.TraverseAll())), types=dict(types), purposes=dict(purposes),
               prototypes=len(st.GetPrototypes()))
    meshes=[p for p in prims if p.IsA(UsdGeom.Mesh)]
    m0=[]
    for m in meshes[:400]:
        mesh=UsdGeom.Mesh(m); pv=UsdGeom.PrimvarsAPI(m)
        m0.append((str(m.GetPath()).split("/")[-1], len(mesh.GetPointsAttr().Get() or []), [x.GetName() for x in pv.GetPrimvars()], UsdGeom.Imageable(m).GetPurposeAttr().Get(),
                   str(UsdShade.MaterialBindingAPI(m).ComputeBoundMaterial()[0].GetPath()), UsdGeom.Mesh(m).GetSubdivisionSchemeAttr().Get(),
                   len(UsdGeom.Subset.GetAllGeomSubsets(mesh))))
    out["mesh_count"]=len(meshes); out["mesh_sample"]=m0[:6]
    out["mesh_purposes"]=dict(collections.Counter(x[3] for x in m0)); out["mesh_primvars"]=dict(collections.Counter(",".join(sorted(x[2])) for x in m0))
    bb=UsdGeom.BBoxCache(Usd.TimeCode.Default(),["default","render"]).ComputeWorldBound(dp).ComputeAlignedRange()
    out["bbox_size"]=[round(x,4) for x in bb.GetSize()]
    mats={}
    seen=set()
    for p in st.Traverse(Usd.TraverseInstanceProxies(Usd.PrimAllPrimsPredicate)):
        if not p.IsA(UsdShade.Material): continue
        mat=UsdShade.Material(p); md={"outputs":{}, "shaders":{}, "colorSpace":p.GetAttribute("colorSpace:name").Get() if p.HasAttribute("colorSpace:name") else None}
        for o in mat.GetOutputs():
            src=o.GetConnectedSources()[0]
            md["outputs"][o.GetBaseName()]=[str(s.source.GetPath())+"."+s.sourceName for s in src]
        for c in Usd.PrimRange(p, Usd.TraverseInstanceProxies(Usd.PrimAllPrimsPredicate)):
            if not c.IsA(UsdShade.Shader): continue
            sh=UsdShade.Shader(c); d={}
            for a in ["info:id","info:implementationSource","info:mdl:sourceAsset","info:mdl:sourceAsset:subIdentifier","info:sourceAsset"]:
                at=c.GetAttribute(a)
                if at and at.HasAuthoredValue(): d[a]=val(at.Get())
            ins={}
            for i in sh.GetInputs():
                src=i.GetConnectedSources()[0]
                if src: ins[i.GetBaseName()]="->"+src[0].source.GetPath().name+"."+src[0].sourceName
                else:
                    v=i.Get(); cs=i.GetAttr().GetColorSpace()
                    ins[i.GetBaseName()]=val(v)+(f" [cs={cs}]" if cs else "")
            d["inputs"]=ins; d["outputs"]=[o.GetBaseName() for o in sh.GetOutputs()]
            md["shaders"][c.GetName()]=d
        md["apiSchemas"]=list(p.GetAppliedSchemas()); mats[str(p.GetPath())]=md
    out["material_count"]=len(mats)
    # dedupe material signatures
    sig=collections.Counter()
    for k,md in mats.items():
        sig[json.dumps({n:(d.get("info:id"),sorted(d["inputs"].keys())) for n,d in md["shaders"].items()},sort_keys=True)]+=1
    out["material_signatures"]=[(c,json.loads(s)) for s,c in sig.most_common()]
    bysig={}
    for k,md in mats.items():
        if not md["shaders"]: continue
        sg=json.dumps({n:(d.get("info:id"),sorted(d["inputs"].keys())) for n,d in md["shaders"].items()},sort_keys=True)
        bysig.setdefault(sg,(k,md))
    out["material_examples"]=dict(bysig.values())
    out["physmat_example"]=next(({"path":k,"schemas":md["apiSchemas"]} for k,md in mats.items() if not md["shaders"]),None)
    print("JSONOUT"+json.dumps(out,default=str))
