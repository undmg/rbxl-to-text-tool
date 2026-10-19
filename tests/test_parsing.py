"""The fixtures are written by rbx-dom (tests/fixgen), the library Rojo and Lune use,
so these tests check the parsers against real Roblox-format files."""

import random

import pytest

import rbxtool
from rbxtool.values import Color3, Vector3

from conftest import fixture


def test_detects_format_and_kind(v1):
    assert v1.kind == "place"
    assert v1.format in ("binary", "xml")
    assert not v1.warnings


def test_tree_and_scripts(v1):
    paths = {i.get_path() for i in v1.instances()}
    assert "ServerScriptService.Main" in paths
    assert "StarterPlayer.StarterPlayerScripts.Shop" in paths
    assert sum(1 for _ in v1.instances()) == 59
    main = v1.find("ServerScriptService.Main")
    assert "BuyItem.OnServerEvent" in main.source


def test_typed_properties(v1):
    lava = v1.find("Workspace.Lava")
    assert lava.properties["Size"] == Vector3(10, 1, 10)
    assert lava.properties["Anchored"] is True
    color = lava.properties["Color"]
    assert isinstance(color, Color3) and round(color.r * 255) == 255
    assert str(lava.properties["CFrame"]) == "pos(0, 0.5, 0)"


def test_attributes_and_tags(v1):
    lava = v1.find("Workspace.Lava")
    assert lava.attributes == {"Damage": 25.0, "Enabled": True, "Label": "Hot"}
    assert lava.tags == ["Lava", "Hazard"]


def test_run_context_makes_script_client(v1):
    runner = v1.find("Workspace.ClientRunner")
    assert runner.class_name == "Script"
    assert runner.run_context == "Client"
    assert runner.script_kind == "client"
    assert v1.find("ServerScriptService.Main").script_kind == "server"
    assert v1.find("ReplicatedStorage.Util").script_kind == "module"


def test_binary_and_xml_agree():
    a = rbxtool.load(fixture("place_v1.rbxl"))
    b = rbxtool.load(fixture("place_v1.rbxlx"))
    assert [i.path_key() for i in a.instances()][:20] == [i.path_key() for i in b.instances()][:20]
    for sa, sb in zip(a.scripts(), b.scripts()):
        assert (sa.get_path(), sa.source) == (sb.get_path(), sb.source)


def test_model_file():
    doc = rbxtool.load(fixture("free_model.rbxm"))
    assert doc.kind == "model"
    assert [s.get_path() for s in doc.scripts()] == ["CoolCar.Fire"]


def test_rejects_non_roblox_data():
    with pytest.raises(ValueError):
        rbxtool.load_bytes(b"hello world, not a place file")


# --- damaged files ------------------------------------------------------

def _data():
    with open(fixture("place_v1.rbxl"), "rb") as f:
        return f.read()


@pytest.mark.parametrize("keep", [0.5, 0.75, 0.95])
def test_truncated_file_recovers_scripts(keep):
    data = _data()
    doc = rbxtool.load_bytes(data[: int(len(data) * keep)])
    assert doc.damaged
    assert len(doc.scripts()) >= 1
    assert any("truncated" in w or "END chunk missing" in w for w in doc.warnings)


def test_corrupted_chunk_only_loses_that_chunk():
    data = bytearray(_data())
    doc = rbxtool.load_bytes(bytes(data))
    chunk = next(c for c in doc.chunks if c.name == "PROP" and c.prop_name == "Size")
    for i in range(chunk.offset + 16, chunk.offset + chunk.compressed_size):
        data[i] ^= 0xFF
    damaged = rbxtool.load_bytes(bytes(data))
    assert damaged.damaged
    assert len(damaged.scripts()) == 6
    assert "Size" not in damaged.find("Workspace.Lava").properties


@pytest.mark.parametrize("seed", range(25))
def test_random_corruption_never_crashes(seed):
    rng = random.Random(seed)
    data = bytearray(_data())
    for _ in range(rng.randint(1, 40)):
        data[rng.randrange(32, len(data))] = rng.randrange(256)
    doc = rbxtool.load_bytes(bytes(data))  # must not raise
    assert doc.root is not None


def test_damaged_xml_salvages_scripts():
    with open(fixture("place_v1.rbxlx"), "rb") as f:
        data = f.read()
    cut = data.index(b">Analytics<") + 400
    doc = rbxtool.load_bytes(data[:cut])
    assert doc.damaged
    assert {"Main", "Util"} <= {s.name for s in doc.scripts()}
