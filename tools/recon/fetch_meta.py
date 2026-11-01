import json, re, sys, time, urllib.request, urllib.parse, os
REPO="https://huggingface.co/api/datasets/nvidia/simready-assets/tree/main/"
RES="https://huggingface.co/datasets/nvidia/simready-assets/resolve/main/"
def get(url):
    for a in range(6):
        try: return urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"recon"}),timeout=120)
        except urllib.error.HTTPError as e:
            if e.code==429: time.sleep(10*(a+1)); continue
            raise
def tree(asset):
    url=REPO+urllib.parse.quote(asset)+"?recursive=true&limit=1000&expand=false"; out=[]
    while url:
        r=get(url); out+=json.load(r)
        m=re.search(r'<([^>]+)>;\s*rel="next"', r.headers.get("Link","") or ""); url=m.group(1) if m else None
    return out
def dl(path, root):
    dst=os.path.join(root,path); os.makedirs(os.path.dirname(dst),exist_ok=True)
    data=get(RES+urllib.parse.quote(path)).read(); open(dst,"wb").write(data); return len(data)
root=sys.argv[1]; idx=sys.argv[2]
for asset in sys.argv[3:]:
    t=tree(asset); json.dump(t,open(os.path.join(idx,f"tree_{asset}.json"),"w"))
    files=[e for e in t if e["type"]=="file"]
    for e in files:
        p=e["path"]
        if p.endswith(".json"): dl(p,root)
    print(asset,len(files),sum(e["size"] for e in files))
