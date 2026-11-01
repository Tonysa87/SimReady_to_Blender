import bpy, sys, glob, os, numpy as np
for f in sys.argv[sys.argv.index("--")+1:]:
    im=bpy.data.images.load(f); w,h=im.size; ch=im.channels; cs=im.colorspace_settings.name; depth=im.depth
    im.colorspace_settings.name="Non-Color"
    im.scale(128,128); a=np.empty(128*128*im.channels,dtype=np.float32); im.pixels.foreach_get(a); a=a.reshape(-1,im.channels)
    m=a.mean(0); s=a.std(0)
    print(f"STAT {os.path.basename(f):50s} {w}x{h} ch={ch} depth={depth} defcs={cs} mean={np.round(m,2)} std={np.round(s,2)}")
    bpy.data.images.remove(im)
