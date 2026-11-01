import json, re, sys, time, urllib.request
BASE="https://huggingface.co/api/datasets/nvidia/simready-assets/tree/main?limit=1000"
out=sys.argv[1]; url=BASE; entries=[]
while url:
    for attempt in range(5):
        try:
            r=urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"recon"}),timeout=60); break
        except urllib.error.HTTPError as e:
            if e.code==429: time.sleep(10*(attempt+1)); continue
            raise
    entries+=json.load(r)
    m=re.search(r'<([^>]+)>;\s*rel="next"', r.headers.get("Link","") or "")
    url=m.group(1) if m else None
json.dump(entries,open(out,"w"),indent=0)
print(len(entries))
