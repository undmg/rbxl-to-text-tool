import rbxtool
from rbxtool.analysis.graph import build_graph
from rbxtool.analysis.luau import scan
from rbxtool.analysis.stats import compute_stats, parse_size
from rbxtool.checks import Config, run_checks, should_fail
from rbxtool.instance import Document, RobloxInstance
from rbxtool.rotation import basic_rotation

from conftest import fixture


def _doc_with(source: str, cls: str = "Script") -> Document:
    root = RobloxInstance("DataModel", "Game")
    sss = RobloxInstance("ServerScriptService", "ServerScriptService")
    root.add_child(sss)
    s = RobloxInstance(cls, "Test")
    s.properties["Source"] = source
    sss.add_child(s)
    return Document(root=root)


# --- Luau scanner -----------------------------------------------------------

def test_scanner_masks_strings_and_comments():
    src = 'local a = "require(1)" -- loadstring()\n--[[ getfenv() ]] local b = 1'
    sc = scan(src)
    assert "require" not in sc.code and "loadstring" not in sc.code and "getfenv" not in sc.code
    assert "local b = 1" in sc.code
    assert [s.value for s in sc.strings] == ["require(1)"]
    assert sc.line_of(src.index("local b")) == 2


def test_scanner_decodes_escapes():
    sc = scan(r'local s = "\108\111\97\100\115\116\114\105\110\103"')
    assert sc.strings[0].value == "loadstring"


# --- graph ------------------------------------------------------------------

def test_requires_resolve_through_aliases(v2):
    g = build_graph(v2)
    targets = {r.target.get_path() for r in g.requires if r.target}
    assert targets == {"ReplicatedStorage.Util", "ReplicatedStorage.Prices"}


def test_asset_require_detected(v1):
    g = build_graph(v1)
    assert [r.asset_id for r in g.requires if r.asset_id] == [4815162342]


def test_remote_wiring(v1):
    g = build_graph(v1)
    buy = next(r for r in g.remotes if r.name == "BuyItem")
    s = g.remote_summary(buy)
    assert [u.script.name for u in s["client_sends"]] == ["Shop"]
    assert [u.script.name for u in s["server_receives"]] == ["Main"]
    assert all(u.matched_by == "path" for u in g.uses_of(buy))


def test_resolver_handles_common_forms():
    src = '''
local RS = game:GetService("ReplicatedStorage")
local a = require(RS:WaitForChild("Mod"))
local b = require(game.ReplicatedStorage["Mod"])
local c = require(script.Parent.Parent.ReplicatedStorage.Mod)
'''
    doc = _doc_with(src)
    rs = RobloxInstance("ReplicatedStorage", "ReplicatedStorage")
    doc.root.add_child(rs)
    mod = RobloxInstance("ModuleScript", "Mod")
    mod.properties["Source"] = "return {}"
    rs.add_child(mod)
    g = build_graph(doc)
    test_requires = [r for r in g.requires if r.script.name == "Test"]
    assert len(test_requires) == 3
    assert all(r.target is mod for r in test_requires)


# --- checks -----------------------------------------------------------------

def _rules(findings):
    return sorted({f.rule for f in findings})


def test_checks_on_place(v1):
    found = run_checks(v1)
    assert _rules(found) == ["RBX001", "RBX003", "RBX004", "RBX005", "RBX010", "RBX021"]
    assert should_fail(found, "error")


def test_secret_is_never_printed(v1):
    for f in run_checks(v1):
        blob = f.message + f.snippet + f.fix
        assert "AbCdEfGhIjKl" not in blob


def test_hidden_keyword_via_escapes():
    found = run_checks(_doc_with(r'local f = getfenv()["\108\111\97\100\115\116\114\105\110\103"]'))
    assert "RBX004" in _rules(found)


def test_inline_suppression():
    src = "wait(1) -- rbxtool: ignore RBX010\nwait(2)\n"
    found = [f for f in run_checks(_doc_with(src)) if f.rule == "RBX010"]
    assert [f.line for f in found] == [2]
    found = [f for f in run_checks(_doc_with("-- rbxtool: ignore RBX010\nwait(1)\nwait(2)\n"))
             if f.rule == "RBX010"]
    assert [f.line for f in found] == [3]
    found = run_checks(_doc_with("-- rbxtool: ignore-file RBX010\nwait(1)\nwait(2)\n"))
    assert "RBX010" not in _rules(found)


def test_shadowed_wait_is_not_deprecated():
    assert "RBX010" not in _rules(run_checks(_doc_with("local function wait(n) end\nwait(1)")))


def test_instance_new_with_parent():
    found = run_checks(_doc_with('local p = Instance.new("Part", workspace)\n'
                                 'local q = Instance.new("Part")\n'))
    assert [f.line for f in found if f.rule == "RBX010"] == [1]


def test_remote_without_handler():
    doc = _doc_with("")
    rs = RobloxInstance("ReplicatedStorage", "ReplicatedStorage")
    doc.root.add_child(rs)
    rs.add_child(RobloxInstance("RemoteEvent", "Ping"))
    client = RobloxInstance("LocalScript", "C")
    client.properties["Source"] = 'game.ReplicatedStorage.Ping:FireServer()'
    rs.add_child(client)
    assert "RBX020" in _rules(run_checks(doc))


def test_config_controls_rules_and_failure(v1):
    cfg = Config.from_dict({"check": {"disable": ["RBX001"], "severity": {"RBX005": "info"},
                                      "ignore": ["Workspace.FreeTree.*"], "fail_on": "error"}})
    found = run_checks(v1, cfg)
    assert "RBX001" not in _rules(found)
    assert all(f.severity == "info" for f in found if f.rule == "RBX005")
    assert not any(f.path.startswith("Workspace.FreeTree") for f in found)
    assert not should_fail(found, cfg.fail_on)


def test_size_budget(v1):
    cfg = Config.from_dict({"check": {"max_file_size": "1KB"}})
    assert "RBX031" in _rules(run_checks(v1, cfg))


def test_parse_size():
    assert parse_size("5MB") == 5 * 1024 * 1024
    assert parse_size("500 KB") == 500 * 1024
    assert parse_size(1234) == 1234


# --- stats / rotation ------------------------------------------------------

def test_stats_binary_has_byte_sizes():
    st = compute_stats(rbxtool.load(fixture("place_v1.rbxl")))
    assert st.instance_count == 59 and st.script_count == 6
    assert dict(st.class_bytes)["Part"] > 0
    assert st.largest_values[0][0] == "ReplicatedStorage.BigData.Value"


def test_basic_rotation_ids():
    assert basic_rotation(0x02) == (1, 0, 0, 0, 1, 0, 0, 0, 1)
    assert basic_rotation(0x03) == (1, 0, 0, 0, 0, -1, 0, 1, 0)  # matches rbx_types
    assert basic_rotation(0x05) == (1, 0, 0, 0, -1, 0, 0, 0, -1)
