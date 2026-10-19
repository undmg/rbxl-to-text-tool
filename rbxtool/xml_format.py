"""Reader for Roblox XML files (.rbxlx places and .rbxmx models)."""

from __future__ import annotations

import base64
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from .attributes import decode_attributes, decode_tags
from .canonical import canonical
from .instance import Document, RobloxInstance
from .values import (
    Axes, BrickColor, CFrame, Color3, ColorSequence, Content, Faces, Font, NumberRange,
    NumberSequence, PhysicalProperties, Ray, Rect, Ref, UDim, UDim2, UniqueId, Vector2,
    Vector3, Vector3int16, name_enum,
)


def _f(el: Optional[ET.Element], tag: str, default: float = 0.0) -> float:
    if el is None:
        return default
    child = el.find(tag)
    try:
        return float(child.text) if child is not None and child.text else default
    except ValueError:
        return default


def _num_list(text: str) -> List[float]:
    out = []
    for part in text.split():
        try:
            out.append(float(part))
        except ValueError:
            pass
    return out


def _cframe(el: ET.Element) -> CFrame:
    pos = Vector3(_f(el, "X"), _f(el, "Y"), _f(el, "Z"))
    rot = tuple(_f(el, f"R{r}{c}", 1.0 if r == c else 0.0) for r in range(3) for c in range(3))
    return CFrame(pos, rot)


class XmlReader:
    def __init__(self) -> None:
        self.warnings: List[str] = []
        self.by_ref: Dict[str, RobloxInstance] = {}
        self.shared: Dict[str, Any] = {}

    def read(self, data: bytes, path: str = "") -> Document:
        root = RobloxInstance("DataModel", "Game")
        try:
            xml_root = ET.fromstring(data)
        except ET.ParseError as exc:
            self.warnings.append(f"damaged: XML is malformed ({exc}); salvaged scripts only")
            self._salvage(data, root)
            return Document(root=root, path=path, format="xml", file_size=len(data),
                            warnings=self.warnings, kind="model")

        shared_el = xml_root.find("SharedStrings")
        if shared_el is not None:
            for s in shared_el.findall("SharedString"):
                key = s.get("md5", "")
                self.shared[key] = self._decode_b64(s.text or "")

        metadata = {m.get("name", ""): m.text or "" for m in xml_root.findall("Meta")}

        for item in xml_root.findall("Item"):
            root.add_child(self._item(item))

        for inst in self.by_ref.values():
            for key, value in list(inst.properties.items()):
                if isinstance(value, Ref):
                    inst.properties[key] = Ref(value.referent, self.by_ref.get(value.referent))

        doc = Document(root=root, path=path, format="xml", file_size=len(data),
                       warnings=self.warnings, metadata=metadata)
        doc.kind = "place" if any(c.class_name == "Workspace" for c in root.children) else "model"
        return doc

    # ------------------------------------------------------------------

    @staticmethod
    def _decode_b64(text: str) -> Any:
        try:
            raw = base64.b64decode("".join(text.split()))
        except (ValueError, TypeError):
            return text
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw

    def _item(self, el: ET.Element) -> RobloxInstance:
        class_name = el.get("class", "Unknown")
        inst = RobloxInstance(class_name, class_name)
        ref = el.get("referent")
        if ref:
            inst.referent = ref
            self.by_ref[ref] = inst
        props = el.find("Properties")
        if props is not None:
            for prop in props:
                name = prop.get("name")
                if not name:
                    continue
                name = canonical(name)
                try:
                    value = self._value(prop, name)
                except Exception as exc:  # noqa: BLE001 - never let one property kill the load
                    self.warnings.append(f"{class_name}.{name}: could not decode ({exc})")
                    value = prop.text or ""
                inst.properties[name] = value
                if name == "Name" and isinstance(value, str):
                    inst.name = value
        blob = inst.properties.get("AttributesSerialize")
        if blob:
            data = blob.encode("utf-8") if isinstance(blob, str) else blob
            inst.attributes, problems = decode_attributes(data)
            self.warnings.extend(f"{inst.name}: {p}" for p in problems)
        if inst.properties.get("Tags"):
            inst.tags = decode_tags(inst.properties["Tags"])
        for child in el.findall("Item"):
            inst.add_child(self._item(child))
        return inst

    def _value(self, prop: ET.Element, name: str) -> Any:
        tag = prop.tag
        text = prop.text or ""
        if tag in ("string", "ProtectedString"):
            return text
        if tag == "BinaryString":
            return self._decode_b64(text)
        if tag == "bool":
            return text.strip().lower() == "true"
        if tag in ("int", "int64", "SecurityCapabilities"):
            return int(text.strip() or 0)
        if tag in ("float", "double"):
            return float(text.strip() or 0)
        if tag == "token":
            return name_enum(name, int(text.strip() or 0))
        if tag == "Vector3":
            return Vector3(_f(prop, "X"), _f(prop, "Y"), _f(prop, "Z"))
        if tag == "Vector2":
            return Vector2(_f(prop, "X"), _f(prop, "Y"))
        if tag == "Vector3int16":
            return Vector3int16(int(_f(prop, "X")), int(_f(prop, "Y")), int(_f(prop, "Z")))
        if tag == "Color3":
            if prop.find("R") is None and text.strip():
                n = int(text.strip())
                return Color3(((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255)
            return Color3(_f(prop, "R"), _f(prop, "G"), _f(prop, "B"))
        if tag == "Color3uint8":
            n = int(text.strip() or 0)
            return Color3(((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255)
        if tag == "CoordinateFrame":
            return _cframe(prop)
        if tag == "OptionalCoordinateFrame":
            cf = prop.find("CFrame")
            return _cframe(cf) if cf is not None else None
        if tag == "UDim":
            return UDim(_f(prop, "S"), int(_f(prop, "O")))
        if tag == "UDim2":
            return UDim2(UDim(_f(prop, "XS"), int(_f(prop, "XO"))),
                         UDim(_f(prop, "YS"), int(_f(prop, "YO"))))
        if tag == "Rect2D":
            mn, mx = prop.find("min"), prop.find("max")
            return Rect(Vector2(_f(mn, "X"), _f(mn, "Y")), Vector2(_f(mx, "X"), _f(mx, "Y")))
        if tag == "Ray":
            o, d = prop.find("origin"), prop.find("direction")
            return Ray(Vector3(_f(o, "X"), _f(o, "Y"), _f(o, "Z")),
                       Vector3(_f(d, "X"), _f(d, "Y"), _f(d, "Z")))
        if tag == "NumberRange":
            nums = _num_list(text) + [0.0, 0.0]
            return NumberRange(nums[0], nums[1])
        if tag == "NumberSequence":
            nums = _num_list(text)
            return NumberSequence(tuple(tuple(nums[i:i + 3]) for i in range(0, len(nums) - 2, 3)))
        if tag == "ColorSequence":
            nums = _num_list(text)
            return ColorSequence(tuple((nums[i], Color3(*nums[i + 1:i + 4]))
                                       for i in range(0, len(nums) - 4, 5)))
        if tag == "BrickColor":
            return BrickColor(int(text.strip() or 0))
        if tag == "Faces":
            return Faces(int(_f(prop, "faces")))
        if tag == "Axes":
            return Axes(int(_f(prop, "axes")))
        if tag == "PhysicalProperties":
            custom = prop.find("CustomPhysics")
            if custom is not None and (custom.text or "").strip().lower() == "true":
                fields = ["Density", "Friction", "Elasticity", "FrictionWeight", "ElasticityWeight"]
                vals = [_f(prop, f) for f in fields]
                if prop.find("AcousticAbsorption") is not None:
                    vals.append(_f(prop, "AcousticAbsorption"))
                return PhysicalProperties(tuple(vals))
            return PhysicalProperties(None)
        if tag == "Font":
            fam = prop.find("Family")
            family = ""
            if fam is not None:
                url = fam.find("url")
                family = (url.text if url is not None else fam.text) or ""
            style_el = prop.find("Style")
            return Font(family, int(_f(prop, "Weight", 400)),
                        (style_el.text if style_el is not None else "Normal") or "Normal")
        if tag in ("Content", "ContentId"):
            for child in prop:
                if child.tag in ("url", "uri"):
                    return Content(uri=child.text or "")
                if child.tag == "Ref":
                    return Content(object_ref=child.text)
            return Content(uri=text.strip() or None)
        if tag == "Ref":
            ref = text.strip()
            return Ref(None if ref in ("", "null") else ref)
        if tag == "SharedString":
            return self.shared.get(text.strip(), b"")
        if tag == "UniqueId":
            return UniqueId(text.strip())
        return text

    def _salvage(self, data: bytes, root: RobloxInstance) -> None:
        text = data.decode("utf-8", errors="replace")
        pattern = re.compile(r'<Item class="(Script|LocalScript|ModuleScript)"[^>]*>(.*?)</Properties>',
                             re.S)
        for match in pattern.finditer(text):
            body = match.group(2)
            name = re.search(r'<string name="Name">(.*?)</string>', body, re.S)
            src = re.search(r'<(?:Protected)?[Ss]tring name="Source">(?:<!\[CDATA\[)?(.*?)'
                            r'(?:\]\]>)?</(?:Protected)?[Ss]tring>', body, re.S)
            inst = RobloxInstance(match.group(1), name.group(1) if name else match.group(1))
            inst.properties["Source"] = src.group(1) if src else ""
            inst.orphan = True
            root.add_child(inst)
        self.warnings.append(f"salvaged {len(root.children)} scripts from the damaged XML")


def read_xml(data: bytes, path: str = "") -> Document:
    return XmlReader().read(data, path)
