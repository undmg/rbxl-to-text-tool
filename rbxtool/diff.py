"""Compare two place/model files: what was added, removed and changed."""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .instance import Document, RobloxInstance
from .values import to_display, to_json

# Properties that change on every save or carry no meaning for a review.
NOISY_PROPERTIES = frozenset({
    "UniqueId", "HistoryId", "ScriptGuid", "SourceAssetId", "DefinesCapabilities",
    "Capabilities", "AttributesSerialize", "Tags", "Source", "LinkedSource",
    "NeedsPivotMigration", "ModelMeshData", "PhysicsData", "MeshData", "ChildData",
    "PhysicalConfigData", "AeroMeshData", "FluidFidelityInternal", "InitialSize",
})


def path_keys(root: RobloxInstance) -> Dict[str, RobloxInstance]:
    """Map a unique path key to every instance (siblings with equal names get [n])."""
    out: Dict[str, RobloxInstance] = {}

    def walk(node: RobloxInstance, prefix: str) -> None:
        totals: Dict[str, int] = {}
        for child in node.children:
            totals[child.name] = totals.get(child.name, 0) + 1
        seen: Dict[str, int] = {}
        for child in node.children:
            seen[child.name] = seen.get(child.name, 0) + 1
            part = child.name if totals[child.name] == 1 else f"{child.name}[{seen[child.name]}]"
            key = f"{prefix}.{part}" if prefix else part
            out[key] = child
            walk(child, key)

    walk(root, "")
    return out


@dataclass
class PropertyChange:
    name: str
    old: Any
    new: Any
    kind: str = "property"  # "property", "attribute" or "tags"

    def to_json(self) -> dict:
        return {"kind": self.kind, "name": self.name, "old": to_json(self.old),
                "new": to_json(self.new)}


@dataclass
class InstanceChange:
    path: str
    class_name: str
    properties: List[PropertyChange] = field(default_factory=list)
    old_source: Optional[str] = None
    new_source: Optional[str] = None
    class_changed_from: Optional[str] = None

    @property
    def source_changed(self) -> bool:
        return self.old_source != self.new_source

    def source_diff(self, context: int = 3) -> List[str]:
        if not self.source_changed:
            return []
        return list(difflib.unified_diff(
            (self.old_source or "").splitlines(), (self.new_source or "").splitlines(),
            f"a/{self.path}", f"b/{self.path}", lineterm="", n=context))

    def line_counts(self) -> Tuple[int, int]:
        added = removed = 0
        for line in self.source_diff(0):
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1
        return added, removed

    def to_json(self) -> dict:
        out: dict = {"path": self.path, "class": self.class_name}
        if self.class_changed_from:
            out["class_changed_from"] = self.class_changed_from
        if self.properties:
            out["changes"] = [p.to_json() for p in self.properties]
        if self.source_changed:
            out["source_diff"] = "\n".join(self.source_diff())
        return out


@dataclass
class AddedOrRemoved:
    path: str
    class_name: str
    descendants: int
    scripts: List[str] = field(default_factory=list)
    instance: Optional[RobloxInstance] = None

    def to_json(self) -> dict:
        return {"path": self.path, "class": self.class_name, "descendants": self.descendants,
                "scripts": self.scripts}


@dataclass
class DiffResult:
    old: Document
    new: Document
    added: List[AddedOrRemoved] = field(default_factory=list)
    removed: List[AddedOrRemoved] = field(default_factory=list)
    changed: List[InstanceChange] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed)

    def scripts_changed(self) -> List[InstanceChange]:
        return [c for c in self.changed if c.source_changed]

    def to_json(self) -> dict:
        return {
            "old": {"path": self.old.path, "size": self.old.file_size},
            "new": {"path": self.new.path, "size": self.new.file_size},
            "added": [a.to_json() for a in self.added],
            "removed": [r.to_json() for r in self.removed],
            "changed": [c.to_json() for c in self.changed],
        }


def _summary(key: str, inst: RobloxInstance) -> AddedOrRemoved:
    desc = list(inst.descendants())
    scripts = [key] if inst.is_script() else []
    scripts += [d.get_path() for d in desc if d.is_script()]
    return AddedOrRemoved(key, inst.class_name, len(desc), scripts, inst)


def _values_equal(a: Any, b: Any) -> bool:
    if type(a) is type(b) and a == b:  # fast path: the common case is "unchanged"
        return True
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) <= 1e-6 * max(1.0, abs(a), abs(b))
    return to_json(a) == to_json(b)


def _defaults_only(value: Any) -> bool:
    """Binary files store every property; XML files only store some. Treat
    'missing' as equal to these common defaults to avoid noise across formats."""
    return value in (False, 0, 0.0, "", None) or (hasattr(value, "value") and value.value == 0)


def diff_documents(old: Document, new: Document, include_noise: bool = False) -> DiffResult:
    result = DiffResult(old, new)
    old_keys, new_keys = path_keys(old.root), path_keys(new.root)

    for key, inst in new_keys.items():
        if key not in old_keys:
            parent_key = key.rsplit(".", 1)[0] if "." in key else None
            if parent_key is None or parent_key in old_keys:
                result.added.append(_summary(key, inst))
    for key, inst in old_keys.items():
        if key not in new_keys:
            parent_key = key.rsplit(".", 1)[0] if "." in key else None
            if parent_key is None or parent_key in new_keys:
                result.removed.append(_summary(key, inst))

    for key, a in old_keys.items():
        b = new_keys.get(key)
        if b is None:
            continue
        change = InstanceChange(key, b.class_name)
        if a.class_name != b.class_name:
            change.class_changed_from = a.class_name
        for name in sorted(set(a.properties) | set(b.properties)):
            if name == "Name" or (name in NOISY_PROPERTIES and not include_noise):
                continue
            if name not in a.properties or name not in b.properties:
                present = a.properties.get(name, b.properties.get(name))
                # Binary files store every property of a class, XML files often don't, so a
                # property present on only one side says nothing when the formats differ.
                if old.format != new.format or _defaults_only(present):
                    continue
            va, vb = a.properties.get(name), b.properties.get(name)
            if not _values_equal(va, vb):
                change.properties.append(PropertyChange(name, va, vb))
        for name in sorted(set(a.attributes) | set(b.attributes)):
            va, vb = a.attributes.get(name), b.attributes.get(name)
            if name not in a.attributes or name not in b.attributes or not _values_equal(va, vb):
                change.properties.append(PropertyChange(name, va, vb, "attribute"))
        if sorted(a.tags) != sorted(b.tags):
            change.properties.append(PropertyChange("Tags", a.tags, b.tags, "tags"))
        if a.is_script() or b.is_script():
            change.old_source, change.new_source = a.source, b.source
        if change.properties or change.source_changed or change.class_changed_from:
            result.changed.append(change)
    return result


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _fmt_value(value: Any, kind: str) -> str:
    if kind == "tags":
        return "[" + ", ".join(value) + "]" if value else "[]"
    if value is None:
        return "(none)"
    return to_display(value, 80)


def render_text(result: DiffResult, color: bool = False, context: int = 3) -> str:
    def c(code: str, text: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if color else text

    out: List[str] = []
    if result.empty:
        return "No differences.\n"
    for a in result.added:
        extra = f" (+{a.descendants} inside)" if a.descendants else ""
        out.append(c("32", f"+ {a.path} ({a.class_name}){extra}"))
        for s in a.scripts:
            if s != a.path:
                out.append(c("32", f"    + script {s}"))
    for r in result.removed:
        extra = f" (+{r.descendants} inside)" if r.descendants else ""
        out.append(c("31", f"- {r.path} ({r.class_name}){extra}"))
        for s in r.scripts:
            if s != r.path:
                out.append(c("31", f"    - script {s}"))
    for ch in result.changed:
        added, removed = ch.line_counts()
        tail = f"  script +{added} -{removed}" if ch.source_changed else ""
        out.append(c("33", f"~ {ch.path} ({ch.class_name}){tail}"))
        if ch.class_changed_from:
            out.append(f"    class: {ch.class_changed_from} -> {ch.class_name}")
        for p in ch.properties:
            label = {"attribute": "@", "tags": "#"}.get(p.kind, "")
            out.append(f"    {label}{p.name}: {_fmt_value(p.old, p.kind)} -> {_fmt_value(p.new, p.kind)}")
        for line in ch.source_diff(context)[2:]:
            if line.startswith("+"):
                out.append(c("32", "    " + line))
            elif line.startswith("-"):
                out.append(c("31", "    " + line))
            elif line.startswith("@@"):
                out.append(c("36", "    " + line))
            else:
                out.append("    " + line)
    totals = (f"{len(result.added)} added, {len(result.removed)} removed, "
              f"{len(result.changed)} changed ({len(result.scripts_changed())} scripts)")
    out.append("")
    out.append(totals)
    return "\n".join(out) + "\n"


def render_markdown(result: DiffResult, context: int = 3) -> str:
    """For pull request comments."""
    if result.empty:
        return "**No differences.**\n"
    out = [f"### Place diff: {len(result.added)} added, {len(result.removed)} removed, "
           f"{len(result.changed)} changed", ""]
    if result.added:
        out.append("**Added**")
        out += [f"- `{a.path}` ({a.class_name})" + (f", {a.descendants} inside" if a.descendants else "")
                for a in result.added]
        out.append("")
    if result.removed:
        out.append("**Removed**")
        out += [f"- `{r.path}` ({r.class_name})" + (f", {r.descendants} inside" if r.descendants else "")
                for r in result.removed]
        out.append("")
    props = [ch for ch in result.changed if ch.properties]
    if props:
        out.append("**Changed properties**")
        out.append("")
        out.append("| Instance | Property | Old | New |")
        out.append("|---|---|---|---|")
        for ch in props:
            for p in ch.properties:
                old = _fmt_value(p.old, p.kind).replace("|", "\\|")
                new = _fmt_value(p.new, p.kind).replace("|", "\\|")
                label = {"attribute": "@", "tags": ""}.get(p.kind, "")
                out.append(f"| `{ch.path}` | {label}{p.name} | `{old}` | `{new}` |")
        out.append("")
    for ch in result.scripts_changed():
        added, removed = ch.line_counts()
        out.append(f"<details><summary><code>{ch.path}</code> (+{added} −{removed})</summary>")
        out.append("")
        out.append("```diff")
        out += ch.source_diff(context)[2:]
        out.append("```")
        out.append("</details>")
        out.append("")
    return "\n".join(out)
