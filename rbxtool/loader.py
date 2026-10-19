"""Open any Roblox file. The format is detected from the content, not the extension."""

from __future__ import annotations

import os
from typing import Union

from .binary import MAGIC, read_binary
from .instance import Document
from .xml_format import read_xml

EXTENSIONS = (".rbxl", ".rbxm", ".rbxlx", ".rbxmx")


def load_bytes(data: bytes, path: str = "") -> Document:
    if data.startswith(MAGIC):
        return read_binary(data, path)
    head = data[:200].lstrip(b"\xef\xbb\xbf \t\r\n")
    if head.startswith(b"<roblox") or head.startswith(b"<?xml"):
        return read_xml(data, path)
    raise ValueError(f"{path or 'data'} is not a Roblox place or model file")


def load(path: Union[str, "os.PathLike[str]"]) -> Document:
    path = os.fspath(path)
    with open(path, "rb") as f:
        data = f.read()
    return load_bytes(data, path)
