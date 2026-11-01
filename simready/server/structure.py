# SPDX-License-Identifier: GPL-3.0-or-later
"""Predict the datablock the converter will produce, from a few small USD layers.

The converter (simready/convert/convert_asset.py, `organize()`) imports the asset
with Blender's USD importer (physics variants "Disabled", everything
de-instanced, visible prims only, render/default purpose only) and keeps only
the MESH objects: exactly one -> OBJECT asset named as the mesh prim, more than
one -> COLLECTION asset named as the asset folder.

The index has to publish that type and name *before* converting, without
downloading geometries.usd or the textures. We compose the asset's small text
layers with pxr (payloads/base.usda and payloads/instances.usda; the root USD
and any other layer given are used too, a missing root is replaced by a
reference to base.usda) and replay the importer's prim walk
(`USDStageReader::collect_readers` in Blender 5.2):

- a prim that is Imageable and has purpose guide/proxy, or visibility
  "invisible", is pruned with its whole subtree;
- inactive, abstract (`class`) and undefined (`over`-only) prims are skipped;
- Mesh prims and the UsdGeom shapes (Cube, Sphere, Cylinder, Cone, Capsule,
  Plane) become MESH objects named as the prim.

geometries.usd (the mesh data) is replaced by a stub: every prim that
references it is marked as a "geometry root". The meshes below a geometry root
are the `over` children that instances.usda authors for them (to bind
materials); a geometry root without such overs is assumed to hold one mesh
named as the referenced prim (NVIDIA layout `/Geometries/<x>/<x>`). When
instances.usda is too big to download (87 assets, up to 298 MB) the references
to it are stubbed the same way, the mesh name being guessed from the instance
name (confidence "low"; the guess matches 9815 of the 9818 instances of the
readable instances.usda files).

The 8 assets without base.usda (one monolithic root USD of 8-69 MB) get the
fallback COLLECTION + asset id (confidence "low"); with the whole root USD
given, the composition handles them too.

Downloaded data is untrusted: layers are parsed from bytes into anonymous
layers and every asset path is remapped to those layers or dropped, so
composition never reads anything from disk.

Without pxr (or if composition fails) a regex fallback counts the
`instances.usda` references of base.usda (confidence "low").
"""

from __future__ import annotations

import os
import posixpath
import re
import tempfile
import threading

PAYLOADS = "payloads"
BASE = "base.usda"
INSTANCES = "instances.usda"
GEOMETRY_FILES = ("geometries.usd", "geometries.usda", "geometries.usdc")
PHYSICS_VARIANT_OFF = "Disabled"          # same rule as convert_asset.write_wrapper
STUB_KEY = "simready_geometry_stub"
SHAPES = ("Cube", "Sphere", "Cylinder", "Cylinder_1", "Cone", "Capsule", "Capsule_1", "Plane")
MAX_NAME_BYTES = 255                      # Blender 5.x ID names (MAX_ID_NAME - 3)
_PXR_LOCK = threading.Lock()              # one composition at a time (index builder uses threads)

try:  # Blender's bundled Python has pxr; the system Python usually does not
    from pxr import Sdf, Usd, UsdGeom  # type: ignore
    HAVE_PXR = True
except ImportError:  # pragma: no cover - depends on the interpreter
    HAVE_PXR = False


# --------------------------------------------------------------------- helpers


def _payload_dir(root: str) -> str:
    d = posixpath.dirname(root)
    return posixpath.join(d, PAYLOADS) if d else PAYLOADS


def _safe_rel(rel: str) -> bool:
    if not rel or rel.startswith(("/", "\\")) or "\\" in rel or ":" in rel:
        return False
    return all(p not in ("", ".", "..") for p in rel.split("/"))


def _find_root(files: dict[str, bytes]) -> str | None:
    """The root USD is the only USD layer outside payloads/ (e.g. simready_usd/sm_x_01.usd)."""
    cands = sorted(r for r in files
                   if r.lower().endswith((".usd", ".usda", ".usdc")) and f"/{PAYLOADS}/" not in f"/{r}"
                   and "/runnables/" not in f"/{r}")
    return cands[0] if cands else None


def _blender_name(name: str) -> str:
    """Blender truncates ID names to 255 bytes (on a UTF-8 boundary)."""
    b = name.encode("utf-8")
    if len(b) <= MAX_NAME_BYTES:
        return name
    return b[:MAX_NAME_BYTES].decode("utf-8", errors="ignore")


def _result(asset_id: str, meshes: list[str], confidence: str, method: str) -> dict:
    if len(meshes) == 1:
        id_type, name = "OBJECT", _blender_name(meshes[0])
    else:
        id_type, name = "COLLECTION", _blender_name(asset_id)
    return {"id_type": id_type, "name": name, "mesh_count": len(meshes),
            "confidence": confidence, "method": method, "meshes": meshes}


# ----------------------------------------------------------------- public API


def wanted_files(asset_id: str, bom_items: list[dict], root: str, max_bytes: int = 2_000_000,
                 include_root: bool = False) -> list[str]:
    """Small layers to download for `predict()`: payloads/base.usda and
    payloads/instances.usda (and the root USD if include_root), each only if listed
    in the BOM and <= max_bytes.

    The root is not needed: on all 2677 assets of revision 4763fbf it only adds
    physics-material/grasp overs and the physics variants, and predictions with
    and without it are identical; predict() then references base.usda itself."""
    sizes = {}
    for item in bom_items:
        rel, size = item.get("relative_path"), item.get("size")
        if isinstance(rel, str) and isinstance(size, int) and _safe_rel(rel):
            sizes[rel] = size
    pdir = _payload_dir(root)
    out = []
    for rel in ((root,) if include_root else ()) + (f"{pdir}/{BASE}", f"{pdir}/{INSTANCES}"):
        if rel in sizes and sizes[rel] <= max_bytes and rel not in out:
            out.append(rel)
    return out


def predict(asset_id: str, files: dict[str, bytes], root: str | None = None) -> dict:
    """Predict {id_type, name, mesh_count, confidence, method} (+ "meshes": predicted
    mesh object names) of the .blend the converter will build for `asset_id`.

    `files` maps BOM relative paths to bytes (root USD, payloads/base.usda,
    optionally payloads/instances.usda and any other small layer)."""
    files = {r: b for r, b in files.items() if isinstance(b, (bytes, bytearray)) and _safe_rel(r)}
    root = root if root in files else _find_root(files)
    pdir = _payload_dir(root) if root else None
    if pdir is None:
        bases = sorted(r for r in files if posixpath.basename(r) == BASE)
        pdir = posixpath.dirname(bases[0]) if bases else PAYLOADS
    base_rel = f"{pdir}/{BASE}"

    if HAVE_PXR and (base_rel in files or root):
        try:
            with _PXR_LOCK:
                return _predict_pxr(asset_id, files, root, base_rel)
        except Exception as e:  # noqa: BLE001 - malformed data must not break the index
            fallback = _predict_text(asset_id, files, base_rel)
            fallback["method"] += f"; pxr failed: {type(e).__name__}"
            return fallback
    return _predict_text(asset_id, files, base_rel)


# ------------------------------------------------------------------ pxr method


def _guess_mesh_name(target: str) -> str:
    """Mesh prim name for a referenced /Geometries/<x> or /Instances/<x> we cannot read:
    NVIDIA layout is /Geometries/<x>/<x>; instances of a shared mesh get a "_<n>" suffix
    (Instances/part_01_mesh_00_1 -> mesh part_01_mesh_00)."""
    return re.sub(r"(_mesh_\d+)_\d+$", r"\1", target)


class _Layers:
    """Anonymous Sdf layers built from bytes, with asset paths remapped among them."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.by_rel: dict[str, Sdf.Layer] = {}
        self.stubs: dict[str, Sdf.Layer] = {}
        self.missing: set[str] = set()
        self.missing_instances = False
        for rel, data in files.items():
            if not rel.lower().endswith((".usd", ".usda", ".usdc")):
                continue
            layer = self._parse(rel, bytes(data))
            if layer is not None:
                self.by_rel[rel] = layer
        for rel, layer in list(self.by_rel.items()):
            self._remap(rel, layer)

    @staticmethod
    def _parse(rel: str, data: bytes):
        name = posixpath.basename(rel)
        if data.startswith(b"#usda"):
            layer = Sdf.Layer.CreateAnonymous(name if name.endswith(".usda") else name + ".usda")
            return layer if layer.ImportFromString(data.decode("utf-8", errors="replace")) else None
        if data.startswith(b"PXR-USDC"):
            # Crate can only be read from a file: private temp copy, opened as anonymous.
            fd, tmp = tempfile.mkstemp(suffix=".usdc")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                src = Sdf.Layer.OpenAsAnonymous(tmp)
                if src is None:
                    return None
                layer = Sdf.Layer.CreateAnonymous(posixpath.splitext(name)[0] + ".usda")
                layer.TransferContent(src)
                return layer
            finally:
                os.unlink(tmp)
        return None

    def _resolve(self, from_rel: str, asset_path: str) -> str | None:
        if not asset_path or "://" in asset_path or asset_path.startswith(("/", "\\")) or ":" in asset_path:
            return None
        rel = posixpath.normpath(posixpath.join(posixpath.dirname(from_rel), asset_path.replace("\\", "/")))
        return rel if _safe_rel(rel) else None

    def _stub(self, rel: str) -> Sdf.Layer:
        if rel not in self.stubs:
            self.stubs[rel] = Sdf.Layer.CreateAnonymous("stub_" + posixpath.basename(rel) + ".usda")
        return self.stubs[rel]

    def _map_arc(self, from_rel: str, arc, kind):
        """Return the remapped arc, or None to drop it."""
        if not arc.assetPath:
            return arc                                    # internal arc
        rel = self._resolve(from_rel, arc.assetPath)
        if rel in self.by_rel:
            target = self.by_rel[rel].identifier
        elif rel and posixpath.basename(rel).lower() in GEOMETRY_FILES + (INSTANCES,):
            # Missing mesh data (geometries.usd) or a too-big instances.usda: stub prim
            # marked with the mesh name it most likely holds.
            if posixpath.basename(rel).lower() == INSTANCES:
                self.missing_instances = True
            stub = self._stub(rel)
            path = arc.primPath if not arc.primPath.isEmpty else Sdf.Path("/GeometryStub")
            if not stub.GetPrimAtPath(path):
                spec = Sdf.CreatePrimInLayer(stub, path)
                spec.specifier = Sdf.SpecifierDef
                spec.typeName = "Xform"
                spec.customData = {STUB_KEY: _guess_mesh_name(path.name)}
            if arc.primPath.isEmpty and not stub.defaultPrim:
                stub.defaultPrim = path.name
            target = stub.identifier
        else:
            if rel:
                self.missing.add(rel)
            return None
        if kind == "references":
            return Sdf.Reference(target, arc.primPath, arc.layerOffset, arc.customData)
        return Sdf.Payload(target, arc.primPath, arc.layerOffset)

    def _remap(self, rel: str, layer) -> None:
        subs = []
        for p in layer.subLayerPaths:
            r = self._resolve(rel, p)
            if r in self.by_rel:
                subs.append(self.by_rel[r].identifier)
            elif r:
                self.missing.add(r)
        if list(layer.subLayerPaths) != subs:
            layer.subLayerPaths = subs

        paths = []
        layer.Traverse(Sdf.Path.absoluteRootPath,
                       lambda p: paths.append(p) if (p.IsPrimPath() or p.IsPrimVariantSelectionPath()) else None)
        for path in paths:
            spec = layer.GetPrimAtPath(path)
            if spec is None:
                continue
            for key in ("references", "payload"):
                if not spec.HasInfo(key):
                    continue
                op = spec.GetInfo(key)
                new = type(op)()
                kind = "references" if key == "references" else "payload"
                fields = (("explicitItems",) if op.isExplicit else
                          ("prependedItems", "appendedItems", "addedItems", "orderedItems", "deletedItems"))
                for field in fields:
                    items = [m for m in (self._map_arc(rel, a, kind) for a in getattr(op, field)) if m is not None]
                    setattr(new, field, items)
                spec.SetInfo(key, new)


def _disable_physics_variants(stage, prim) -> None:
    for name in prim.GetVariantSets().GetNames():
        vs = prim.GetVariantSets().GetVariantSet(name)
        if PHYSICS_VARIANT_OFF in vs.GetVariantNames():
            vs.SetVariantSelection(PHYSICS_VARIANT_OFF)


def _deinstance(stage) -> None:
    for _ in range(64):
        found = [p for p in stage.Traverse() if p.IsInstanceable()]
        if not found:
            return
        for p in found:
            p.SetInstanceable(False)


def _token(prim, attr_name: str) -> str | None:
    attr = prim.GetAttribute(attr_name)
    if not attr or not attr.HasAuthoredValue():
        return None
    if attr.ValueMightBeTimeVarying():
        return "<animated>"
    return attr.Get()


def _pruned(prim, typed: bool) -> bool:
    """Blender's include_by_purpose / include_by_visibility on one prim (own opinion only;
    the subtree goes with it). Placeholders (untyped overs) are checked too."""
    if typed and not prim.IsA(UsdGeom.Imageable):
        return False
    purpose = _token(prim, "purpose")
    if purpose in ("guide", "proxy"):
        return True
    return _token(prim, "visibility") == "invisible"


_WALK = None


def _children(prim):
    global _WALK
    if _WALK is None:
        _WALK = Usd.PrimIsActive & Usd.PrimIsLoaded & ~Usd.PrimIsAbstract & Usd.PrimIsDefined
    return prim.GetFilteredChildren(_WALK)


def _collect(prim, meshes: list[str], notes: list[str]) -> None:
    if _pruned(prim, typed=True):
        return
    if prim.IsA(UsdGeom.Mesh) or prim.GetTypeName() in SHAPES:
        meshes.append(prim.GetName())
    elif prim.IsA(UsdGeom.PointInstancer):
        notes.append(f"PointInstancer {prim.GetPath()}")
    stub = prim.GetCustomDataByKey(STUB_KEY)
    if stub:
        # Geometry root: its meshes live in the missing geometries.usd; instances.usda
        # names them with material-binding overs (undefined prims here).
        placeholders = [c for c in prim.GetFilteredChildren(Usd.PrimIsActive & ~Usd.PrimIsDefined)
                        if c.GetSpecifier() == Sdf.SpecifierOver]
        if placeholders:
            for c in placeholders:
                if not _pruned(c, typed=False):
                    meshes.append(c.GetName())
        else:
            meshes.append(stub)
            notes.append(f"no overs below {prim.GetPath()}: assumed mesh {stub}")
    for child in _children(prim):
        _collect(child, meshes, notes)


def _predict_pxr(asset_id: str, files: dict[str, bytes], root: str | None, base_rel: str) -> dict:
    layers = _Layers(files)
    notes: list[str] = []
    root_layer = layers.by_rel.get(root) if root else None
    if root_layer is None:
        base = layers.by_rel.get(base_rel)
        if base is None:
            return _fallback(asset_id, "pxr: no usable root or base layer")
        # Synthetic root: what the NVIDIA root USD does (reference base.usda at the default prim).
        root_layer = Sdf.Layer.CreateAnonymous("root.usda")
        dp = base.defaultPrim or "RootNode"
        spec = Sdf.CreatePrimInLayer(root_layer, Sdf.Path("/" + dp))
        spec.specifier = Sdf.SpecifierDef
        spec.referenceList.prependedItems.append(Sdf.Reference(base.identifier))
        root_layer.defaultPrim = dp

    stage = Usd.Stage.Open(root_layer, Usd.Stage.LoadAll)
    dp = stage.GetDefaultPrim()
    if not dp:
        return _fallback(asset_id, "pxr: no default prim")
    stage.SetEditTarget(stage.GetSessionLayer())
    _disable_physics_variants(stage, dp)
    _deinstance(stage)

    meshes: list[str] = []
    _collect(dp, meshes, notes)

    confidence = "high"
    method = "pxr-compose"
    if layers.missing_instances:
        confidence, method = "low", method + " (no instances.usda)"
    if any(n.startswith("PointInstancer") for n in notes):
        confidence = "low"
    if not meshes:
        return _fallback(asset_id, method + ": no mesh found")
    res = _result(asset_id, meshes, confidence, method)
    if notes:
        res["notes"] = notes[:20]
    return res


# ----------------------------------------------------------------- text method


_REF = re.compile(r'@([^@\s]*instances\.usda)@\s*<([^>]*)>')


def _predict_text(asset_id: str, files: dict[str, bytes], base_rel: str) -> dict:
    """Regex fallback: one mesh per instances.usda reference in base.usda."""
    base = files.get(base_rel)
    if base is None:
        return _fallback(asset_id, "text: no base.usda")
    text = base.decode("utf-8", errors="replace")
    refs = [m.group(2) for m in _REF.finditer(text)]
    if not refs:
        return _fallback(asset_id, "text: no instance reference")
    inst = files.get(posixpath.join(posixpath.dirname(base_rel), INSTANCES))
    names = []
    for target in refs:
        leaf = target.rsplit("/", 1)[-1]
        name = _guess_mesh_name(leaf)
        if inst is not None:
            m = re.search(r'def\s+\w*\s*"%s"\s*\((?:[^()]|\([^()]*\))*\)\s*\{\s*over\s+"([^"]+)"' % re.escape(leaf),
                          inst.decode("utf-8", errors="replace"))
            if m:
                name = m.group(1)
        names.append(name)
    return _result(asset_id, names, "low", "text-refcount")


def _fallback(asset_id: str, why: str) -> dict:
    return {"id_type": "COLLECTION", "name": _blender_name(asset_id), "mesh_count": 0,
            "confidence": "low", "method": f"fallback ({why})", "meshes": []}
