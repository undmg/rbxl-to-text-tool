"""Reader for Roblox binary files (.rbxl places and .rbxm models).

The format is a header followed by chunks (META, SSTR, INST, PROP, PRNT, END).
Every chunk is decoded independently, so a damaged or unknown chunk only loses
that chunk's data — the rest of the file is still recovered and the problem is
reported in ``Document.warnings``.
"""

from __future__ import annotations

import struct
from typing import Any, Callable, Dict, List, Optional, Tuple

from .attributes import decode_attributes, decode_tags
from .canonical import canonical
from .instance import ChunkInfo, Document, RobloxInstance
from .rotation import basic_rotation
from .values import (
    Axes, BrickColor, CFrame, Color3, ColorSequence, Content, Faces, Font, NumberRange,
    NumberSequence, PhysicalProperties, Ray, Rect, Ref, UDim, UDim2, UniqueId, Unsupported,
    Vector2, Vector3, Vector3int16, name_enum,
)

MAGIC = b"<roblox!"
SIGNATURE = b"\x89\xff\r\n\x1a\n"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


KNOWN_CHUNKS = (b"META", b"SSTR", b"INST", b"PROP", b"PRNT", b"SIGN", b"END\x00")


class ChunkError(ValueError):
    pass


def _next_chunk(data: bytes, start: int):
    """Offset of the next plausible chunk header at or after ``start``."""
    best = None
    for name in KNOWN_CHUNKS:
        at = data.find(name, start)
        while at != -1 and at + 16 <= len(data):
            comp, size, reserved = struct.unpack("<III", data[at + 4:at + 16])
            stored = comp if comp else size
            if reserved == 0 and at + 16 + stored <= len(data) and size < 1 << 30:
                break
            at = data.find(name, at + 1)
        if at != -1 and at + 16 <= len(data) and (best is None or at < best):
            best = at
    return best


# ---------------------------------------------------------------------------
# Decompression
# ---------------------------------------------------------------------------

def decompress(data: bytes, size: int) -> bytes:
    if data[:4] == ZSTD_MAGIC:
        try:
            import zstandard
        except ImportError as exc:
            raise ChunkError("zstd-compressed chunk but the 'zstandard' package is missing") from exc
        return zstandard.ZstdDecompressor().decompress(data, max_output_size=size)
    try:
        import lz4.block
    except ImportError as exc:
        raise ChunkError("lz4-compressed chunk but the 'lz4' package is missing") from exc
    return lz4.block.decompress(data, uncompressed_size=size)


# ---------------------------------------------------------------------------
# Primitive readers
# ---------------------------------------------------------------------------

class Cursor:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def remaining(self) -> int:
        return len(self.data) - self.pos

    def take(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise ChunkError(f"needed {n} bytes at {self.pos}, only {self.remaining()} left")
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return struct.unpack("<H", self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def f32(self) -> float:
        return struct.unpack("<f", self.take(4))[0]

    def f64(self) -> float:
        return struct.unpack("<d", self.take(8))[0]

    def string(self) -> bytes:
        return self.take(self.u32())

    def text(self) -> str:
        return self.string().decode("utf-8", errors="replace")

    # Interleaved arrays: byte 0 of every value, then byte 1 of every value, ...
    # Decoded column-wise with zip, which is far faster than per-byte indexing.
    def interleaved(self, count: int, width: int) -> List[bytes]:
        raw = self.take(count * width)
        cols = [raw[j * count:(j + 1) * count] for j in range(width)]
        return [bytes(v) for v in zip(*cols)] if count else []

    def u32_array(self, count: int) -> List[int]:
        raw = self.take(count * 4)
        a, b, c, d = (raw[j * count:(j + 1) * count] for j in range(4))
        return [(w << 24) | (x << 16) | (y << 8) | z for w, x, y, z in zip(a, b, c, d)]

    def i32_array(self, count: int) -> List[int]:
        return [(n >> 1) ^ -(n & 1) for n in self.u32_array(count)]

    def i64_array(self, count: int) -> List[int]:
        out = []
        for b in self.interleaved(count, 8):
            n = int.from_bytes(b, "big")
            out.append((n >> 1) ^ -(n & 1))
        return out

    def f32_array(self, count: int) -> List[float]:
        # Roblox moves the sign bit to the end; rotate it back, then unpack in one call
        rotated = [(n >> 1) | ((n & 1) << 31) for n in self.u32_array(count)]
        return list(struct.unpack(f">{count}f", struct.pack(f">{count}I", *rotated)))

    def ref_array(self, count: int) -> List[int]:
        out, acc = [], 0
        for delta in self.i32_array(count):
            acc += delta
            out.append(acc)
        return out


def _strings(c: Cursor, n: int) -> List[Any]:
    out: List[Any] = []
    for _ in range(n):
        raw = c.string()
        try:
            out.append(raw.decode("utf-8"))
        except UnicodeDecodeError:
            out.append(raw)  # binary payload (e.g. AttributesSerialize, mesh data)
    return out


def _cframes(c: Cursor, n: int) -> List[CFrame]:
    rotations = []
    for _ in range(n):
        rot_id = c.u8()
        if rot_id == 0:
            rotations.append(tuple(c.f32() for _ in range(9)))
        else:
            rotations.append(basic_rotation(rot_id))
    xs, ys, zs = c.f32_array(n), c.f32_array(n), c.f32_array(n)
    return [CFrame(Vector3(x, y, z), r) for x, y, z, r in zip(xs, ys, zs, rotations)]


def _physical(c: Cursor, n: int) -> List[PhysicalProperties]:
    out = []
    for _ in range(n):
        flag = c.u8()
        if flag & 1:
            count = 6 if flag & 2 else 5
            out.append(PhysicalProperties(tuple(c.f32() for _ in range(count))))
        else:
            out.append(PhysicalProperties(None))
    return out


def _fonts(c: Cursor, n: int) -> List[Font]:
    out = []
    for _ in range(n):
        family = c.text()
        weight = c.u16()
        style = "Italic" if c.u8() == 1 else "Normal"
        c.string()  # cached face id
        out.append(Font(family, weight, style))
    return out


def _contents(c: Cursor, n: int) -> List[Content]:
    kinds = c.i32_array(n)
    uris = [c.text() for _ in range(c.u32())]
    objects = c.ref_array(c.u32())
    out, ui, oi = [], 0, 0
    for kind in kinds:
        if kind == 1 and ui < len(uris):
            out.append(Content(uri=uris[ui]))
            ui += 1
        elif kind == 2 and oi < len(objects):
            out.append(Content(object_ref=objects[oi]))
            oi += 1
        else:
            out.append(Content())
    return out


def _decode_values(c: Cursor, type_id: int, n: int, prop: str, shared: List[Any]) -> List[Any]:
    if type_id in (0x01, 0x1D):  # String, Bytecode
        return _strings(c, n)
    if type_id == 0x02:
        return [b != 0 for b in c.take(n)]
    if type_id == 0x03:
        return c.i32_array(n)
    if type_id == 0x04:
        return c.f32_array(n)
    if type_id == 0x05:
        return [c.f64() for _ in range(n)]
    if type_id == 0x06:
        scales, offsets = c.f32_array(n), c.i32_array(n)
        return [UDim(s, o) for s, o in zip(scales, offsets)]
    if type_id == 0x07:
        sx, sy, ox, oy = c.f32_array(n), c.f32_array(n), c.i32_array(n), c.i32_array(n)
        return [UDim2(UDim(a, c_), UDim(b, d)) for a, b, c_, d in zip(sx, sy, ox, oy)]
    if type_id == 0x08:
        out = []
        for _ in range(n):
            v = [c.f32() for _ in range(6)]
            out.append(Ray(Vector3(*v[:3]), Vector3(*v[3:])))
        return out
    if type_id == 0x09:
        return [Faces(b) for b in c.take(n)]
    if type_id == 0x0A:
        return [Axes(b) for b in c.take(n)]
    if type_id == 0x0B:
        return [BrickColor(v) for v in c.u32_array(n)]
    if type_id == 0x0C:
        r, g, b = c.f32_array(n), c.f32_array(n), c.f32_array(n)
        return [Color3(*v) for v in zip(r, g, b)]
    if type_id == 0x0D:
        x, y = c.f32_array(n), c.f32_array(n)
        return [Vector2(*v) for v in zip(x, y)]
    if type_id == 0x0E:
        x, y, z = c.f32_array(n), c.f32_array(n), c.f32_array(n)
        return [Vector3(*v) for v in zip(x, y, z)]
    if type_id == 0x10:
        return _cframes(c, n)
    if type_id == 0x12:
        return [name_enum(prop, v) for v in c.u32_array(n)]
    if type_id == 0x13:
        return [Ref(v) for v in c.ref_array(n)]
    if type_id == 0x14:
        return [Vector3int16(*struct.unpack("<hhh", c.take(6))) for _ in range(n)]
    if type_id == 0x15:
        out = []
        for _ in range(n):
            kps = []
            for _ in range(c.u32()):
                t, v, e = c.f32(), c.f32(), c.f32()
                kps.append((t, v, e))
            out.append(NumberSequence(tuple(kps)))
        return out
    if type_id == 0x16:
        out = []
        for _ in range(n):
            kps = []
            for _ in range(c.u32()):
                t, r, g, b, _e = (c.f32() for _ in range(5))
                kps.append((t, Color3(r, g, b)))
            out.append(ColorSequence(tuple(kps)))
        return out
    if type_id == 0x17:
        return [NumberRange(c.f32(), c.f32()) for _ in range(n)]
    if type_id == 0x18:
        a, b, c2, d = c.f32_array(n), c.f32_array(n), c.f32_array(n), c.f32_array(n)
        return [Rect(Vector2(w, x), Vector2(y, z)) for w, x, y, z in zip(a, b, c2, d)]
    if type_id == 0x19:
        return _physical(c, n)
    if type_id == 0x1A:
        r, g, b = c.take(n), c.take(n), c.take(n)
        return [Color3(r[i] / 255, g[i] / 255, b[i] / 255) for i in range(n)]
    if type_id == 0x1B:
        return c.i64_array(n)
    if type_id == 0x1C:
        return [shared[i] if 0 <= i < len(shared) else b"" for i in c.u32_array(n)]
    if type_id == 0x1E:
        if c.u8() != 0x10:
            raise ChunkError("OptionalCFrame without CFrame block")
        frames = _cframes(c, n)
        if c.u8() != 0x02:
            raise ChunkError("OptionalCFrame without Bool block")
        present = [b != 0 for b in c.take(n)]
        return [f if p else None for f, p in zip(frames, present)]
    if type_id == 0x1F:
        return [UniqueId(b.hex()) for b in c.interleaved(n, 16)]
    if type_id == 0x20:
        return _fonts(c, n)
    if type_id == 0x21:
        return [int.from_bytes(b, "big") for b in c.interleaved(n, 8)]
    if type_id == 0x22:
        return _contents(c, n)
    return [Unsupported(f"type 0x{type_id:02x}")] * n


# ---------------------------------------------------------------------------
# File reader
# ---------------------------------------------------------------------------

class BinaryReader:
    def __init__(self) -> None:
        self.doc_warnings: List[str] = []
        self.class_names: Dict[int, str] = {}
        self.class_refs: Dict[int, List[int]] = {}
        self.by_ref: Dict[int, RobloxInstance] = {}
        self.shared: List[Any] = []
        self.metadata: Dict[str, str] = {}
        self.chunks: List[ChunkInfo] = []
        self.parented = False

    def warn(self, msg: str) -> None:
        self.doc_warnings.append(msg)

    def read(self, data: bytes, path: str = "") -> Document:
        if not data.startswith(MAGIC):
            raise ValueError("not a Roblox binary file (missing '<roblox!' header)")
        if data[8:14] != SIGNATURE:
            self.warn("damaged: file signature is wrong (file may have been opened as text)")
        if len(data) < 32:
            raise ValueError("file is too short to be a Roblox file")
        _version, class_count, inst_count = struct.unpack("<HII", data[14:24])

        prnt: List[Tuple[int, bytes]] = []
        props: List[Tuple[int, bytes, ChunkInfo]] = []
        pos, ended = 32, False

        while pos + 16 <= len(data):
            header = data[pos:pos + 4]
            comp, size, _ = struct.unpack("<III", data[pos + 4:pos + 16])
            stored = comp if comp else size
            if header not in KNOWN_CHUNKS or pos + 16 + stored > len(data):
                nxt = _next_chunk(data, pos + 1)
                label = header.rstrip(b"\x00").decode("ascii", errors="replace")
                if header in KNOWN_CHUNKS and nxt is None:
                    self.warn(f"damaged: {label} chunk at byte {pos} runs past the end of the "
                              "file (file is truncated)")
                    break
                if nxt is None:
                    self.warn(f"damaged: unreadable data from byte {pos} to the end of the file")
                    break
                self.warn(f"damaged: unreadable data at bytes {pos}-{nxt}; skipped to the "
                          "next valid chunk")
                pos = nxt
                continue
            name = header.rstrip(b"\x00").decode("ascii")
            start = pos
            pos += 16
            raw = data[pos:pos + stored]
            pos += stored
            info = ChunkInfo(name, start, stored + 16, size)
            self.chunks.append(info)
            if name == "END":
                ended = True
                break
            try:
                body = decompress(raw, size) if comp else raw
            except Exception as exc:  # noqa: BLE001 - any codec failure means a damaged chunk
                self.warn(f"damaged: could not decompress {name} chunk at byte {start} ({exc})")
                continue
            try:
                if name == "META":
                    self._meta(body)
                elif name == "SSTR":
                    self._sstr(body)
                elif name == "INST":
                    self._inst(body, info)
                elif name == "PROP":
                    # Decode after all INST chunks so every class is known.
                    props.append((start, body, info))
                elif name == "PRNT":
                    prnt.append((start, body))
            except Exception as exc:  # noqa: BLE001
                self.warn(f"damaged: could not read {name} chunk at byte {start} ({exc})")

        if not ended:
            self.warn("damaged: END chunk missing — the file is truncated or corrupted; "
                      "everything before the damage was recovered")

        for start, body, info in props:
            try:
                self._prop(body, info)
            except Exception as exc:  # noqa: BLE001
                label = f"{info.class_name}.{info.prop_name}" if info.prop_name else "PROP"
                self.warn(f"could not decode {label} (chunk at byte {start}): {exc}")

        root = RobloxInstance("DataModel", "Game")
        for start, body in prnt:
            try:
                self._prnt(body, root)
            except Exception as exc:  # noqa: BLE001
                self.warn(f"damaged: could not read PRNT chunk at byte {start} ({exc})")

        orphans = [inst for inst in self.by_ref.values() if inst.parent is None]
        if orphans:
            if not self.parented:
                self.warn("damaged: parent information (PRNT) missing; all instances were "
                          "recovered as top-level orphans")
            else:
                self.warn(f"damaged: {len(orphans)} instances had no known parent and were "
                          "attached to the top level")
            for inst in orphans:
                inst.orphan = True
                root.add_child(inst)

        self._finish(root)

        if len(self.by_ref) != inst_count:
            self.warn(f"header says {inst_count} instances but {len(self.by_ref)} were read")
        if len(self.class_names) != class_count:
            self.warn(f"header says {class_count} classes but {len(self.class_names)} were read")

        doc = Document(root=root, path=path, format="binary", file_size=len(data),
                       warnings=self.doc_warnings, chunks=self.chunks, metadata=self.metadata)
        doc.kind = "place" if any(c.class_name == "Workspace" for c in root.children) else "model"
        return doc

    # --- chunks -------------------------------------------------------------

    def _meta(self, body: bytes) -> None:
        c = Cursor(body)
        for _ in range(c.u32()):
            key = c.text()
            self.metadata[key] = c.text()

    def _sstr(self, body: bytes) -> None:
        c = Cursor(body)
        c.u32()  # version
        for _ in range(c.u32()):
            c.take(16)  # md5 hash
            raw = c.string()
            try:
                self.shared.append(raw.decode("utf-8"))
            except UnicodeDecodeError:
                self.shared.append(raw)

    def _inst(self, body: bytes, info: ChunkInfo) -> None:
        c = Cursor(body)
        class_id = c.u32()
        class_name = c.text()
        c.u8()  # object format (1 = service)
        count = c.u32()
        refs = c.ref_array(count)
        self.class_names[class_id] = class_name
        self.class_refs[class_id] = refs
        info.class_name = class_name
        info.instance_count = count
        for ref in refs:
            inst = RobloxInstance(class_name, class_name)
            inst.referent = ref
            self.by_ref[ref] = inst

    def _prop(self, body: bytes, info: ChunkInfo) -> None:
        c = Cursor(body)
        class_id = c.u32()
        prop = canonical(c.text())
        type_id = c.u8()
        info.class_name = self.class_names.get(class_id)
        info.prop_name = prop
        refs = self.class_refs.get(class_id)
        if refs is None:
            raise ChunkError(f"property {prop!r} belongs to unknown class id {class_id}")
        info.instance_count = len(refs)
        values = _decode_values(c, type_id, len(refs), prop, self.shared)
        for ref, value in zip(refs, values):
            inst = self.by_ref.get(ref)
            if inst is None:
                continue
            inst.properties[prop] = value
            if prop == "Name" and isinstance(value, str):
                inst.name = value

    def _prnt(self, body: bytes, root: RobloxInstance) -> None:
        c = Cursor(body)
        c.u8()  # version
        count = c.u32()
        children = c.ref_array(count)
        parents = c.ref_array(count)
        self.parented = True
        for child_ref, parent_ref in zip(children, parents):
            child = self.by_ref.get(child_ref)
            if child is None or child.parent is not None:
                continue
            if parent_ref == -1:
                root.add_child(child)
            elif parent_ref in self.by_ref:
                self.by_ref[parent_ref].add_child(child)
            # else: parent was lost in a damaged chunk; handled as an orphan later

    def _finish(self, root: RobloxInstance) -> None:
        """Resolve references and decode attribute/tag blobs."""
        for inst in self.by_ref.values():
            for key, value in list(inst.properties.items()):
                if isinstance(value, Ref):
                    inst.properties[key] = Ref(value.referent, self.by_ref.get(value.referent))
            blob = inst.properties.get("AttributesSerialize")
            if blob:
                if isinstance(blob, str):
                    blob = blob.encode("utf-8")
                inst.attributes, problems = decode_attributes(blob)
                for p in problems:
                    self.warn(f"{inst.get_path()}: {p}")
            tags = inst.properties.get("Tags")
            if tags:
                inst.tags = decode_tags(tags)


def read_binary(data: bytes, path: str = "") -> Document:
    return BinaryReader().read(data, path)
