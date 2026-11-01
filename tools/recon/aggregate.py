import json, os, re, sys, collections
raw=sys.argv[1]; names=json.load(open(sys.argv[2]))
C=collections.Counter(); tot=collections.Counter(); notex=[]; multiroot=[]; nothumb=[]; missing=[]; rootfmt=collections.Counter(); layout=collections.Counter()
thumbnames=collections.Counter(); sizes=[]; texext=collections.Counter(); nonroot_name=[]; hasmdl_payload=0; srcmdl=0; nomdl=[]
for n in names:
    try:
        roots=json.load(open(f"{raw}/{n}/.metadata/com.nvidia.simready.root_usds.json"))["entries"]
        bom=json.load(open(f"{raw}/{n}/.metadata/com.nvidia.simready.packaging.bom.json"))["items"]
    except Exception as e: missing.append(n); continue
    if len(roots)!=1: multiroot.append((n,roots))
    for r in roots:
        rootfmt[os.path.splitext(r)[1]]+=1
        if r!=f"simready_usd/sm_{n}_01"+os.path.splitext(r)[1]: nonroot_name.append((n,r))
    paths={i["relative_path"]:i["size"] for i in bom}
    t=sum(paths.values()); tot["all"]+=t
    useful=0; tex=0
    for p,s in paths.items():
        if p in roots or p.startswith("simready_usd/payloads/"): useful+=s
        elif re.match(r"simready_usd/Textures/[^/]+$",p): useful+=s; tex+=1; texext[os.path.splitext(p)[1].lower()]+=1
        seg=p.split("/"); key=seg[1] if len(seg)>2 else seg[-1]; tot[key]+=s
    tot["useful"]+=useful; sizes.append((useful,n))
    if tex==0: notex.append(n)
    th=[p for p in paths if re.match(r"simready_usd/\.thumbs/256x256/[^/]+\.png$",p)]
    main=[p for p in th for r in roots if p==f"simready_usd/.thumbs/256x256/{os.path.basename(r)}.png"]
    if not main: nothumb.append((n,th[:3]))
    for p in th: thumbnames[re.sub(r".*\.usda?","",p)]+=1
    lay=("A:runnables" if any(p.startswith("simready_usd/runnables/") for p in paths) else "B")
    layout[lay]+=1
    if any(p.startswith("simready_usd/payloads/") and p.endswith(".mdl") for p in paths): hasmdl_payload+=1
    if any(p.startswith("simready_usd/source_assets/") and p.endswith(".mdl") for p in paths): srcmdl+=1
    if not any(p.endswith(".mdl") for p in paths): nomdl.append(n)
sizes.sort()
print("assets",len(names),"missing meta",len(missing),missing[:10])
print("root formats",dict(rootfmt),"multiroot",len(multiroot),multiroot[:5],"nonstd root names",len(nonroot_name),nonroot_name[:8])
print("layout",dict(layout),"mdl in payloads",hasmdl_payload,"mdl in source_assets",srcmdl,"no mdl at all",len(nomdl),nomdl[:5])
print("no textures",len(notex),notex[:15])
print("no main thumb",len(nothumb),nothumb[:8]); print("thumb suffixes",dict(thumbnames))
print("texture exts",dict(texext))
print("bytes GB",{k:round(v/1e9,2) for k,v in tot.most_common(12)},"useful frac",round(tot["useful"]/tot["all"],3))
print("useful size per asset MB: min",sizes[0][0]/1e6,sizes[0][1],"median",sizes[len(sizes)//2][0]/1e6,"p90",sizes[int(len(sizes)*.9)][0]/1e6,"max",[ (round(s/1e6),n) for s,n in sizes[-5:]])
