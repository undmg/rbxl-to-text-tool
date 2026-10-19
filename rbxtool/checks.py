"""Rule-based checks. Each rule is small and explains *why* it matters, so the
output is useful to people who didn't write the rule.

Suppress a finding with a comment on the same line or the line above:
    -- rbxtool: ignore RBX006
or for a whole script, anywhere in it:
    -- rbxtool: ignore-file RBX006
"""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional

from .analysis.graph import PlaceGraph, build_graph
from .analysis.luau import Scanned, scan
from .analysis.stats import human_bytes, parse_size
from .instance import Document, RobloxInstance

SEVERITIES = ("info", "warning", "error")


@dataclass
class Finding:
    rule: str
    severity: str
    title: str
    message: str
    path: str = ""
    line: Optional[int] = None
    snippet: str = ""
    fix: str = ""

    def to_json(self) -> dict:
        out = {"rule": self.rule, "severity": self.severity, "title": self.title,
               "message": self.message, "path": self.path}
        if self.line:
            out["line"] = self.line
        if self.snippet:
            out["snippet"] = self.snippet
        if self.fix:
            out["fix"] = self.fix
        return out


@dataclass
class Config:
    disable: List[str] = field(default_factory=list)
    severity: Dict[str, str] = field(default_factory=dict)
    ignore: List[str] = field(default_factory=list)  # instance path globs
    fail_on: str = "error"  # error | warning | info | never
    max_file_size: Optional[int] = None
    max_growth_percent: Optional[float] = None

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        check = data.get("check", data)
        cfg = cls()
        cfg.disable = [str(r).upper() for r in check.get("disable", [])]
        cfg.severity = {str(k).upper(): str(v).lower() for k, v in check.get("severity", {}).items()}
        cfg.ignore = list(check.get("ignore", []))
        cfg.fail_on = str(check.get("fail_on", "error")).lower()
        if "max_file_size" in check:
            cfg.max_file_size = parse_size(check["max_file_size"])
        if "max_growth_percent" in check:
            cfg.max_growth_percent = float(check["max_growth_percent"])
        bad = [v for v in cfg.severity.values() if v not in SEVERITIES]
        if bad or cfg.fail_on not in SEVERITIES + ("never",):
            raise ValueError(f"invalid severity in config: {bad or cfg.fail_on}")
        return cfg

    @classmethod
    def load(cls, path: Optional[str] = None) -> "Config":
        if path is None:
            path = find_config()
            if path is None:
                return cls()
        try:
            import tomllib  # Python 3.11+
        except ImportError:  # pragma: no cover
            try:
                import tomli as tomllib  # type: ignore
            except ImportError as exc:
                raise RuntimeError("reading rbxtool.toml needs Python 3.11+ or "
                                   "'pip install tomli'") from exc
        with open(path, "rb") as f:
            return cls.from_dict(tomllib.load(f))


def find_config(start: Optional[str] = None) -> Optional[str]:
    directory = os.path.abspath(start or os.getcwd())
    while True:
        candidate = os.path.join(directory, "rbxtool.toml")
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(directory)
        if parent == directory:
            return None
        directory = parent


# ---------------------------------------------------------------------------
# Rule registry
# ---------------------------------------------------------------------------

@dataclass
class Rule:
    id: str
    name: str
    severity: str
    summary: str
    script_check: Optional[Callable[[RobloxInstance, Scanned], Iterable[Finding]]] = None
    doc_check: Optional[Callable[["CheckContext"], Iterable[Finding]]] = None


RULES: Dict[str, Rule] = {}


def rule(id: str, name: str, severity: str, summary: str, scope: str = "script"):
    def wrap(fn):
        RULES[id] = Rule(id, name, severity, summary,
                         script_check=fn if scope == "script" else None,
                         doc_check=fn if scope == "doc" else None)
        return fn
    return wrap


@dataclass
class CheckContext:
    doc: Document
    config: Config
    baseline: Optional[Document] = None
    _graph: Optional[PlaceGraph] = None

    @property
    def graph(self) -> PlaceGraph:
        if self._graph is None:
            self._graph = build_graph(self.doc)
        return self._graph


def _f(rule_id: str, script: RobloxInstance, sc: Scanned, offset: int, message: str,
       fix: str = "", title: str = "") -> Finding:
    line = sc.line_of(offset)
    r = RULES[rule_id]
    return Finding(rule_id, r.severity, title or r.summary, message, script.get_path(), line,
                   sc.line_text(line).strip()[:160], fix)


def _calls(sc: Scanned, names: str) -> Iterable[re.Match]:
    return re.finditer(rf"(?<![\w.:])({names})\s*\(", sc.code)


def _redact(secret: str) -> str:
    return secret[:24] + "…" if len(secret) > 24 else secret[:6] + "…"


# ---------------------------------------------------------------------------
# Security rules
# ---------------------------------------------------------------------------

@rule("RBX001", "asset-require", "error", "Code loaded from the Roblox library by ID")
def _asset_require(script, sc):
    from .analysis.resolve import ScriptContext, resolve, _match_bracket

    ctx = None
    for m in re.finditer(r"(?<![\w.:])require\s*\(", sc.code):
        close = _match_bracket(sc.code, m.end() - 1, 1)
        if close == -1:
            continue
        ctx = ctx or ScriptContext.build(script)
        res = resolve(ctx, m.end(), close)
        if res.number is not None:
            yield _f("RBX001", script, sc, m.start(),
                     f"require({res.number}) downloads and runs a module from the Roblox library "
                     "at runtime. Its owner can change that code at any time without you seeing "
                     "it. This is the most common free-model backdoor.",
                     "Remove it, or copy the module into your game and require it by path.")


@rule("RBX002", "dynamic-code", "error", "Runs code built from a string")
def _loadstring(script, sc):
    for m in _calls(sc, "loadstring"):
        yield _f("RBX002", script, sc, m.start(),
                 "loadstring() runs code from a string. Backdoors use it to execute commands "
                 "sent from outside the game.",
                 "Remove it; legitimate games almost never need loadstring.")


@rule("RBX003", "environment-access", "warning", "Reads or replaces a function environment")
def _fenv(script, sc):
    for m in _calls(sc, "getfenv|setfenv"):
        yield _f("RBX003", script, sc, m.start(),
                 f"{m.group(1)}() is used to hide what code does (e.g. reaching require or "
                 "loadstring without writing their names) and disables Luau optimizations.",
                 "Remove it unless you know exactly why it is there.")


_HIDDEN_WORDS = ("require", "loadstring", "getfenv", "setfenv", "HttpService", "MarketplaceService")


@rule("RBX004", "hidden-keyword", "error", "Sensitive name hidden with escape codes")
def _hidden(script, sc):
    for s in sc.strings:
        raw = sc.source[s.start:s.end]
        if s.value in ("loadstring", "getfenv", "setfenv", "require"):
            yield _f("RBX004", script, sc, s.start,
                     f'The name "{s.value}" is written as a string. Backdoors do this to call it '
                     "through getfenv()/_G lookups so a search for the function finds nothing.",
                     "Check what this string is used for; if it is looked up and called, "
                     "remove the script.", title="Function name passed around as a string")
            continue
        for word in _HIDDEN_WORDS:
            if word in s.value and word not in raw:
                yield _f("RBX004", script, sc, s.start,
                         f'A string spells "{word}" using escape codes so it can\'t be found by '
                         "searching. Nothing legitimate needs to hide that name.",
                         "Treat this script as malicious and remove it.")
                break


_SECRETS = [
    ("Discord webhook URL", re.compile(r"https?://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+")),
    ("Slack webhook URL", re.compile(r"https://hooks\.slack\.com/services/[\w/]+")),
    ("API key (sk-...)", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]


@rule("RBX005", "secret-in-script", "error", "Secret stored in a script")
def _secrets(script, sc):
    where = ("Server scripts are not sent to players, but anyone you share the file with "
             "(team members, freelancers, model uploads) can read it."
             if script.script_kind == "server" else
             "This script runs on players' devices, so exploiters can read it.")
    for s in sc.strings:
        for label, pattern in _SECRETS:
            m = pattern.search(s.value)
            if m:
                finding = _f("RBX005", script, sc, s.start,
                             f"{label} found ({_redact(m.group(0))}). {where}",
                             "Move it out of the place file (e.g. a Secret in Creator Hub, used "
                             "via HttpService:GetSecret) and rotate the leaked value.")
                # never echo the secret itself into logs or PR comments
                finding.snippet = finding.snippet.replace(m.group(0), _redact(m.group(0)))
                raw = sc.source[s.start + 1:s.end - 1]
                if raw and raw in finding.snippet:
                    finding.snippet = finding.snippet.replace(raw, _redact(raw))
                yield finding
                break


@rule("RBX006", "obfuscated-code", "warning", "Looks deliberately obfuscated")
def _obfuscated(script, sc):
    src = sc.source
    escapes = len(re.findall(r"\\\d{2,3}|\\x[0-9a-fA-F]{2}", src))
    char_calls = len(re.findall(r"string\.char\s*\(", sc.code)) + len(re.findall(r"\bchar\s*\(\s*\d", sc.code))
    long_line = max((len(l) for l in src.splitlines()), default=0)
    reasons = []
    if escapes >= 40:
        reasons.append(f"{escapes} escaped character codes")
    if char_calls >= 10:
        reasons.append(f"{char_calls} string.char calls")
    if long_line >= 2000 and src.count("\n") < 5:
        reasons.append(f"a single {long_line}-character line")
    if reasons:
        yield Finding("RBX006", RULES["RBX006"].severity, RULES["RBX006"].summary,
                      "This script contains " + ", ".join(reasons) + ". Real code is rarely "
                      "written this way; backdoors are.", script.get_path(), 1, "",
                      "Don't run it until you know what it does.")


@rule("RBX007", "remote-asset-load", "warning", "Loads an asset by ID at runtime")
def _load_asset(script, sc):
    for m in re.finditer(r"[.:](LoadAsset|LoadAssetVersion)\s*\(\s*\d", sc.code):
        yield _f("RBX007", script, sc, m.start(),
                 f"{m.group(1)} with a fixed ID inserts content (possibly scripts) that someone "
                 "else controls.", "Make sure you own that asset.")


# ---------------------------------------------------------------------------
# Code quality
# ---------------------------------------------------------------------------

_DEPRECATED = {
    "wait": ("task.wait", "wait() can throttle and drift; task.wait() is precise."),
    "spawn": ("task.spawn", "spawn() waits a frame before running and can be throttled."),
    "delay": ("task.delay", "delay() has the same throttling problems as wait()."),
}


@rule("RBX010", "deprecated-api", "info", "Deprecated API")
def _deprecated(script, sc):
    shadowed = set(re.findall(r"\blocal\s+(?:function\s+)?(wait|spawn|delay)\b", sc.code))
    for m in _calls(sc, "wait|spawn|delay"):
        name = m.group(1)
        if name in shadowed:
            continue
        new, why = _DEPRECATED[name]
        yield _f("RBX010", script, sc, m.start(), why,
                 f"Use {new}().", title=f"{name}() is deprecated")
    from .analysis.resolve import _match_bracket

    for m in re.finditer(r"\bInstance\.new\s*\(", sc.code):
        close = _match_bracket(sc.code, m.end() - 1, 1)
        if close == -1:
            continue
        inner, depth, top_commas = sc.code[m.end():close], 0, 0
        for ch in inner:
            depth += ch in "([{"
            depth -= ch in ")]}"
            top_commas += ch == "," and depth == 0
        if top_commas:
            yield _f("RBX010", script, sc, m.start(),
                     "Instance.new(class, parent) parents the object before its properties are "
                     "set, which makes Roblox replicate every later change separately.",
                     "Set properties first, then assign .Parent last.",
                     title="Instance.new with a parent argument")


# ---------------------------------------------------------------------------
# Whole-place rules
# ---------------------------------------------------------------------------

@rule("RBX020", "remote-without-handler", "warning", "Remote is fired but nothing listens",
      scope="doc")
def _remote_handlers(ctx: CheckContext):
    g = ctx.graph
    for remote in g.remotes:
        s = g.remote_summary(remote)
        if s["client_sends"] and not s["server_receives"]:
            u = s["client_sends"][0]
            yield Finding("RBX020", "warning", RULES["RBX020"].summary,
                          f"{u.script.get_path()} line {u.line} calls {u.member} on "
                          f"{remote.get_path()}, but no server script handles it. The call does "
                          "nothing (or, for a RemoteFunction, yields forever).",
                          remote.get_path(), None, "",
                          "Add an OnServerEvent/OnServerInvoke handler, or remove the remote.")
        if s["server_sends"] and not s["client_receives"]:
            u = s["server_sends"][0]
            yield Finding("RBX020", "warning", RULES["RBX020"].summary,
                          f"{u.script.get_path()} line {u.line} calls {u.member} on "
                          f"{remote.get_path()}, but no client script listens to it.",
                          remote.get_path())


@rule("RBX021", "unused-remote", "info", "Remote is never used", scope="doc")
def _unused_remotes(ctx: CheckContext):
    g = ctx.graph
    unresolved = any(u.remote is None for u in g.remote_uses)
    for remote in g.remotes:
        if not g.uses_of(remote):
            note = (" Some remote calls couldn't be traced, so it may still be used."
                    if unresolved else "")
            yield Finding("RBX021", "info", RULES["RBX021"].summary,
                          f"No script references {remote.get_path()}.{note}", remote.get_path(),
                          fix="Remove it if it's left over; every remote is attack surface.")


@rule("RBX030", "damaged-file", "error", "File is damaged", scope="doc")
def _damaged(ctx: CheckContext):
    damage = [w for w in ctx.doc.warnings if w.startswith("damaged:")]
    if damage:
        yield Finding("RBX030", "error", RULES["RBX030"].summary,
                      f"{len(damage)} problem(s) reading the file; first: {damage[0][9:]}",
                      fix="Restore from a backup or Studio version history. "
                          "`rbxtool dump` shows what could be recovered.")


@rule("RBX031", "file-size", "error", "File is over its size budget", scope="doc")
def _size(ctx: CheckContext):
    cfg, doc = ctx.config, ctx.doc
    if cfg.max_file_size and doc.file_size > cfg.max_file_size:
        yield Finding("RBX031", "error", RULES["RBX031"].summary,
                      f"{human_bytes(doc.file_size)} is over the {human_bytes(cfg.max_file_size)} "
                      "budget set in rbxtool.toml.", fix="Run `rbxtool stats` to see what is heavy.")
    base = ctx.baseline
    if cfg.max_growth_percent is not None and base is not None and base.file_size:
        growth = (doc.file_size - base.file_size) * 100 / base.file_size
        if growth > cfg.max_growth_percent:
            yield Finding("RBX031", "error", "File grew more than allowed",
                          f"Grew {growth:.1f}% ({human_bytes(base.file_size)} → "
                          f"{human_bytes(doc.file_size)}); the limit is {cfg.max_growth_percent}%.",
                          fix="Run `rbxtool stats` on both versions to see what was added.")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

_IGNORE = re.compile(r"rbxtool:\s*ignore(-file)?\s+([A-Z0-9, ]+)")


def _suppressed(script: RobloxInstance, sc: Scanned, finding: Finding) -> bool:
    for c in sc.comments:
        m = _IGNORE.search(c.value)
        if not m:
            continue
        ids = {x.strip() for x in m.group(2).replace(",", " ").split()}
        if finding.rule not in ids and "ALL" not in ids:
            continue
        if m.group(1):  # ignore-file
            return True
        if finding.line is None:
            continue
        if c.line == finding.line:
            return True
        # a comment on its own line covers the line below it
        standalone = sc.line_text(c.line).strip().startswith("--")
        if standalone and c.line == finding.line - 1:
            return True
    return False


def run_checks(doc: Document, config: Optional[Config] = None,
               baseline: Optional[Document] = None,
               only: Optional[List[str]] = None) -> List[Finding]:
    config = config or Config()
    ctx = CheckContext(doc, config, baseline)
    active = [r for r in RULES.values()
              if r.id not in config.disable and (not only or r.id in only)]
    findings: List[Finding] = []

    for script in doc.scripts():
        if not script.source or _ignored_path(script.get_path(), config):
            continue
        sc = scan(script.source)
        for r in active:
            if r.script_check:
                for f in r.script_check(script, sc):
                    if not _suppressed(script, sc, f):
                        findings.append(f)
    for r in active:
        if r.doc_check:
            findings.extend(f for f in r.doc_check(ctx)
                            if not f.path or not _ignored_path(f.path, config))

    for f in findings:
        f.severity = config.severity.get(f.rule, f.severity)
    order = {s: i for i, s in enumerate(reversed(SEVERITIES))}
    findings.sort(key=lambda f: (order[f.severity], f.path, f.line or 0, f.rule))
    return findings


def _ignored_path(path: str, config: Config) -> bool:
    return any(fnmatch.fnmatchcase(path, pat) for pat in config.ignore)


def should_fail(findings: List[Finding], fail_on: str) -> bool:
    if fail_on == "never":
        return False
    threshold = SEVERITIES.index(fail_on)
    return any(SEVERITIES.index(f.severity) >= threshold for f in findings)
