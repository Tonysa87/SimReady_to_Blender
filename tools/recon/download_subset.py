import json, re, sys, time, os, hashlib, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor
RES="https://huggingface.co/datasets/nvidia/simready-assets/resolve/main/"
raw, trees = sys.argv[1], sys.argv[2]
def want(rel, asset, roots):
    return (rel in roots or rel.startswith("simready_usd/payloads/") or re.match(r"simready_usd/Textures/[^/]+\.png$",rel)
            or rel==".metadata/LICENSE.txt" or rel.endswith(".json") or re.match(r"simready_usd/\.thumbs/256x256/sm_[^/]+\.usda?\.png$",rel))
def get(url):
    for a in range(6):
        try: return urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"recon"}),timeout=300).read()
        except urllib.error.HTTPError as e:
            if e.code==429: time.sleep(10*(a+1)); continue
            raise
jobs=[]
for asset in sys.argv[3:]:
    t=[e for e in json.load(open(f"{trees}/tree_{asset}.json")) if e["type"]=="file"]
    roots=json.load(open(f"{raw}/{asset}/.metadata/com.nvidia.simready.root_usds.json"))["entries"]
    bom={i["relative_path"]:i for i in json.load(open(f"{raw}/{asset}/.metadata/com.nvidia.simready.packaging.bom.json"))["items"]}
    for e in t:
        rel=e["path"][len(asset)+1:]
        if want(rel,asset,roots): jobs.append((e["path"],e["size"],bom.get(rel)))
def run(j):
    path,size,b=j; dst=os.path.join(raw,path)
    if not (os.path.exists(dst) and os.path.getsize(dst)==size):
        os.makedirs(os.path.dirname(dst),exist_ok=True); open(dst,"wb").write(get(RES+urllib.parse.quote(path)))
    d=open(dst,"rb").read(); ok_size=len(d)==size
    ok_bom = None if b is None else (len(d)==b["size"] and hashlib.sha256(d).hexdigest()==b["hash"]["sha256"])
    return path,len(d),ok_size,ok_bom
with ThreadPoolExecutor(6) as ex:
    res=list(ex.map(run,jobs))
bad=[r for r in res if not r[2] or r[3] is False]
print("files",len(res),"bytes",sum(r[1] for r in res),"bom-verified",sum(1 for r in res if r[3]),"not-in-bom",sum(1 for r in res if r[3] is None),"BAD",bad)
