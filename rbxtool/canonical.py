"""Files store some properties under old "serialized" names (e.g. Part.Size is
saved as ``size``). This maps the common ones back to the names developers use."""

SERIALIZED_TO_CANONICAL = {
    "size": "Size",
    "Color3uint8": "Color",
    "shape": "Shape",
    "formFactorRaw": "FormFactor",
    "archivable": "Archivable",
    "Health_XML": "Health",
    "xmlRead_MaxDistance_3": "MaxDistance",
    "MaxDistance": "MaxDistance",
    "size_xml": "Size",
    "heat_xml": "Heat",
    "secondaryColor_xml": "SecondaryColor",
    "Color3uint8_xml": "Color",
    "InternalBodyScale": "BodyScale",
    "InternalHeadScale": "HeadScale",
    "ScriptGuid": "ScriptGuid",
}


def canonical(name: str) -> str:
    return SERIALIZED_TO_CANONICAL.get(name, name)
