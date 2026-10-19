"""A small Luau scanner.

It does not build a syntax tree; it separates code from strings and comments so
pattern checks don't fire on text inside comments, and so string contents can be
inspected on their own. Positions are preserved, so line numbers stay correct.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import List


@dataclass(frozen=True)
class StringLiteral:
    value: str
    line: int
    start: int  # offset of the opening quote in the source
    end: int  # offset just past the closing quote


@dataclass
class Scanned:
    source: str
    code: str  # strings and comments replaced by spaces (newlines kept)
    strings: List[StringLiteral] = field(default_factory=list)
    comments: List[StringLiteral] = field(default_factory=list)
    _line_starts: List[int] = field(default_factory=list)

    def line_of(self, offset: int) -> int:
        import bisect

        return bisect.bisect_right(self._line_starts, offset)

    def line_text(self, line: int) -> str:
        lines = self.source.splitlines()
        return lines[line - 1] if 0 < line <= len(lines) else ""


_LONG_OPEN = re.compile(r"\[(=*)\[")
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'", "a": "\a",
            "b": "\b", "f": "\f", "v": "\v", "0": "\0"}


def _unescape(raw: str) -> str:
    out, i = [], 0
    while i < len(raw):
        ch = raw[i]
        if ch != "\\" or i + 1 >= len(raw):
            out.append(ch)
            i += 1
            continue
        nxt = raw[i + 1]
        if nxt.isdigit():
            m = re.match(r"\d{1,3}", raw[i + 1:])
            out.append(chr(min(int(m.group(0)), 255)))
            i += 1 + len(m.group(0))
        elif nxt == "x" and re.match(r"[0-9a-fA-F]{2}", raw[i + 2:i + 4]):
            out.append(chr(int(raw[i + 2:i + 4], 16)))
            i += 4
        elif nxt == "z":
            i += 2
            while i < len(raw) and raw[i].isspace():
                i += 1
        else:
            out.append(_ESCAPES.get(nxt, nxt))
            i += 2
    return "".join(out)


@lru_cache(maxsize=512)
def scan(source: str) -> Scanned:
    n = len(source)
    code = list(source)
    strings: List[StringLiteral] = []
    comments: List[StringLiteral] = []
    line_starts = [0] + [m.end() for m in re.finditer(r"\n", source)]

    def line_at(off: int) -> int:
        import bisect

        return bisect.bisect_right(line_starts, off)

    def blank(a: int, b: int) -> None:
        for k in range(a, min(b, n)):
            if code[k] != "\n":
                code[k] = " "

    i = 0
    while i < n:
        ch = source[i]
        if ch == "-" and source.startswith("--", i):
            m = _LONG_OPEN.match(source, i + 2)
            if m:
                close = "]" + m.group(1) + "]"
                end = source.find(close, m.end())
                end = n if end == -1 else end + len(close)
            else:
                end = source.find("\n", i)
                end = n if end == -1 else end
            comments.append(StringLiteral(source[i:end], line_at(i), i, end))
            blank(i, end)
            i = end
            continue
        if ch in "\"'`":
            j = i + 1
            while j < n and source[j] != ch and source[j] != "\n":
                j += 2 if source[j] == "\\" else 1
            end = min(j + 1, n)
            raw = source[i + 1:min(j, n)]
            strings.append(StringLiteral(_unescape(raw), line_at(i), i, end))
            blank(i + 1, end - 1)  # keep the quotes so code still reads as a string token
            i = end
            continue
        if ch == "[":
            m = _LONG_OPEN.match(source, i)
            if m:
                close = "]" + m.group(1) + "]"
                end = source.find(close, m.end())
                end = n if end == -1 else end + len(close)
                strings.append(StringLiteral(source[m.end():max(m.end(), end - len(close))],
                                             line_at(i), i, end))
                blank(i + 1, end - 1)
                code[i] = '"'
                if end - 1 < n:
                    code[end - 1] = '"'
                i = end
                continue
        i += 1

    return Scanned(source, "".join(code), strings, comments, line_starts)


def string_at(scanned: Scanned, offset: int) -> str:
    """Value of the string literal that starts at ``offset`` (used by resolvers)."""
    for s in scanned.strings:
        if s.start == offset:
            return s.value
    return ""
