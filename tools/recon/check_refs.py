import sys, os
from pxr import Usd, UsdShade, Sdf, UsdUtils
for root in sys.argv[sys.argv.index("--")+1:]:
    layers, assets, unresolved = UsdUtils.ComputeAllDependencies(root)
    base=os.path.dirname(root)
    print("##",os.path.basename(root),"layers",len(layers),"assets",len(assets),"unresolved",[os.path.relpath(u,base) if os.path.isabs(u) else u for u in unresolved])
    missing=[os.path.relpath(a.resolvedPath if hasattr(a,'resolvedPath') else str(a),base) for a in assets if not os.path.exists(str(a.resolvedPath if hasattr(a,'resolvedPath') else a))]
    print("   assets:",sorted(set(os.path.relpath(str(a.resolvedPath if hasattr(a,'resolvedPath') else a),base) for a in assets))[:40])
