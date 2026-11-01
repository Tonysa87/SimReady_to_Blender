import json, glob, os, re, collections, sys
raw, trees = sys.argv[1], sys.argv[2]
tot=collections.Counter()
for tf in sorted(glob.glob(trees+"/tree_*.json")):
    a=os.path.basename(tf)[5:-5]
    t=[e for e in json.load(open(tf)) if e["type"]=="file"]
    tree={e["path"][len(a)+1:]:e for e in t}
    bom=json.load(open(f"{raw}/{a}/.metadata/com.nvidia.simready.packaging.bom.json"))
    roots=json.load(open(f"{raw}/{a}/.metadata/com.nvidia.simready.root_usds.json"))["entries"]
    items={i["relative_path"]:i for i in bom["items"]}
    keys=set(); [keys.update(i.keys()) for i in bom["items"]]; hk=set(); [hk.update(i["hash"].keys()) for i in bom["items"]]
    not_in_bom=[p for p in tree if p not in items]
    not_in_tree=[p for p in items if p not in tree]
    size_mismatch=[p for p in items if p in tree and tree[p]["size"]!=items[p]["size"]]
    sha_mismatch=[p for p in items if p in tree and tree[p].get("lfs") and tree[p]["lfs"]["oid"]!=items[p]["hash"]["sha256"]]
    def useful(p):
        return (p in roots or p.startswith("simready_usd/payloads/") or
                (re.match(r"simready_usd/Textures/[^/]+\.png$",p)) or p==".metadata/LICENSE.txt")
    tb=sum(e["size"] for e in tree.values()); ub=sum(e["size"] for p,e in tree.items() if useful(p))
    cats=collections.Counter()
    for p,e in tree.items():
        seg=p.split("/"); c=seg[1] if seg[0]=="simready_usd" and len(seg)>2 else (seg[0] if len(seg)>1 else "top")
        cats[c]+=e["size"]
    thumbs=[p for p in tree if ".thumbs/256x256" in p and p.count("/")==3]
    exts=collections.Counter(os.path.splitext(p)[1] for p in tree if "payloads" in p)
    print(f"== {a}: files={len(tree)} total={tb/1e6:.1f}MB useful={ub/1e6:.1f}MB ({100*ub/tb:.0f}%) roots={roots}")
    print("   bomkeys",sorted(keys),"hashkeys",sorted(hk),"not_in_bom",not_in_bom,"not_in_tree",not_in_tree,"size_mm",size_mismatch,"sha_mm",sha_mismatch)
    print("   thumbs",thumbs,"payload exts",dict(exts))
    print("   bytes by dir(MB)",{k:round(v/1e6,1) for k,v in cats.most_common()})
    tot["all"]+=tb; tot["useful"]+=ub
    for k,v in cats.items(): tot[k]+=v
print("TOTAL",{k:round(v/1e6,1) for k,v in tot.most_common()}, "useful frac", tot["useful"]/tot["all"])
