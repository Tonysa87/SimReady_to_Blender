import bpy
for p in bpy.ops.wm.usd_import.get_rna_type().properties:
    if p.identifier in ("rna_type",): continue
    extra=""
    if p.type=="ENUM": extra=" items="+",".join(i.identifier for i in p.enum_items)+(" default="+str(p.default_flag if p.is_enum_flag else p.default))
    elif hasattr(p,"default") and getattr(p,"array_length",0)==0: extra=f" default={p.default}"
    print(f"OPT {p.identifier}: {p.type}{extra}")
