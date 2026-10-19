"""Facts about how a place fits together: which scripts require which modules,
and which scripts fire or listen to which remotes.

These facts power ``rbxtool map``, several ``check`` rules, and are the
ground truth the AI analyzer will query instead of guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..instance import Document, RobloxInstance
from .resolve import ScriptContext, chain_backward, resolve, _match_bracket

_REQUIRE = re.compile(r"(?<![\w.:])require\s*\(")
_REMOTE_MEMBER = re.compile(
    r"[.:](FireServer|FireClient|FireAllClients|InvokeServer|InvokeClient|"
    r"OnServerEvent|OnClientEvent|OnServerInvoke|OnClientInvoke)\b"
)

# member -> (side the code must run on, role)
REMOTE_MEMBERS = {
    "FireServer": ("client", "sends"),
    "InvokeServer": ("client", "sends"),
    "OnClientEvent": ("client", "receives"),
    "OnClientInvoke": ("client", "receives"),
    "FireClient": ("server", "sends"),
    "FireAllClients": ("server", "sends"),
    "InvokeClient": ("server", "sends"),
    "OnServerEvent": ("server", "receives"),
    "OnServerInvoke": ("server", "receives"),
}


@dataclass
class Require:
    script: RobloxInstance
    line: int
    text: str
    target: Optional[RobloxInstance] = None
    asset_id: Optional[int] = None

    def to_json(self) -> dict:
        return {
            "script": self.script.get_path(),
            "line": self.line,
            "expression": self.text,
            "target": self.target.get_path() if self.target else None,
            "asset_id": self.asset_id,
        }


@dataclass
class RemoteUse:
    script: RobloxInstance
    line: int
    member: str
    expression: str
    remote: Optional[RobloxInstance] = None
    matched_by: str = "path"  # "path" (resolved statically) or "name" (best guess by name)

    @property
    def side(self) -> str:
        return REMOTE_MEMBERS[self.member][0]

    @property
    def role(self) -> str:
        return REMOTE_MEMBERS[self.member][1]

    def to_json(self) -> dict:
        return {
            "script": self.script.get_path(),
            "line": self.line,
            "member": self.member,
            "side": self.side,
            "role": self.role,
            "remote": self.remote.get_path() if self.remote else None,
            "matched_by": self.matched_by if self.remote else None,
            "expression": self.expression,
        }


@dataclass
class PlaceGraph:
    doc: Document
    requires: List[Require] = field(default_factory=list)
    remote_uses: List[RemoteUse] = field(default_factory=list)
    remotes: List[RobloxInstance] = field(default_factory=list)

    def required_by(self, module: RobloxInstance) -> List[Require]:
        return [r for r in self.requires if r.target is module]

    def uses_of(self, remote: RobloxInstance) -> List[RemoteUse]:
        return [u for u in self.remote_uses if u.remote is remote]

    def remote_summary(self, remote: RobloxInstance) -> Dict[str, List[RemoteUse]]:
        uses = self.uses_of(remote)
        return {
            "client_sends": [u for u in uses if u.side == "client" and u.role == "sends"],
            "server_receives": [u for u in uses if u.side == "server" and u.role == "receives"],
            "server_sends": [u for u in uses if u.side == "server" and u.role == "sends"],
            "client_receives": [u for u in uses if u.side == "client" and u.role == "receives"],
        }

    def to_json(self) -> dict:
        remotes = []
        for r in self.remotes:
            summary = self.remote_summary(r)
            remotes.append({
                "path": r.get_path(),
                "class": r.class_name,
                **{k: [u.to_json() for u in v] for k, v in summary.items()},
            })
        return {
            "requires": [r.to_json() for r in self.requires],
            "remotes": remotes,
            "unmatched_remote_uses": [u.to_json() for u in self.remote_uses if u.remote is None],
        }


def build_graph(doc: Document) -> PlaceGraph:
    graph = PlaceGraph(doc)
    graph.remotes = [i for i in doc.instances() if i.is_remote()]
    by_name: Dict[str, List[RobloxInstance]] = {}
    for r in graph.remotes:
        by_name.setdefault(r.name, []).append(r)

    for script in doc.scripts():
        if not script.source:
            continue
        ctx = ScriptContext.build(script)
        code = ctx.scanned.code

        for m in _REQUIRE.finditer(code):
            open_paren = m.end() - 1
            close = _match_bracket(code, open_paren, 1)
            if close == -1:
                continue
            res = resolve(ctx, open_paren + 1, close)
            graph.requires.append(Require(
                script=script,
                line=ctx.scanned.line_of(m.start()),
                text=res.text,
                target=res.target if res.target is not None and res.target.class_name == "ModuleScript"
                else None,
                asset_id=res.number,
            ))

        for m in _REMOTE_MEMBER.finditer(code):
            start = chain_backward(code, m.start())
            if start == m.start():
                continue
            res = resolve(ctx, start, m.start())
            use = RemoteUse(script, ctx.scanned.line_of(m.start()), m.group(1), res.text)
            if res.target is not None and res.target.is_remote():
                use.remote = res.target
            elif res.last_name and len(by_name.get(res.last_name, [])) == 1:
                use.remote = by_name[res.last_name][0]
                use.matched_by = "name"
            graph.remote_uses.append(use)
    return graph
