"""What makes a file big. For binary files, sizes are real on-disk (compressed)
bytes taken from the chunk table — something a Studio plugin cannot see."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from ..instance import Document


@dataclass
class Stats:
    file_size: int
    format: str
    instance_count: int
    script_count: int
    script_lines: int
    classes: List[Tuple[str, int]] = field(default_factory=list)  # (class, instances)
    class_bytes: List[Tuple[str, int]] = field(default_factory=list)  # (class, on-disk bytes)
    property_bytes: List[Tuple[str, int]] = field(default_factory=list)  # (Class.Prop, bytes)
    largest_scripts: List[Tuple[str, int, int]] = field(default_factory=list)  # (path, lines, chars)
    largest_values: List[Tuple[str, int]] = field(default_factory=list)  # (path.Prop, bytes)

    def to_json(self) -> dict:
        return {
            "file_size": self.file_size,
            "format": self.format,
            "instances": self.instance_count,
            "scripts": self.script_count,
            "script_lines": self.script_lines,
            "classes": [{"class": c, "count": n} for c, n in self.classes],
            "bytes_by_class": [{"class": c, "bytes": b} for c, b in self.class_bytes],
            "bytes_by_property": [{"property": p, "bytes": b} for p, b in self.property_bytes],
            "largest_scripts": [{"path": p, "lines": l, "chars": c}
                                for p, l, c in self.largest_scripts],
            "largest_values": [{"property": p, "bytes": b} for p, b in self.largest_values],
        }


def compute_stats(doc: Document, top: int = 10) -> Stats:
    instances = list(doc.instances())
    scripts = [i for i in instances if i.is_script()]
    counts = Counter(i.class_name for i in instances)

    class_bytes: Dict[str, int] = defaultdict(int)
    prop_bytes: Dict[str, int] = defaultdict(int)
    for chunk in doc.chunks:
        if chunk.class_name and chunk.name in ("INST", "PROP"):
            class_bytes[chunk.class_name] += chunk.compressed_size
        if chunk.name == "PROP" and chunk.prop_name:
            prop_bytes[f"{chunk.class_name}.{chunk.prop_name}"] += chunk.compressed_size

    values = []
    for inst in instances:
        for key, value in inst.properties.items():
            if key in ("Source", "Name"):
                continue
            if isinstance(value, (str, bytes)) and len(value) > 1024:
                size = len(value.encode("utf-8")) if isinstance(value, str) else len(value)
                values.append((f"{inst.get_path()}.{key}", size))

    script_info = []
    total_lines = 0
    for s in scripts:
        src = s.source
        lines = src.count("\n") + (1 if src and not src.endswith("\n") else 0)
        total_lines += lines
        script_info.append((s.get_path(), lines, len(src)))

    return Stats(
        file_size=doc.file_size,
        format=doc.format,
        instance_count=len(instances),
        script_count=len(scripts),
        script_lines=total_lines,
        classes=counts.most_common(top),
        class_bytes=sorted(class_bytes.items(), key=lambda kv: -kv[1])[:top],
        property_bytes=sorted(prop_bytes.items(), key=lambda kv: -kv[1])[:top],
        largest_scripts=sorted(script_info, key=lambda t: (-t[1], -t[2]))[:top],
        largest_values=sorted(values, key=lambda kv: -kv[1])[:top],
    )


def human_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def parse_size(text) -> int:
    """'5MB', '500 KB', 1048576 -> bytes."""
    if isinstance(text, (int, float)):
        return int(text)
    t = str(text).strip().upper().replace(" ", "")
    for suffix, mult in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10), ("B", 1)):
        if t.endswith(suffix):
            return int(float(t[: -len(suffix)]) * mult)
    return int(float(t))
