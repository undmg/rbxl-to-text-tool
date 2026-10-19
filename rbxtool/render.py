"""Text, JSON, Markdown and CI renderings for dump, textconv, map, stats and check."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from .analysis.graph import PlaceGraph
from .analysis.stats import Stats, human_bytes
from .checks import RULES, Finding
from .diff import NOISY_PROPERTIES
from .instance import Document, RobloxInstance
from .values import fmt_num, to_display, to_json


def _label(inst: RobloxInstance) -> str:
    bits = [inst.class_name]
    if inst.is_script():
        kind = inst.script_kind
        if inst.class_name == "Script" and kind != "server":
            bits.append(f"RunContext={inst.run_context}")
        lines = inst.source.count("\n") + (1 if inst.source and not inst.source.endswith("\n") else 0)
        bits.append(f"{lines} line{'' if lines == 1 else 's'}")
        if inst.disabled:
            bits.append("disabled")
    if inst.orphan:
        bits.append("recovered, parent unknown")
    return f"{inst.name} ({', '.join(bits)})"


def header(doc: Document) -> str:
    n = sum(1 for _ in doc.instances())
    return (f"{doc.path or 'file'}: {doc.format} {doc.kind}, {human_bytes(doc.file_size)}, "
            f"{n} instances, {len(doc.scripts())} scripts")


def render_tree(doc: Document, scripts: bool = False, props: bool = False,
                max_depth: Optional[int] = None) -> str:
    out = [header(doc)]
    for w in doc.warnings:
        out.append(f"! {w}")
    out.append("")

    def walk(inst: RobloxInstance, depth: int) -> None:
        pad = "  " * depth
        out.append(pad + _label(inst))
        if props:
            for k in sorted(inst.properties):
                if k in ("Name", "Source") or k in NOISY_PROPERTIES:
                    continue
                out.append(f"{pad}    .{k} = {to_display(inst.properties[k], 100)}")
            for k in sorted(inst.attributes):
                out.append(f"{pad}    @{k} = {to_display(inst.attributes[k], 100)}")
            if inst.tags:
                out.append(f"{pad}    #tags = {', '.join(inst.tags)}")
        if scripts and inst.is_script() and inst.source:
            for line in inst.source.splitlines():
                out.append(f"{pad}  | {line}")
        if max_depth is None or depth < max_depth:
            for child in inst.children:
                walk(child, depth + 1)
        elif inst.children:
            n = len(inst.children)
            out.append(f"{pad}  ... {n} {'child' if n == 1 else 'children'}")

    for child in doc.root.children:
        walk(child, 0)
    return "\n".join(out) + "\n"


def _textconv_value(value: Any) -> str:
    if isinstance(value, str) and len(value) > 200:
        digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:10]
        return to_display(value[:80], 80) + f" ...({len(value)} chars sha1:{digest})"
    return to_display(value, 200)


def render_textconv(doc: Document) -> str:
    """Stable text form for `git diff`. One self-describing line per fact, so a
    changed line in the diff always says which instance it belongs to."""
    from .diff import path_keys

    out = [f"# rbxtool: {doc.format} {doc.kind}"]
    for w in doc.warnings:
        out.append(f"# warning: {w}")
    for key, inst in path_keys(doc.root).items():
        out.append("")
        kind = f", {inst.script_kind}" if inst.is_script() else ""
        out.append(f"[{key}] {inst.class_name}{kind}")
        for k in sorted(inst.properties):
            if k in ("Name", "Source") or k in NOISY_PROPERTIES:
                continue
            out.append(f"  {k} = {_textconv_value(inst.properties[k])}")
        for k in sorted(inst.attributes):
            out.append(f"  @{k} = {_textconv_value(inst.attributes[k])}")
        if inst.tags:
            out.append(f"  #tags = {', '.join(sorted(inst.tags))}")
        if inst.is_script():
            for line in inst.source.splitlines():
                out.append(f"  | {line}")
    return "\n".join(out) + "\n"


def instance_json(inst: RobloxInstance, scripts: bool = True) -> Dict[str, Any]:
    data: Dict[str, Any] = {"name": inst.name, "class": inst.class_name, "path": inst.get_path()}
    if inst.is_script():
        data["kind"] = inst.script_kind
        data["disabled"] = inst.disabled
        if scripts:
            data["source"] = inst.source
    props = {k: to_json(v) for k, v in sorted(inst.properties.items())
             if k not in ("Name", "Source") and k not in NOISY_PROPERTIES}
    if props:
        data["properties"] = props
    if inst.attributes:
        data["attributes"] = {k: to_json(v) for k, v in sorted(inst.attributes.items())}
    if inst.tags:
        data["tags"] = list(inst.tags)
    if inst.orphan:
        data["recovered_orphan"] = True
    if inst.children:
        data["children"] = [instance_json(c, scripts) for c in inst.children]
    return data


def document_json(doc: Document, scripts: bool = True) -> Dict[str, Any]:
    return {
        "file": doc.path,
        "format": doc.format,
        "kind": doc.kind,
        "size": doc.file_size,
        "warnings": doc.warnings,
        "metadata": doc.metadata,
        "instances": [instance_json(c, scripts) for c in doc.root.children],
    }


# ---------------------------------------------------------------------------
# map
# ---------------------------------------------------------------------------

def render_map(doc: Document, graph: PlaceGraph, markdown: bool = False) -> str:
    code = (lambda s: f"`{s}`") if markdown else (lambda s: s)
    h = (lambda s: f"### {s}") if markdown else (lambda s: s.upper())
    out = [header(doc), ""]

    scripts = sorted(doc.scripts(), key=lambda s: (s.script_kind or "", s.get_path()))
    out.append(h("Scripts"))
    for s in scripts:
        if s.script_kind == "module":
            continue
        reqs = [r for r in graph.requires if r.script is s]
        line = f"- {s.script_kind:<6} {code(s.get_path())}"
        if s.disabled:
            line += " (disabled)"
        if reqs:
            names = [code(r.target.get_path()) if r.target else
                     (f"asset {r.asset_id}" if r.asset_id is not None else f"? {r.text}")
                     for r in reqs]
            line += " — requires " + ", ".join(names)
        out.append(line)
    out.append("")

    modules = [s for s in scripts if s.script_kind == "module"]
    if modules:
        out.append(h("Modules"))
        for m in modules:
            users = graph.required_by(m)
            who = ", ".join(sorted({code(u.script.get_path()) for u in users})) or "nothing (unused?)"
            out.append(f"- {code(m.get_path())} — required by {who}")
        out.append("")

    if graph.remotes:
        out.append(h("Remotes"))
        for r in graph.remotes:
            s = graph.remote_summary(r)
            out.append(f"- {code(r.get_path())} ({r.class_name})")
            labels = (("client_sends", "client fires"), ("server_receives", "server handles"),
                      ("server_sends", "server fires"), ("client_receives", "client handles"))
            empty = True
            for key, label in labels:
                if s[key]:
                    empty = False
                    where = ", ".join(f"{code(u.script.get_path())}:{u.line}"
                                      + (" (matched by name)" if u.matched_by == "name" else "")
                                      for u in s[key])
                    out.append(f"    {label}: {where}")
            if empty:
                out.append("    not referenced by any script")
        out.append("")

    unresolved = [u for u in graph.remote_uses if u.remote is None]
    asset = [r for r in graph.requires if r.asset_id is not None]
    if asset or unresolved:
        out.append(h("Needs attention"))
        for r in asset:
            out.append(f"- {code(r.script.get_path())}:{r.line} loads code by asset ID "
                       f"({r.asset_id})")
        for u in unresolved:
            out.append(f"- {code(u.script.get_path())}:{u.line} {u.member} on "
                       f"{code(u.expression)} — couldn't tell which remote")
        out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------

def render_stats(st: Stats) -> str:
    out = [f"Size: {human_bytes(st.file_size)} ({st.format})",
           f"Instances: {st.instance_count}",
           f"Scripts: {st.script_count} ({st.script_lines} lines)", ""]
    if st.class_bytes:
        out.append("Bytes on disk by class:")
        total = st.file_size or 1
        for cls, b in st.class_bytes:
            out.append(f"  {human_bytes(b):>10}  {b * 100 / total:5.1f}%  {cls}")
        out.append("")
        out.append("Heaviest properties:")
        for prop, b in st.property_bytes:
            out.append(f"  {human_bytes(b):>10}  {prop}")
        out.append("")
    else:
        out.append("(Byte sizes per class are only available for binary .rbxl/.rbxm files.)")
        out.append("")
    out.append("Most instances:")
    for cls, n in st.classes:
        out.append(f"  {n:>8}  {cls}")
    if st.largest_scripts:
        out.append("")
        out.append("Largest scripts:")
        for path, lines, chars in st.largest_scripts:
            out.append(f"  {lines:>6} lines  {path}")
    if st.largest_values:
        out.append("")
        out.append("Large stored values:")
        for path, b in st.largest_values:
            out.append(f"  {human_bytes(b):>10}  {path}")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------

_SEV_COLOR = {"error": "31", "warning": "33", "info": "36"}


def render_findings_text(file: str, findings: List[Finding], color: bool = False) -> str:
    if not findings:
        return f"{file}: no problems found\n"
    out = []
    for f in findings:
        sev = f.severity.upper()
        if color:
            sev = f"\x1b[{_SEV_COLOR[f.severity]}m{sev}\x1b[0m"
        where = f.path + (f":{f.line}" if f.line else "")
        out.append(f"{file}: {sev} {f.rule} {f.title}")
        if where:
            out.append(f"    at {where}")
        if f.snippet:
            out.append(f"    > {f.snippet}")
        out.append(f"    {f.message}")
        if f.fix:
            out.append(f"    fix: {f.fix}")
        out.append("")
    counts = {s: sum(1 for f in findings if f.severity == s) for s in ("error", "warning", "info")}
    def n(k: str, word: str) -> str:
        return f"{counts[k]} {word}{'' if counts[k] == 1 else 's'}"

    out.append(f"{file}: {n('error', 'error')}, {n('warning', 'warning')}, {counts['info']} info")
    return "\n".join(out) + "\n"


def render_findings_markdown(results: Dict[str, List[Finding]]) -> str:
    total = sum(len(v) for v in results.values())
    icon = {"error": "🔴", "warning": "🟡", "info": "🔵"}
    out = [f"### rbxtool check: {total} finding{'s' if total != 1 else ''}", ""]
    if not total:
        out.append("No problems found.")
    for file, findings in results.items():
        if not findings:
            continue
        out += [f"**{file}**", "", "| | Rule | Where | Problem |", "|---|---|---|---|"]
        for f in findings:
            where = f"`{f.path}`" + (f" line {f.line}" if f.line else "")
            msg = f"**{f.title}.** {f.message}".replace("|", "\\|").replace("\n", " ")
            out.append(f"| {icon[f.severity]} | {f.rule} | {where} | {msg} |")
        out.append("")
    return "\n".join(out) + "\n"


def render_findings_github(results: Dict[str, List[Finding]]) -> str:
    """GitHub Actions workflow commands: findings show up as annotations on the PR."""
    level = {"error": "error", "warning": "warning", "info": "notice"}

    def esc(s: str) -> str:
        return s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")

    out = []
    for file, findings in results.items():
        for f in findings:
            where = f.path + (f" line {f.line}" if f.line else "")
            title = esc(f"{f.rule} {f.title}").replace(",", "%2C").replace(":", "%3A")
            out.append(f"::{level[f.severity]} file={file},title={title}::"
                       + esc(f"{where}: {f.message}" + (f" Fix: {f.fix}" if f.fix else "")))
    return "\n".join(out) + ("\n" if out else "")


def render_findings_sarif(results: Dict[str, List[Finding]], version: str) -> str:
    level = {"error": "error", "warning": "warning", "info": "note"}
    rules = [{
        "id": r.id, "name": r.name,
        "shortDescription": {"text": r.summary},
        "defaultConfiguration": {"level": level[r.severity]},
    } for r in RULES.values()]
    sarif_results = []
    for file, findings in results.items():
        for f in findings:
            where = f.path + (f" line {f.line}" if f.line else "")
            sarif_results.append({
                "ruleId": f.rule,
                "level": level[f.severity],
                "message": {"text": f"{where}: {f.message}" + (f" Fix: {f.fix}" if f.fix else "")},
                "locations": [{
                    "physicalLocation": {"artifactLocation": {"uri": file.replace("\\", "/")}},
                    "logicalLocations": [{"fullyQualifiedName": f.path or file}],
                }],
            })
    doc = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {"name": "rbxtool", "version": version,
                                "informationUri": "https://github.com/undmg/rbxl-to-text-tool",
                                "rules": rules}},
            "results": sarif_results,
        }],
    }
    return json.dumps(doc, indent=2) + "\n"
