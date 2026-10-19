import json
import os
import subprocess
import sys

import pytest

import rbxtool
from rbxtool.cli import main
from rbxtool.diff import diff_documents, render_markdown, render_text

from conftest import fixture


def run(capsys, *args):
    code = main(list(args))
    out = capsys.readouterr()
    return code, out.out, out.err


# --- diff -------------------------------------------------------------------

def test_diff_versions(v1, v2):
    d = diff_documents(v1, v2)
    assert [a.path for a in d.added] == ["ReplicatedStorage.Prices"]
    assert [r.path for r in d.removed] == ["Workspace.FreeTree.Trunk.Fire"]
    changed = {c.path: c for c in d.changed}
    lava = {p.name: (p.old, p.new) for p in changed["Workspace.Lava"].properties}
    assert lava["Damage"] == (25.0, 50.0)
    assert str(lava["Size"][1]) == "20, 1, 10"
    assert changed["ServerScriptService.Main"].source_changed
    assert changed["ServerScriptService.Analytics"].properties[0].name == "Disabled"


def test_diff_same_place_across_formats_is_empty():
    a = rbxtool.load(fixture("place_v1.rbxl"))
    b = rbxtool.load(fixture("place_v1.rbxlx"))
    assert diff_documents(a, b).empty


def test_diff_renderers(v1, v2):
    d = diff_documents(v1, v2)
    text = render_text(d)
    assert "+ ReplicatedStorage.Prices" in text and "@Damage: 25 -> 50" in text
    md = render_markdown(d)
    assert "```diff" in md and "| `Workspace.Lava` |" in md


# --- CLI --------------------------------------------------------------------

def test_cli_dump_json(capsys):
    code, out, _ = run(capsys, "dump", fixture("place_v1.rbxl"), "--json")
    data = json.loads(out)
    assert code == 0 and data["kind"] == "place"
    ws = data["instances"][0]
    lava = next(c for c in ws["children"] if c["name"] == "Lava")
    assert lava["attributes"]["Damage"] == 25 and lava["tags"] == ["Lava", "Hazard"]


def test_cli_check_exit_codes(capsys):
    assert run(capsys, "check", fixture("place_v1.rbxl"))[0] == 1
    assert run(capsys, "check", fixture("place_v1.rbxl"), "--fail-on", "never")[0] == 0
    assert run(capsys, "check", fixture("place_v1.rbxl"), "--rule", "RBX010",
               "--fail-on", "warning")[0] == 0
    assert run(capsys, "check", "does-not-exist.rbxl")[0] == 2


def test_cli_check_directory_and_formats(capsys):
    code, out, _ = run(capsys, "check", os.path.dirname(fixture("x")), "--format", "json")
    data = json.loads(out)
    assert len(data) == 5 and code == 1
    code, out, _ = run(capsys, "check", fixture("place_v1.rbxl"), "--format", "sarif")
    sarif = json.loads(out)
    assert sarif["version"] == "2.1.0" and sarif["runs"][0]["results"]
    code, out, _ = run(capsys, "check", fixture("place_v1.rbxl"), "--format", "github")
    assert out.startswith("::error file=")


def test_cli_diff_exit_code(capsys):
    assert run(capsys, "diff", fixture("place_v1.rbxl"), fixture("place_v1.rbxlx"),
               "--exit-code")[0] == 0
    code, out, _ = run(capsys, "diff", fixture("place_v1.rbxl"), fixture("place_v2.rbxl"),
                       "--exit-code", "--color", "never")
    assert code == 1 and "~ ServerScriptService.Main" in out


def test_cli_textconv_is_stable_and_never_fails(capsys, tmp_path):
    _, a, _ = run(capsys, "textconv", fixture("place_v1.rbxl"))
    _, b, _ = run(capsys, "textconv", fixture("place_v1.rbxl"))
    assert a == b and "[Workspace.Lava] Part" in a
    junk = tmp_path / "junk.rbxl"
    junk.write_bytes(b"garbage")
    code, out, _ = run(capsys, "textconv", str(junk))
    assert code == 0 and out.startswith("# rbxtool could not read")


def test_cli_export_rojo_layout(capsys, tmp_path):
    code, _, err = run(capsys, "export", fixture("place_v1.rbxl"), str(tmp_path), "--project")
    assert code == 0
    files = {str(p.relative_to(tmp_path)).replace(os.sep, "/") for p in tmp_path.rglob("*")
             if p.is_file()}
    assert {"default.project.json", "src/ServerScriptService/Main.server.luau",
            "src/ReplicatedStorage/Util.luau",
            "src/StarterPlayer/StarterPlayerScripts/Shop.client.luau",
            "src/Workspace/ClientRunner.client.luau",
            "src/Workspace/FreeTree/init.meta.json"} <= files
    project = json.loads((tmp_path / "default.project.json").read_text())
    assert project["tree"]["ServerScriptService"]["$path"] == "src/ServerScriptService"


def test_cli_map_and_stats(capsys):
    code, out, _ = run(capsys, "map", fixture("place_v1.rbxl"))
    assert code == 0 and "server handles: ServerScriptService.Main:5" in out
    code, out, _ = run(capsys, "stats", fixture("place_v1.rbxl"), "--json")
    assert json.loads(out)["instances"] == 59


def test_module_entry_point():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    res = subprocess.run([sys.executable, "-m", "rbxtool", "--version"],
                         capture_output=True, text=True, cwd=repo_root)
    assert res.returncode == 0 and res.stdout.startswith("rbxtool ")


@pytest.mark.skipif(not os.environ.get("RBXTOOL_TEST_GIT") and
                    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
                    reason="git not available")
def test_git_textconv_end_to_end(tmp_path):
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, PYTHONPATH=repo_root + os.pathsep + os.environ.get("PYTHONPATH", ""))

    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, text=True,
                              check=True, env=env).stdout

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    subprocess.run([sys.executable, "-m", "rbxtool", "git-setup", "--apply", "--use-python"],
                   cwd=tmp_path, check=True, capture_output=True, env=env)
    (tmp_path / "game.rbxl").write_bytes(open(fixture("place_v1.rbxl"), "rb").read())
    git("add", "-A")
    git("commit", "-qm", "v1")
    (tmp_path / "game.rbxl").write_bytes(open(fixture("place_v2.rbxl"), "rb").read())
    out = git("--no-pager", "diff")
    assert "+  @Damage = 50" in out
    assert "@@ [Workspace.Lava] Part" in out or "[Workspace.Lava] Part" in out
