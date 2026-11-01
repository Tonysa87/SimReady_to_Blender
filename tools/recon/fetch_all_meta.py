import json, sys, time, os, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor
RES="https://huggingface.co/datasets/nvidia/simready-assets/resolve/4763fbfaa4f8e97658e68b57d01b4c86659e08b5/"
raw=sys.argv[1]; names=json.load(open(sys.argv[2]))
FILES=[".metadata/com.nvidia.simready.root_usds.json",".metadata/com.nvidia.simready.packaging.bom.json"]
def get(path):
    dst=os.path.join(raw,path)
    if os.path.exists(dst): return "cached"
    for a in range(6):
        try:
            d=urllib.request.urlopen(urllib.request.Request(RES+urllib.parse.quote(path),headers={"User-Agent":"recon"}),timeout=60).read()
            os.makedirs(os.path.dirname(dst),exist_ok=True); open(dst,"wb").write(d); return "ok"
        except urllib.error.HTTPError as e:
            if e.code==429: time.sleep(15*(a+1)); continue
            return f"HTTP{e.code}"
        except Exception as e: time.sleep(3); err=str(e)
    return "fail"
jobs=[f"{n}/{f}" for n in names for f in FILES]
with ThreadPoolExecutor(8) as ex: res=list(zip(jobs,ex.map(get,jobs)))
bad=[r for r in res if r[1] not in("ok","cached")]
print("done",len(res),"bad",len(bad),bad[:20])
