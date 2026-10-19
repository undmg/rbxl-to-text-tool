"""Decoding of the ``AttributesSerialize`` and ``Tags`` blobs.

Both file formats store attributes as one opaque binary string per instance.
Layout (all little-endian): u32 count, then per attribute a u32-length key,
a one-byte type id and the value.
"""

from __future__ import annotations

import struct
from typing import Any, Dict, List, Tuple

from .values import (
    BrickColor, CFrame, Color3, ColorSequence, EnumValue, Font, NumberRange,
    NumberSequence, Rect, UDim, UDim2, Unsupported, Vector2, Vector3,
)
from .rotation import basic_rotation


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise ValueError("attribute data ends early")
        chunk = self.data[self.pos:self.pos + n]
        self.pos += n
        return chunk

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return struct.unpack("<H", self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def i32(self) -> int:
        return struct.unpack("<i", self.take(4))[0]

    def f32(self) -> float:
        return struct.unpack("<f", self.take(4))[0]

    def f64(self) -> float:
        return struct.unpack("<d", self.take(8))[0]

    def string(self) -> bytes:
        return self.take(self.u32())

    def text(self) -> str:
        return self.string().decode("utf-8", errors="replace")

    def vector2(self) -> Vector2:
        return Vector2(self.f32(), self.f32())

    def vector3(self) -> Vector3:
        return Vector3(self.f32(), self.f32(), self.f32())

    def color3(self) -> Color3:
        return Color3(self.f32(), self.f32(), self.f32())

    def udim(self) -> UDim:
        return UDim(self.f32(), self.i32())


def _read_value(r: _Reader, type_id: int) -> Any:
    if type_id == 0x02:  # string (may be binary)
        raw = r.string()
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw
    if type_id == 0x03:
        return r.u8() != 0
    if type_id == 0x04:
        return r.i32()
    if type_id == 0x05:
        return r.f32()
    if type_id == 0x06:
        return r.f64()
    if type_id == 0x09:
        return r.udim()
    if type_id == 0x0A:
        return UDim2(r.udim(), r.udim())
    if type_id == 0x0E:
        return BrickColor(r.u32())
    if type_id == 0x0F:
        return r.color3()
    if type_id == 0x10:
        return r.vector2()
    if type_id == 0x11:
        return r.vector3()
    if type_id == 0x14:
        position = r.vector3()
        rot_id = r.u8()
        if rot_id == 0:
            rotation = tuple(r.f32() for _ in range(9))
        else:
            rotation = basic_rotation(rot_id)
        return CFrame(position, rotation)
    if type_id == 0x15:
        enum_type = r.text()
        return EnumValue(r.u32(), enum_type)
    if type_id == 0x17:
        count = r.u32()
        kps = []
        for _ in range(count):
            envelope, time, value = r.f32(), r.f32(), r.f32()
            kps.append((time, value, envelope))
        return NumberSequence(tuple(kps))
    if type_id == 0x19:
        count = r.u32()
        kps = []
        for _ in range(count):
            r.f32()  # envelope, always zero
            time = r.f32()
            kps.append((time, r.color3()))
        return ColorSequence(tuple(kps))
    if type_id == 0x1B:
        return NumberRange(r.f32(), r.f32())
    if type_id == 0x1C:
        return Rect(r.vector2(), r.vector2())
    if type_id == 0x21:
        weight = r.u16()
        style = "Italic" if r.u8() == 1 else "Normal"
        family = r.text()
        r.string()  # cached face id
        return Font(family, weight, style)
    raise ValueError(f"unknown attribute type 0x{type_id:02x}")


def decode_attributes(data: bytes) -> Tuple[Dict[str, Any], List[str]]:
    """Return ``(attributes, problems)``. Never raises."""
    attrs: Dict[str, Any] = {}
    problems: List[str] = []
    if not data:
        return attrs, problems
    r = _Reader(data)
    try:
        count = r.u32()
        for _ in range(count):
            key = r.text()
            type_id = r.u8()
            try:
                attrs[key] = _read_value(r, type_id)
            except ValueError as exc:
                # Without knowing the value's length we cannot continue past it.
                attrs[key] = Unsupported(f"attribute type 0x{type_id:02x}")
                problems.append(f"attribute {key!r}: {exc}")
                break
    except ValueError as exc:
        problems.append(str(exc))
    return attrs, problems


def decode_tags(data: Any) -> List[str]:
    if isinstance(data, str):
        data = data.encode("utf-8")
    if not data:
        return []
    return [t.decode("utf-8", errors="replace") for t in data.split(b"\x00") if t]
