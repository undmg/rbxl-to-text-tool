"""Resolve Luau expressions like ``game:GetService("ReplicatedStorage").Remotes.Buy``
to instances in the loaded file — statically, without running anything.

Handles: ``script``, ``game``, ``workspace``, ``:GetService``, ``.Parent``,
``.Child``, ``["Child"]``, ``:WaitForChild``, ``:FindFirstChild``,
``:FindFirstAncestor``, local aliases (``local RS = game:GetService(...)``) and
number literals (``require(12345)``). Anything that depends on runtime state
(``Players.LocalPlayer``, function results, ...) is reported as unresolved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union

from ..instance import RobloxInstance
from .luau import Scanned, scan

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER = re.compile(r"0[xX][0-9a-fA-F_]+|\d[\d_]*(?:\.\d+)?")
_LOCAL = re.compile(r"\blocal\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*[A-Za-z_][\w.<>]*\s*)?=\s*")

CHILD_METHODS = ("WaitForChild", "FindFirstChild")
ANCESTOR_METHODS = ("FindFirstAncestor",)


@dataclass
class Resolution:
    text: str
    target: Optional[RobloxInstance] = None
    number: Optional[int] = None
    last_name: Optional[str] = None  # final name in the chain, for name-based fallback

    @property
    def resolved(self) -> bool:
        return self.target is not None


@dataclass
class ScriptContext:
    script: RobloxInstance
    scanned: Scanned
    aliases: Dict[str, List[Tuple[int, int, int]]] = field(default_factory=dict)

    @classmethod
    def build(cls, script: RobloxInstance) -> "ScriptContext":
        scanned = scan(script.source)
        ctx = cls(script, scanned)
        code = scanned.code
        for m in _LOCAL.finditer(code):
            end = chain_forward(code, m.end())
            if end > m.end():
                ctx.aliases.setdefault(m.group(1), []).append((m.start(), m.end(), end))
        return ctx

    def alias_at(self, name: str, offset: int) -> Optional[Tuple[int, int]]:
        """The most recent ``local name = ...`` before ``offset``."""
        best = None
        for decl, start, end in self.aliases.get(name, []):
            if decl < offset:
                best = (start, end)
        return best


def _match_bracket(code: str, i: int, step: int) -> int:
    pairs = {"(": ")", "[": "]", ")": "(", "]": "["}
    open_ch, close_ch = code[i], pairs[code[i]]
    depth, j = 0, i
    while 0 <= j < len(code):
        if code[j] == open_ch:
            depth += 1
        elif code[j] == close_ch:
            depth -= 1
            if depth == 0:
                return j
        j += step
    return -1


def chain_forward(code: str, i: int) -> int:
    """End offset of an expression chain starting at ``i``."""
    n = len(code)
    while i < n and code[i] in " \t":
        i += 1
    start = i
    while i < n:
        ch = code[i]
        if ch.isalnum() or ch in "_.:":
            i += 1
        elif ch in "([":
            j = _match_bracket(code, i, 1)
            if j == -1:
                break
            i = j + 1
        elif ch in "\"'`" and i == start:
            j = code.find(ch, i + 1)
            if j == -1:
                break
            i = j + 1
        else:
            break
    return i


def chain_backward(code: str, i: int) -> int:
    """Start offset of the expression chain that ends just before ``i``."""
    j = i - 1
    while j >= 0:
        ch = code[j]
        if ch.isalnum() or ch in "_.:":
            j -= 1
        elif ch in ")]":
            k = _match_bracket(code, j, -1)
            if k == -1:
                break
            j = k - 1
        else:
            break
    return j + 1


def _string_arg(ctx: ScriptContext, open_paren: int, close_paren: int) -> Optional[str]:
    """First argument of a call if it is a plain string literal."""
    for s in ctx.scanned.strings:
        if open_paren < s.start < close_paren:
            inner = ctx.scanned.code[open_paren + 1:s.start].strip()
            return s.value if inner == "" else None
    return None


def resolve(ctx: ScriptContext, start: int, end: int, depth: int = 0) -> Resolution:
    code = ctx.scanned.code
    text = ctx.scanned.source[start:end].strip()
    res = Resolution(text)
    i = start
    while i < end and code[i] in " \t":
        i += 1

    num = _NUMBER.fullmatch(code[i:end].strip())
    if num:
        try:
            res.number = int(num.group(0).replace("_", ""), 0)
        except ValueError:
            pass
        return res

    m = _IDENT.match(code, i)
    if not m:
        return res
    base = m.group(0)
    i = m.end()
    cur: Union[RobloxInstance, None, str]
    root = ctx.script.root()

    if base == "script":
        cur = ctx.script
    elif base == "game":
        cur = root
    elif base in ("workspace", "Workspace") and ctx.alias_at(base, start) is None:
        cur = next((c for c in root.children if c.class_name == "Workspace"), None)
    else:
        alias = ctx.alias_at(base, start)
        if alias is None or depth > 8:
            res.last_name = base
            return res
        inner = resolve(ctx, alias[0], alias[1], depth + 1)
        if inner.number is not None and i >= end:
            res.number = inner.number
            return res
        cur = inner.target
        res.last_name = inner.last_name
        if cur is None:
            # keep walking names so the fallback name is the last one in the chain
            cur = "unresolved"

    while i < end:
        ch = code[i]
        if ch == ".":
            nm = _IDENT.match(code, i + 1)
            if not nm:
                break
            name = nm.group(0)
            i = nm.end()
            res.last_name = name
            if isinstance(cur, RobloxInstance):
                cur = cur.parent if name == "Parent" else cur.find_child(name)
                if cur is None:
                    cur = "unresolved"
        elif ch == ":":
            nm = _IDENT.match(code, i + 1)
            if not nm:
                break
            method = nm.group(0)
            j = nm.end()
            while j < end and code[j] in " \t":
                j += 1
            if j >= end or code[j] != "(":
                break
            close = _match_bracket(code, j, 1)
            if close == -1 or close >= end:
                break
            arg = _string_arg(ctx, j, close)
            i = close + 1
            if arg is not None:
                res.last_name = arg
            if not isinstance(cur, RobloxInstance) or arg is None:
                cur = "unresolved"
            elif method == "GetService":
                cur = next((c for c in cur.root().children
                            if c.class_name == arg or c.name == arg), None) or "unresolved"
            elif method in CHILD_METHODS:
                cur = cur.find_child(arg) or "unresolved"
            elif method in ANCESTOR_METHODS:
                cur = next((a for a in cur.ancestors() if a.name == arg), None) or "unresolved"
            else:
                cur = "unresolved"
        elif ch == "[":
            close = _match_bracket(code, i, 1)
            if close == -1:
                break
            key = _string_arg(ctx, i, close)
            i = close + 1
            if key is not None:
                res.last_name = key
            if isinstance(cur, RobloxInstance) and key is not None:
                cur = cur.find_child(key) or "unresolved"
            else:
                cur = "unresolved"
        elif ch in " \t":
            i += 1
        else:
            cur = "unresolved"
            break

    if isinstance(cur, RobloxInstance):
        res.target = cur
    return res
