"""Command line interface.

Exit codes: 0 = fine, 1 = problems found (check) or differences (diff --exit-code),
2 = the tool could not do its job (bad arguments, unreadable file).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Dict, List, Optional

from . import __version__
from .loader import EXTENSIONS, load


def _out(text: str) -> None:
    sys.stdout.write(text)


def _color(choice: str) -> bool:
    if choice == "always":
        return True
    if choice == "never" or os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _expand(paths: List[str]) -> List[str]:
    """Files as given, plus every Roblox file inside any directories."""
    out = []
    for p in paths:
        if os.path.isdir(p):
            for dirpath, _dirs, files in os.walk(p):
                out += [os.path.join(dirpath, f) for f in sorted(files)
                        if f.lower().endswith(EXTENSIONS)]
        else:
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_dump(a) -> int:
    from .render import document_json, render_tree

    doc = load(a.file)
    if a.json:
        _out(json.dumps(document_json(doc, scripts=not a.no_source), indent=2, ensure_ascii=False) + "\n")
    else:
        _out(render_tree(doc, scripts=a.scripts, props=a.props, max_depth=a.depth))
    return 0


def cmd_textconv(a) -> int:
    from .render import render_textconv

    try:
        _out(render_textconv(load(a.file)))
    except Exception as exc:  # noqa: BLE001 - git shows whatever we print; never break `git diff`
        _out(f"# rbxtool could not read this file: {exc}\n")
    return 0


def cmd_map(a) -> int:
    from .analysis.graph import build_graph
    from .render import render_map

    doc = load(a.file)
    graph = build_graph(doc)
    if a.json:
        _out(json.dumps(graph.to_json(), indent=2) + "\n")
    else:
        _out(render_map(doc, graph, markdown=a.markdown))
    return 0


def cmd_stats(a) -> int:
    from .analysis.stats import compute_stats
    from .render import render_stats

    st = compute_stats(load(a.file), top=a.top)
    _out(json.dumps(st.to_json(), indent=2) + "\n" if a.json else render_stats(st))
    return 0


def cmd_diff(a) -> int:
    from .diff import diff_documents, render_markdown, render_text

    result = diff_documents(load(a.old), load(a.new), include_noise=a.all_props)
    if a.format == "json":
        _out(json.dumps(result.to_json(), indent=2, ensure_ascii=False) + "\n")
    elif a.format == "markdown":
        _out(render_markdown(result, a.context))
    else:
        _out(render_text(result, color=_color(a.color), context=a.context))
    return 1 if a.exit_code and not result.empty else 0


def cmd_check(a) -> int:
    from .checks import RULES, Config, run_checks, should_fail
    from .render import (render_findings_github, render_findings_markdown,
                         render_findings_sarif, render_findings_text)

    if a.list_rules:
        for r in RULES.values():
            _out(f"{r.id}  {r.severity:<7}  {r.name:<24} {r.summary}\n")
        return 0
    if not a.files:
        print("rbxtool check: give at least one file or directory", file=sys.stderr)
        return 2
    config = Config.load(a.config)
    if a.fail_on:
        config.fail_on = a.fail_on
    if a.disable:
        config.disable += [r.upper() for r in a.disable]
    baseline = load(a.baseline) if a.baseline else None

    files = _expand(a.files)
    if not files:
        print("rbxtool check: no Roblox files found", file=sys.stderr)
        return 2
    results: Dict[str, List] = {}
    for path in files:
        try:
            doc = load(path)
        except (OSError, ValueError) as exc:
            print(f"{path}: cannot read: {exc}", file=sys.stderr)
            return 2
        results[path] = run_checks(doc, config, baseline, only=[r.upper() for r in a.rule or []])

    if a.format == "json":
        _out(json.dumps({p: [f.to_json() for f in fs] for p, fs in results.items()}, indent=2) + "\n")
    elif a.format == "sarif":
        _out(render_findings_sarif(results, __version__))
    elif a.format == "markdown":
        _out(render_findings_markdown(results))
    elif a.format == "github":
        _out(render_findings_github(results))
    else:
        color = _color(a.color)
        for path, findings in results.items():
            _out(render_findings_text(path, findings, color))
    failed = any(should_fail(f, config.fail_on) for f in results.values())
    return 1 if failed else 0


def cmd_export(a) -> int:
    from .export import export_scripts

    doc = load(a.file)
    res = export_scripts(doc, a.out, ext=a.ext, project=a.project)
    for w in res.warnings:
        print(f"warning: {w}", file=sys.stderr)
    _out(f"Exported {len(res.files)} files to {a.out}\n")
    return 0


GITATTRIBUTES = "*.rbxl diff=rbx\n*.rbxm diff=rbx\n*.rbxlx diff=rbx\n*.rbxmx diff=rbx\n"


def cmd_git_setup(a) -> int:
    command = f'"{sys.executable}" -m rbxtool textconv' if a.use_python else "rbxtool textconv"
    if not a.apply:
        _out("Run these in your repository to get readable diffs for Roblox files:\n\n"
             f"  git config diff.rbx.textconv '{command}'\n"
             "  git config diff.rbx.cachetextconv true\n"
             "  git config diff.rbx.xfuncname '^\\[.*$'\n\n"
             "and add this to .gitattributes:\n\n"
             + "".join("  " + l + "\n" for l in GITATTRIBUTES.splitlines())
             + "\nOr run `rbxtool git-setup --apply` to do both.\n")
        return 0
    try:
        subprocess.run(["git", "config", "diff.rbx.textconv", command], check=True)
        subprocess.run(["git", "config", "diff.rbx.cachetextconv", "true"], check=True)
        # label each hunk with the instance it is in, like function names for code
        subprocess.run(["git", "config", "diff.rbx.xfuncname", r"^\[.*$"], check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"git config failed ({exc}); are you inside a git repository?", file=sys.stderr)
        return 2
    existing = ""
    if os.path.exists(".gitattributes"):
        with open(".gitattributes", encoding="utf-8") as f:
            existing = f.read()
    missing = [l for l in GITATTRIBUTES.splitlines() if l not in existing]
    if missing:
        with open(".gitattributes", "a", encoding="utf-8") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write("\n".join(missing) + "\n")
    _out("Done. `git diff` and `git log -p` now show Roblox files as text.\n"
         "Commit .gitattributes so teammates get it too (they still run the git config line).\n")
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rbxtool",
        description="Read, diff and check Roblox place/model files (.rbxl .rbxlx .rbxm .rbxmx) "
                    "without Studio.")
    p.add_argument("--version", action="version", version=f"rbxtool {__version__}")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    d = sub.add_parser("dump", help="show the instance tree (and scripts)")
    d.add_argument("file")
    d.add_argument("-s", "--scripts", action="store_true", help="include script source")
    d.add_argument("-p", "--props", action="store_true", help="include properties, attributes, tags")
    d.add_argument("--depth", type=int, help="limit tree depth")
    d.add_argument("--json", action="store_true", help="full JSON output")
    d.add_argument("--no-source", action="store_true", help="leave script source out of --json")
    d.set_defaults(func=cmd_dump)

    m = sub.add_parser("map", help="overview: scripts, which modules they require, remote wiring")
    m.add_argument("file")
    g = m.add_mutually_exclusive_group()
    g.add_argument("--json", action="store_true")
    g.add_argument("--markdown", action="store_true")
    m.set_defaults(func=cmd_map)

    df = sub.add_parser("diff", help="compare two files")
    df.add_argument("old")
    df.add_argument("new")
    df.add_argument("--format", choices=("text", "markdown", "json"), default="text")
    df.add_argument("--context", type=int, default=3, help="lines of context around script changes")
    df.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    df.add_argument("--all-props", action="store_true",
                    help="include properties that change on every save (UniqueId, ...)")
    df.add_argument("--exit-code", action="store_true", help="exit with 1 if the files differ")
    df.set_defaults(func=cmd_diff)

    c = sub.add_parser("check", help="find backdoors, leaked secrets, broken remotes and more")
    c.add_argument("files", nargs="*", help="files or directories")
    c.add_argument("--config", help="path to rbxtool.toml (default: search upwards)")
    c.add_argument("--format", choices=("text", "json", "sarif", "markdown", "github"),
                   default="text", help="github = annotations for GitHub Actions")
    c.add_argument("--fail-on", choices=("error", "warning", "info", "never"),
                   help="lowest severity that makes the exit code 1 (default: error)")
    c.add_argument("--rule", action="append", help="only run this rule (repeatable)")
    c.add_argument("--disable", action="append", help="skip this rule (repeatable)")
    c.add_argument("--baseline", help="previous version of the file, for growth limits")
    c.add_argument("--list-rules", action="store_true")
    c.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    c.set_defaults(func=cmd_check)

    s = sub.add_parser("stats", help="what makes the file big")
    s.add_argument("file")
    s.add_argument("--top", type=int, default=10)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_stats)

    e = sub.add_parser("export", help="write scripts to files (Rojo naming)")
    e.add_argument("file")
    e.add_argument("out", help="output directory")
    e.add_argument("--ext", choices=("luau", "lua"), default="luau")
    e.add_argument("--project", action="store_true",
                   help="also write default.project.json so Rojo can serve it")
    e.set_defaults(func=cmd_export)

    t = sub.add_parser("textconv", help="text form for git diff (used by git-setup)")
    t.add_argument("file")
    t.set_defaults(func=cmd_textconv)

    gs = sub.add_parser("git-setup", help="make `git diff` show Roblox files as text")
    gs.add_argument("--apply", action="store_true", help="run the git config and edit .gitattributes")
    gs.add_argument("--use-python", action="store_true",
                    help="call rbxtool through this Python (when rbxtool isn't on PATH)")
    gs.set_defaults(func=cmd_git_setup)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        return args.func(args)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"rbxtool {args.command}: {exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:  # e.g. piped into `head`
        return 0


if __name__ == "__main__":
    sys.exit(main())
