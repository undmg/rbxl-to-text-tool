"""Roblox property value types.

Every decoded value is either a plain Python type (str, bytes, bool, int, float)
or one of the small classes below. All of them render readably with ``str()``
and convert to JSON-friendly data with :func:`to_json`.
"""

from __future__ import annotations

import base64
import math
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple


def fmt_num(value: float) -> str:
    """Format a float the way a person would write it (f32 noise removed)."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return str(value)
    if math.isnan(value):
        return "nan"
    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    text = format(value, ".7g")
    if "e" not in text and "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _round(value: float) -> float:
    if math.isnan(value) or math.isinf(value):
        return value
    return float(fmt_num(value))


@dataclass(frozen=True)
class Vector2:
    x: float
    y: float

    def __str__(self) -> str:
        return f"{fmt_num(self.x)}, {fmt_num(self.y)}"


@dataclass(frozen=True)
class Vector3:
    x: float
    y: float
    z: float

    def __str__(self) -> str:
        return f"{fmt_num(self.x)}, {fmt_num(self.y)}, {fmt_num(self.z)}"


@dataclass(frozen=True)
class Vector3int16:
    x: int
    y: int
    z: int

    def __str__(self) -> str:
        return f"{self.x}, {self.y}, {self.z}"


@dataclass(frozen=True)
class Color3:
    r: float
    g: float
    b: float

    def __str__(self) -> str:
        rgb = [max(0, min(255, round(c * 255))) for c in (self.r, self.g, self.b)]
        return f"rgb({rgb[0]}, {rgb[1]}, {rgb[2]})"


IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


@dataclass(frozen=True)
class CFrame:
    position: Vector3
    rotation: Tuple[float, ...] = IDENTITY  # row-major 3x3

    def __str__(self) -> str:
        if all(abs(a - b) < 1e-6 for a, b in zip(self.rotation, IDENTITY)):
            return f"pos({self.position})"
        rot = ", ".join(fmt_num(v) for v in self.rotation)
        return f"pos({self.position}) rot({rot})"


@dataclass(frozen=True)
class UDim:
    scale: float
    offset: int

    def __str__(self) -> str:
        return f"{{{fmt_num(self.scale)}, {self.offset}}}"


@dataclass(frozen=True)
class UDim2:
    x: UDim
    y: UDim

    def __str__(self) -> str:
        return f"{{{self.x}, {self.y}}}"


@dataclass(frozen=True)
class Rect:
    min: Vector2
    max: Vector2

    def __str__(self) -> str:
        return f"{self.min}, {self.max}"


@dataclass(frozen=True)
class Ray:
    origin: Vector3
    direction: Vector3

    def __str__(self) -> str:
        return f"origin({self.origin}) dir({self.direction})"


@dataclass(frozen=True)
class NumberRange:
    min: float
    max: float

    def __str__(self) -> str:
        return f"{fmt_num(self.min)} to {fmt_num(self.max)}"


@dataclass(frozen=True)
class NumberSequence:
    keypoints: Tuple[Tuple[float, float, float], ...]  # (time, value, envelope)

    def __str__(self) -> str:
        return " ".join(f"{fmt_num(t)}:{fmt_num(v)}" for t, v, _ in self.keypoints)


@dataclass(frozen=True)
class ColorSequence:
    keypoints: Tuple[Tuple[float, Color3], ...]  # (time, color)

    def __str__(self) -> str:
        return " ".join(f"{fmt_num(t)}:{c}" for t, c in self.keypoints)


@dataclass(frozen=True)
class EnumValue:
    value: int
    name: Optional[str] = None  # filled in for enums we know (e.g. RunContext)

    def __str__(self) -> str:
        return f"{self.name} ({self.value})" if self.name else f"enum {self.value}"


@dataclass(frozen=True)
class BrickColor:
    number: int

    def __str__(self) -> str:
        return f"BrickColor {self.number}"


@dataclass(frozen=True)
class Faces:
    bits: int

    def __str__(self) -> str:
        names = ["Right", "Top", "Back", "Left", "Bottom", "Front"]
        return ", ".join(n for i, n in enumerate(names) if self.bits & (1 << i)) or "none"


@dataclass(frozen=True)
class Axes:
    bits: int

    def __str__(self) -> str:
        names = ["X", "Y", "Z"]
        return ", ".join(n for i, n in enumerate(names) if self.bits & (1 << i)) or "none"


@dataclass(frozen=True)
class PhysicalProperties:
    custom: Optional[Tuple[float, ...]] = None  # None means default

    def __str__(self) -> str:
        if self.custom is None:
            return "default"
        return "custom(" + ", ".join(fmt_num(v) for v in self.custom) + ")"


@dataclass(frozen=True)
class Font:
    family: str
    weight: int
    style: str

    def __str__(self) -> str:
        return f"{self.family} weight={self.weight} {self.style}"


@dataclass(frozen=True)
class Content:
    uri: Optional[str] = None
    object_ref: Optional[int] = None

    def __str__(self) -> str:
        if self.uri:
            return self.uri
        if self.object_ref is not None:
            return f"<object {self.object_ref}>"
        return "<none>"


@dataclass(frozen=True)
class UniqueId:
    hex: str

    def __str__(self) -> str:
        return self.hex


@dataclass(frozen=True)
class Ref:
    """A reference to another instance. ``target`` is filled in after loading."""

    referent: Any
    target: Any = field(default=None, compare=False, hash=False, repr=False)

    def __str__(self) -> str:
        if self.target is not None:
            return f"-> {self.target.get_path() or self.target.class_name}"
        return "nil" if self.referent in (None, -1, "null") else f"-> <missing {self.referent}>"


@dataclass(frozen=True)
class Unsupported:
    """A property whose type this tool cannot decode yet."""

    type_name: str

    def __str__(self) -> str:
        return f"<{self.type_name}>"


def describe_bytes(data: bytes) -> str:
    import hashlib

    return f"<binary {len(data)} bytes sha1:{hashlib.sha1(data).hexdigest()[:10]}>"


def to_display(value: Any, max_len: int = 200) -> str:
    """Readable single-line text for any property value."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return fmt_num(value)
    if isinstance(value, bytes):
        return describe_bytes(value)
    if isinstance(value, str):
        text = value.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")
        if len(text) > max_len:
            text = text[:max_len] + f"... ({len(value)} chars)"
        return f'"{text}"'
    return str(value)


def to_json(value: Any) -> Any:
    """Convert any property value into JSON-serialisable data."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return _round(value) if math.isfinite(value) else str(value)
    if isinstance(value, bytes):
        return {"type": "binary", "size": len(value), "base64": base64.b64encode(value).decode()}
    if isinstance(value, Vector2):
        return {"type": "Vector2", "x": _round(value.x), "y": _round(value.y)}
    if isinstance(value, Vector3):
        return {"type": "Vector3", "x": _round(value.x), "y": _round(value.y), "z": _round(value.z)}
    if isinstance(value, Color3):
        return {"type": "Color3", "r": _round(value.r), "g": _round(value.g), "b": _round(value.b)}
    if isinstance(value, CFrame):
        return {
            "type": "CFrame",
            "position": to_json(value.position),
            "rotation": [_round(v) for v in value.rotation],
        }
    if isinstance(value, EnumValue):
        out: dict = {"type": "Enum", "value": value.value}
        if value.name:
            out["name"] = value.name
        return out
    if isinstance(value, Ref):
        return {"type": "Ref", "path": value.target.get_path() if value.target is not None else None}
    if isinstance(value, (list, tuple)):
        return [to_json(v) for v in value]
    if hasattr(value, "__dataclass_fields__"):
        out = {"type": type(value).__name__}
        for name in value.__dataclass_fields__:
            if name == "target":
                continue
            out[name] = to_json(getattr(value, name))
        return out
    return str(value)


# Enums whose item names matter for analysis. Binary files only store numbers.
KNOWN_ENUMS = {
    "RunContext": {0: "Legacy", 1: "Server", 2: "Client", 3: "Plugin"},
}


def name_enum(prop_name: str, value: int) -> EnumValue:
    names = KNOWN_ENUMS.get(prop_name)
    return EnumValue(value, names.get(value) if names else None)


__all__: List[str] = [
    "Axes", "BrickColor", "CFrame", "Color3", "ColorSequence", "Content", "EnumValue",
    "Faces", "Font", "NumberRange", "NumberSequence", "PhysicalProperties", "Ray", "Rect",
    "Ref", "UDim", "UDim2", "UniqueId", "Unsupported", "Vector2", "Vector3", "Vector3int16",
    "fmt_num", "name_enum", "to_display", "to_json", "describe_bytes",
]
