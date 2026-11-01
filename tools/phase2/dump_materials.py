# SPDX-License-Identifier: GPL-3.0-or-later
"""Dump MDL + preview material networks of an asset (run inside Blender for pxr)."""
import json, sys
from pathlib import Path
from pxr import Usd, UsdShade, Sdf

asset_dir = Path(sys.argv[sys.argv.index("--") + 1])
root_rel = json.loads((asset_dir / ".metadata/com.nvidia.simready.root_usds.json").read_text())["entries"][0]
stage = Usd.Stage.Open(str(asset_dir / root_rel))
print("ASSET", asset_dir.name, "variants:", {vs: stage.GetDefaultPrim().GetVariantSets().GetVariantSet(vs).GetVariantSelection() for vs in stage.GetDefaultPrim().GetVariantSets().GetNames()})
seen = set()
for prim in Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies()):
    if not prim.IsA(UsdShade.Material):
        continue
    key = prim.GetName()
    if key in seen: continue
    seen.add(key)
    mat = UsdShade.Material(prim)
    mdl = mat.GetSurfaceOutput("mdl")
    src = mdl.GetConnectedSources()[0] if mdl and mdl.HasConnectedSource() else []
    print(f"  MAT {key}")
    if src:
        sh = UsdShade.Shader(src[0].source.GetPrim())
        print("    MDL", sh.GetPrim().GetAttribute("info:mdl:sourceAsset:subIdentifier").Get(), sh.GetPrim().GetAttribute("info:mdl:sourceAsset").Get())
        for inp in sh.GetInputs():
            v = inp.Get()
            if isinstance(v, Sdf.AssetPath):
                ok = "OK" if v.resolvedPath else "UNRESOLVED"
                v = f"{v.path} [{ok}] cs={inp.GetAttr().GetColorSpace() or '-'}"
            print(f"      {inp.GetBaseName()} = {v}")
    else:
        print("    (no MDL)")
