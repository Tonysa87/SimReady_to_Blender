# SPDX-License-Identifier: GPL-3.0-or-later
"""Make a saved .blend byte-for-byte reproducible, then compress it like Blender does.

Blender writes a few values that change at every run even when the scene is the same:

- ``ID.session_uid``: a per-session counter (reset by Blender when it reads the file);
- ``bNode.identifier``: random, seeded from the clock (only has to be unique per tree);
- ``Mesh.face_sets_color_seed``: hash of the current second (only colors sculpt face sets);
- ``ScrEdge.v1/v2``: the two vertices of a UI edge, sorted by memory address;
- the "old address" of data blocks: Blender numbers them per ID from a hash of the ID name,
  but its address -> number map is shared by the whole file, so a temporary buffer that
  reuses the memory of one written for an earlier ID takes that earlier number and shifts
  the following ones (it depends on the memory allocator: it happens now and then on big
  files). The reader only resolves data pointers within the same ID.

``normalize()`` patches them in an uncompressed .blend, in place: the session uid is set
to 0 ("unset"), node identifiers become 1..n in list order, the seed 0 and the two edge
vertices are sorted by their written value, and the data blocks of each ID get new
numbers from the ID's own number and their position (pointers inside the ID follow).
Field offsets come from the file's own DNA,
so a field that does not exist (another Blender version) is simply skipped. Node trees
that could reference node identifiers (zones, bakes) are left untouched.

``compress()`` writes Blender's zstd "seekable" format (1 MiB frames, level 3, seek table
at the end), deterministic for the same input. Runs with Blender's Python (``zstandard``).
"""

from __future__ import annotations

import hashlib
import mmap
import re
import struct
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HEADER = re.compile(rb"BLENDER17-01v\d{4}")   # format 1, 64-bit pointers, little endian (Blender >= 5.0)
BHEAD = struct.Struct("<4sIQqq")               # code, SDNAnr, old pointer, len, nr
FRAME = 1 << 20
NO_STRUCT = {b"DNA1", b"ENDB", b"TEST", b"REND"}  # blocks whose SDNAnr means nothing
LEVEL = 3
# Node types that never store another node's identifier (shader trees built by the importer and our hook).
SAFE_NODE_PREFIXES = (b"ShaderNode",)
SAFE_NODES = {b"NodeFrame", b"NodeReroute", b"NodeGroupInput", b"NodeGroupOutput"}


class NotSupported(ValueError):
    pass


class DNA:
    """Struct layouts of a .blend: {struct name: {field name: (offset, type name, size)}}."""

    def __init__(self, buf, start: int) -> None:
        p = start

        def align(q: int) -> int:
            return start + ((q - start + 3) & ~3)

        def strings(p: int, tag: bytes) -> tuple[list[str], int]:
            if buf[p:p + 4] != tag:
                raise NotSupported(f"DNA: expected {tag!r}")
            n, = struct.unpack_from("<I", buf, p + 4)
            p += 8
            out = []
            for _ in range(n):
                e = buf.find(b"\0", p)
                out.append(bytes(buf[p:e]).decode("latin-1"))
                p = e + 1
            return out, align(p)

        if buf[p:p + 4] != b"SDNA":
            raise NotSupported("DNA: no SDNA")
        names, p = strings(p + 4, b"NAME")
        types, p = strings(p, b"TYPE")
        if buf[p:p + 4] != b"TLEN":
            raise NotSupported("DNA: no TLEN")
        tlen = struct.unpack_from(f"<{len(types)}H", buf, p + 4)
        p = align(p + 4 + 2 * len(types))
        if buf[p:p + 4] != b"STRC":
            raise NotSupported("DNA: no STRC")
        n, = struct.unpack_from("<I", buf, p + 4)
        p += 8
        self.structs: list[str] = []
        self.sizes: list[int] = []
        self.fields: dict[str, dict[str, tuple[int, str, int]]] = {}
        for _ in range(n):
            t, nf = struct.unpack_from("<HH", buf, p)
            p += 4
            off, fields = 0, {}
            for k in range(nf):
                ft, fn = struct.unpack_from("<HH", buf, p + 4 * k)
                name = names[fn]
                size = 8 if name.startswith(("*", "(*")) else tlen[ft]
                for dim in re.findall(r"\[(\d+)\]", name):
                    size *= int(dim)
                key = re.sub(r"[\*\(\)]|\[.*", "", name)
                fields.setdefault(key, (off, types[ft], size))
                off += size
            p += 4 * nf
            self.structs.append(types[t])
            self.sizes.append(tlen[t])
            self.fields[types[t]] = fields

    def field(self, struct_name: str, field: str, type_name: str | None = None, size: int | None = None):
        """Offset of `field` in `struct_name`, or None if absent / not of the expected type and size."""
        f = self.fields.get(struct_name, {}).get(field)
        if not f or (type_name and f[1] != type_name) or (size and f[2] != size):
            return None
        return f[0]


def _blocks(buf) -> list[tuple[bytes, int, int, int, int, int]]:
    """(code, sdna, old, data offset, len, nr) of every block, up to ENDB."""
    if not HEADER.fullmatch(bytes(buf[:17])):
        raise NotSupported(f"unsupported .blend header {bytes(buf[:17])!r}")
    out, i, end = [], 17, len(buf)
    while i + BHEAD.size <= end:
        code, sdna, old, ln, nr = BHEAD.unpack_from(buf, i)
        if ln < 0 or i + BHEAD.size + ln > end:
            raise NotSupported("truncated block")
        out.append((code, sdna, old, i + BHEAD.size, ln, nr))
        if code == b"ENDB":
            return out
        i += BHEAD.size + ln
    raise NotSupported("no ENDB block")


def _sections(blocks) -> list[tuple[int, list[int]]]:
    """(head block index, [its DATA block indices]): an ID (or another non-DATA block) and the
    data written after it. Blender's reader resolves data pointers within one section."""
    out: list[tuple[int, list[int]]] = []
    for i, b in enumerate(blocks):
        if b[0] == b"DATA":
            if out:
                out[-1][1].append(i)
        elif b[0] not in (b"DNA1", b"ENDB"):
            out.append((i, []))
    return out


def _new_address(seed: bytes, used: set[int]) -> int:
    """A non-zero 'old address' in Blender's style (low 4 bits 0, top bit 0) not in `used`."""
    while True:
        v = (int.from_bytes(hashlib.blake2b(seed, digest_size=8).digest(), "little") << 4) & ((1 << 63) - 1)
        if v and v not in used:
            used.add(v)
            return v
        seed += b"+"


def _sharing_fields(dna: "DNA") -> dict[str, list[int]]:
    """struct name -> offsets of its `*sharing_info*` pointers. Blender writes them as numbers
    with no block behind (the reader recreates the sharing info), taken from the same counter."""
    out: dict[str, list[int]] = {}
    for name, flds in dna.fields.items():
        offs = [off for key, (off, _t, size) in flds.items() if "sharing_info" in key and size == 8]
        if offs:
            out[name] = offs
    return out


def renumber_data(buf, blocks, dna: "DNA | None" = None) -> dict:
    """Give the DATA blocks of every section numbers that depend only on the section's head
    (its own address, stable: Blender derives it from the ID name) and their position, and
    rewrite the pointers to them found in the section (any aligned 64-bit word equal to an
    old number: struct pointer fields and pointer arrays alike; a false match would need a
    60-bit coincidence). Sections with duplicate numbers (corrupt) are left as they are."""
    import numpy as np
    stats = {"data_blocks": 0, "pointers": 0, "sections_skipped": 0, "sharing_info": 0}
    used = {b[2] for b in blocks if b[0] != b"DATA"}
    sharing = _sharing_fields(dna) if dna else {}
    for head, data in _sections(blocks):
        if not data:
            continue
        olds = [blocks[i][2] for i in data]
        if len(set(olds)) != len(olds) or 0 in olds:
            stats["sections_skipped"] += 1
            continue
        hb = blocks[head]
        seed = hb[0] + struct.pack("<Q", hb[2])
        table = {old: _new_address(seed + struct.pack("<I", k), used) for k, old in enumerate(olds)}
        keys = np.fromiter(table, dtype=np.uint64, count=len(table))
        vals = np.fromiter(table.values(), dtype=np.uint64, count=len(table))
        order = np.argsort(keys)
        keys, vals = keys[order], vals[order]
        for i in (head, *data):
            off, ln = blocks[i][3], blocks[i][4]
            if ln < 8:
                continue
            words = np.frombuffer(buf, dtype="<u8", count=ln // 8, offset=off)
            hit = np.isin(words, keys)
            if hit.any():
                idx = np.nonzero(hit)[0]
                new = vals[np.searchsorted(keys, words[idx])]
                for j, v in zip(idx.tolist(), new.tolist()):
                    struct.pack_into("<Q", buf, off + 8 * j, v)
                stats["pointers"] += len(idx)
        for i in data:
            struct.pack_into("<Q", buf, blocks[i][3] - BHEAD.size + 8, table[blocks[i][2]])
        stats["data_blocks"] += len(data)
        n = 0
        for i in (head, *data):
            b = blocks[i]
            offs = sharing.get(dna.structs[b[1]]) if b[0] not in NO_STRUCT and b[1] < len(dna.structs) else None
            if not offs or b[5] < 1:
                continue
            step = b[4] // b[5]
            for k in range(b[5]):
                for o in offs:
                    pos = b[3] + k * step + o
                    if pos + 8 <= b[3] + b[4] and struct.unpack_from("<Q", buf, pos)[0]:
                        struct.pack_into("<Q", buf, pos, _new_address(seed + b"s" + struct.pack("<I", n), used))
                        n += 1
        stats["sharing_info"] += n
    return stats


def normalize(path: Path) -> dict:
    """Patch the run-dependent values of an uncompressed .blend in place. Returns counters."""
    stats = {"session_uid": 0, "node_identifier": 0, "face_sets_seed": 0, "screen_edges": 0,
             "trees_skipped": 0}
    with path.open("r+b") as f, mmap.mmap(f.fileno(), 0) as buf:
        blocks = _blocks(buf)
        dna_blk = next((b for b in blocks if b[0] == b"DNA1"), None)
        if not dna_blk:
            raise NotSupported("no DNA1 block")
        dna = DNA(buf, dna_blk[3])

        def struct_of(b) -> str:
            if b[0] in NO_STRUCT or b[1] >= len(dna.structs):
                return ""
            return dna.structs[b[1]]

        def put_u32(pos: int, value: int) -> None:
            struct.pack_into("<I", buf, pos, value)

        # 1. ID.session_uid of every ID (real or embedded): reset to 0 = "unset".
        uid = dna.field("ID", "session_uid", "int", 4)
        id_structs = {name for name, flds in dna.fields.items()
                      if flds and next(iter(flds.values()))[:2] == (0, "ID")}
        if uid is not None:
            for b in blocks:
                if struct_of(b) in id_structs and b[4] >= uid + 4:
                    put_u32(b[3] + uid, 0)
                    stats["session_uid"] += 1

        # 2. Mesh.face_sets_color_seed.
        seed = dna.field("Mesh", "face_sets_color_seed", "int", 4)
        if seed is not None:
            for b in blocks:
                if struct_of(b) == "Mesh" and b[4] >= seed + 4:
                    put_u32(b[3] + seed, 0)
                    stats["face_sets_seed"] += 1

        # 3. bNode.identifier: 1..n in list order, per tree.
        nodes_off = dna.field("bNodeTree", "nodes", "ListBase")
        refs_num = dna.field("bNodeTree", "nested_node_refs_num", "int", 4)
        n_next = dna.field("bNode", "next", size=8)
        n_ident = dna.field("bNode", "identifier", "int", 4)
        n_idname = dna.field("bNode", "idname", "char")
        if None not in (nodes_off, n_next, n_ident, n_idname):
            idname_size = dna.fields["bNode"]["idname"][2]
            section_of = {}
            for head, data in _sections(blocks):
                for i in (head, *data):
                    section_of[i] = head
            nodes_in: dict[int, dict[int, int]] = {}  # per section: old address -> data offset
            for i, b in enumerate(blocks):
                if struct_of(b) == "bNode" and b[5] == 1:
                    nodes_in.setdefault(section_of.get(i), {})[b[2]] = b[3]
            for i, b in enumerate(blocks):
                if struct_of(b) != "bNodeTree":
                    continue
                node_at = nodes_in.get(section_of.get(i), {})
                if refs_num is not None and struct.unpack_from("<i", buf, b[3] + refs_num)[0] != 0:
                    stats["trees_skipped"] += 1
                    continue
                chain, ptr = [], struct.unpack_from("<Q", buf, b[3] + nodes_off)[0]
                while ptr and ptr in node_at and len(chain) <= len(node_at):
                    chain.append(node_at[ptr])
                    ptr = struct.unpack_from("<Q", buf, node_at[ptr] + n_next)[0]
                if ptr:  # broken chain: leave the tree as it is
                    stats["trees_skipped"] += 1
                    continue
                idnames = [bytes(buf[p + n_idname:p + n_idname + idname_size]).split(b"\0")[0] for p in chain]
                if not all(n.startswith(SAFE_NODE_PREFIXES) or n in SAFE_NODES for n in idnames):
                    stats["trees_skipped"] += 1
                    continue
                for i, p in enumerate(chain, 1):
                    put_u32(p + n_ident, i)
                stats["node_identifier"] += len(chain)

        # 4. ScrEdge.v1/v2: undirected, sorted by address at run time; sort by written value.
        v1 = dna.field("ScrEdge", "v1", size=8)
        v2 = dna.field("ScrEdge", "v2", size=8)
        if v1 is not None and v2 == v1 + 8:
            for b in blocks:
                if struct_of(b) == "ScrEdge" and b[5] >= 1:
                    step = b[4] // b[5]
                    for k in range(b[5]):
                        p = b[3] + k * step + v1
                        a, c = struct.unpack_from("<QQ", buf, p)
                        if a > c:
                            struct.pack_into("<QQ", buf, p, c, a)
                        stats["screen_edges"] += 1

        # 5. Data block numbers (last: the steps above look blocks up by their old number).
        stats.update(renumber_data(buf, blocks, dna))
        buf.flush()
    return stats


def compress(src: Path, dst: Path, threads: int = 4) -> None:
    """zstd seekable file as Blender writes it: independent frames + seek table frame."""
    import zstandard

    def one(chunk: bytes) -> bytes:
        return zstandard.ZstdCompressor(level=LEVEL).compress(chunk)

    sizes: list[tuple[int, int]] = []
    with src.open("rb") as f, dst.open("wb") as out, ThreadPoolExecutor(threads) as pool:
        while True:
            batch = [c for c in (f.read(FRAME) for _ in range(threads * 4)) if c]
            if not batch:
                break
            for raw, comp in zip(batch, pool.map(one, batch)):
                out.write(comp)
                sizes.append((len(comp), len(raw)))
            if len(batch[-1]) < FRAME:
                break
        table = b"".join(struct.pack("<II", c, u) for c, u in sizes)
        table += struct.pack("<IBI", len(sizes), 0, 0x8F92EAB1)
        out.write(struct.pack("<II", 0x184D2A5E, len(table)) + table)


def decompress(src: Path, dst: Path) -> None:
    """Inverse of compress() (and of Blender's own compression): for checks and tools."""
    import zstandard
    with src.open("rb") as f, dst.open("wb") as out:
        r = zstandard.ZstdDecompressor().stream_reader(f, read_across_frames=True)
        while chunk := r.read(FRAME):
            out.write(chunk)


if __name__ == "__main__":  # python -m simready.convert.blend_normalize in.blend out.blend (uncompressed input)
    import shutil
    import sys
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    tmp = dst.with_suffix(".raw")
    shutil.copyfile(src, tmp)
    print(normalize(tmp))
    compress(tmp, dst)
    tmp.unlink()
