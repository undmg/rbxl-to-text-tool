"""Export scripts to files using Rojo's naming, optionally with a Rojo project file.

    Script (server)          -> Name.server.luau
    LocalScript / client RC  -> Name.client.luau
    ModuleScript             -> Name.luau
    Script with children     -> Name/init.server.luau (+ children inside)
    Non-Folder containers    -> directory + init.meta.json {"className": ...}
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Set

from .instance import Document, RobloxInstance

_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}


def safe_name(name: str) -> str:
    cleaned = _BAD.sub("_", name).strip().rstrip(".") or "_"
    if cleaned.upper().split(".")[0] in _RESERVED:
        cleaned = "_" + cleaned
    return cleaned


@dataclass
class ExportResult:
    files: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _suffix(inst: RobloxInstance) -> str:
    kind = inst.script_kind
    if kind == "module":
        return ""
    return ".client" if kind == "client" else ".server"


def export_scripts(doc: Document, out_dir: str, ext: str = ".luau",
                   project: bool = False) -> ExportResult:
    result = ExportResult()
    ext = ext if ext.startswith(".") else "." + ext
    keep: Set[RobloxInstance] = set()
    for s in doc.scripts():
        keep.add(s)
        keep.update(s.ancestors())

    def write(path: str, content: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        result.files.append(path)

    def walk(inst: RobloxInstance, directory: str) -> None:
        used: Dict[str, int] = {}
        for child in inst.children:
            if child not in keep:
                continue
            name = safe_name(child.name)
            if name.lower() in used:
                used[name.lower()] += 1
                result.warnings.append(f"{child.get_path()}: duplicate name; exported as "
                                       f"'{name} ({used[name.lower()]})' — Rojo cannot keep "
                                       "two siblings with the same name")
                name = f"{name} ({used[name.lower()]})"
            else:
                used[name.lower()] = 1
            has_kids = any(c in keep for c in child.children)
            if child.is_script():
                if has_kids:
                    sub = os.path.join(directory, name)
                    write(os.path.join(sub, f"init{_suffix(child)}{ext}"), child.source)
                    walk(child, sub)
                else:
                    write(os.path.join(directory, f"{name}{_suffix(child)}{ext}"), child.source)
            else:
                sub = os.path.join(directory, name)
                if child.class_name != "Folder" and not child.is_service():
                    write(os.path.join(sub, "init.meta.json"),
                          json.dumps({"className": child.class_name}, indent=2) + "\n")
                walk(child, sub)

    src = os.path.join(out_dir, "src") if project else out_dir
    walk(doc.root, src)

    if project:
        tree: Dict[str, object] = {"$className": "DataModel"}
        for service in doc.root.children:
            if service in keep and service.is_service():
                tree[service.name] = {"$className": service.class_name,
                                      "$path": f"src/{safe_name(service.name)}"}
            elif service in keep:
                result.warnings.append(f"{service.name}: top-level non-service with scripts "
                                       "is exported under src/ but not mapped in the project")
        name = os.path.splitext(os.path.basename(doc.path))[0] or "place"
        write(os.path.join(out_dir, "default.project.json"),
              json.dumps({"name": name, "tree": tree}, indent=2) + "\n")
        for s in doc.scripts():
            if s.class_name == "Script" and s.script_kind == "client":
                result.warnings.append(
                    f"{s.get_path()}: Script with RunContext=Client exported as .client — Rojo "
                    "turns that into a LocalScript unless the project sets emitLegacyScripts=false")
                break
    return result
