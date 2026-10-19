"""The in-memory instance tree produced by both parsers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

from .values import EnumValue

SCRIPT_CLASSES = ("Script", "LocalScript", "ModuleScript")

SERVICE_CLASSES = frozenset({
    "Workspace", "Players", "Lighting", "MaterialService", "NetworkClient",
    "ReplicatedFirst", "ReplicatedStorage", "ServerScriptService", "ServerStorage",
    "StarterGui", "StarterPack", "StarterPlayer", "Teams", "SoundService",
    "TextChatService", "Chat", "LocalizationService", "TestService", "CoreGui",
    "HttpService", "RunService", "UserInputService", "TweenService", "Debris",
    "PhysicsService", "PathfindingService", "CollectionService", "BadgeService",
    "MarketplaceService", "TeleportService", "DataStoreService", "MemoryStoreService",
    "MessagingService", "PolicyService", "SocialService", "VoiceChatService",
    "AvatarEditorService", "InsertService",
})

REMOTE_CLASSES = ("RemoteEvent", "RemoteFunction", "UnreliableRemoteEvent")
BINDABLE_CLASSES = ("BindableEvent", "BindableFunction")

VALUE_CLASSES = (
    "StringValue", "IntValue", "NumberValue", "BoolValue", "ObjectValue", "CFrameValue",
    "Vector3Value", "Color3Value", "BrickColorValue", "RayValue",
)


class RobloxInstance:
    """One Roblox instance. Method names match the original GUI so it keeps working."""

    def __init__(self, class_name: str, name: str):
        self.class_name = class_name
        self.name = name
        self.properties: Dict[str, Any] = {}
        self.attributes: Dict[str, Any] = {}
        self.tags: List[str] = []
        self.children: List["RobloxInstance"] = []
        self.parent: Optional["RobloxInstance"] = None
        self.referent: Any = None
        self.orphan = False  # True when recovered from a damaged file with no known parent
        self._id: int = id(self)

    def __repr__(self) -> str:
        return f"<{self.class_name} {self.get_path() or self.name!r}>"

    def __eq__(self, other):
        return isinstance(other, RobloxInstance) and self._id == other._id

    def __hash__(self):
        return hash(self._id)

    # --- tree helpers -------------------------------------------------------

    def add_child(self, child: "RobloxInstance") -> None:
        child.parent = self
        self.children.append(child)

    def descendants(self) -> Iterator["RobloxInstance"]:
        stack = list(reversed(self.children))
        while stack:
            inst = stack.pop()
            yield inst
            stack.extend(reversed(inst.children))

    def find_child(self, name: str) -> Optional["RobloxInstance"]:
        for child in self.children:
            if child.name == name:
                return child
        return None

    def find_service(self, class_name: str) -> Optional["RobloxInstance"]:
        root = self.root()
        for child in root.children:
            if child.class_name == class_name:
                return child
        return None

    def root(self) -> "RobloxInstance":
        node = self
        while node.parent is not None:
            node = node.parent
        return node

    def ancestors(self) -> Iterator["RobloxInstance"]:
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def get_path(self) -> str:
        """Dot path like ``ServerScriptService.Main`` (the root is left out)."""
        parts = []
        current: Optional[RobloxInstance] = self
        while current is not None and current.parent is not None:
            parts.append(current.name)
            current = current.parent
        parts.reverse()
        return ".".join(parts)

    def path_key(self) -> str:
        """Unique, stable path: siblings with the same name get ``[n]`` suffixes."""
        parts = []
        current: Optional[RobloxInstance] = self
        while current is not None and current.parent is not None:
            same = [c for c in current.parent.children if c.name == current.name]
            if len(same) > 1:
                parts.append(f"{current.name}[{same.index(current) + 1}]")
            else:
                parts.append(current.name)
            current = current.parent
        parts.reverse()
        return ".".join(parts)

    # --- classification -----------------------------------------------------

    def is_script(self) -> bool:
        return self.class_name in SCRIPT_CLASSES

    def get_script_source(self) -> str:
        if not self.is_script():
            return ""
        for key in ("Source", "source", "ScriptSource", "LinkedSource"):
            val = self.properties.get(key)
            if val:
                if isinstance(val, bytes):
                    return val.decode("utf-8", errors="replace")
                if isinstance(val, str):
                    return val
        return ""

    @property
    def source(self) -> str:
        return self.get_script_source()

    @property
    def run_context(self) -> Optional[str]:
        value = self.properties.get("RunContext")
        if isinstance(value, EnumValue):
            return value.name or str(value.value)
        return None

    @property
    def script_kind(self) -> Optional[str]:
        """``server``, ``client``, ``module`` or ``plugin`` — RunContext aware."""
        if self.class_name == "ModuleScript":
            return "module"
        if self.class_name == "LocalScript":
            return "client"
        if self.class_name == "Script":
            ctx = self.run_context
            if ctx == "Client":
                return "client"
            if ctx == "Plugin":
                return "plugin"
            return "server"
        return None

    @property
    def disabled(self) -> bool:
        if self.properties.get("Disabled") is True:
            return True
        return self.properties.get("Enabled") is False

    def is_service(self) -> bool:
        return self.class_name in SERVICE_CLASSES

    def is_remote(self) -> bool:
        return self.class_name in REMOTE_CLASSES

    def is_remote_or_bindable(self) -> bool:
        return self.class_name in REMOTE_CLASSES or self.class_name in BINDABLE_CLASSES

    def is_value_object(self) -> bool:
        return self.class_name in VALUE_CLASSES

    def count_children_recursive(self) -> int:
        return sum(1 for _ in self.descendants())

    def find_scripts_recursive(self) -> List["RobloxInstance"]:
        found = [self] if self.is_script() else []
        found.extend(d for d in self.descendants() if d.is_script())
        return found


@dataclass
class ChunkInfo:
    """Size information about one chunk of a binary file (used by ``stats``)."""

    name: str
    offset: int
    compressed_size: int
    size: int
    class_name: Optional[str] = None
    prop_name: Optional[str] = None
    instance_count: int = 0


@dataclass
class Document:
    """A loaded place or model file."""

    root: RobloxInstance
    path: str = ""
    format: str = "binary"  # "binary" or "xml"
    kind: str = "place"  # "place" or "model"
    file_size: int = 0
    warnings: List[str] = field(default_factory=list)
    chunks: List[ChunkInfo] = field(default_factory=list)
    metadata: Dict[str, str] = field(default_factory=dict)

    @property
    def damaged(self) -> bool:
        return any(w.startswith("damaged:") for w in self.warnings)

    def instances(self) -> Iterator[RobloxInstance]:
        return self.root.descendants()

    def scripts(self) -> List[RobloxInstance]:
        return [i for i in self.root.descendants() if i.is_script()]

    def find(self, path: str) -> Optional[RobloxInstance]:
        """Find an instance by dot path or path key."""
        for inst in self.root.descendants():
            if inst.path_key() == path or inst.get_path() == path:
                return inst
        return None
