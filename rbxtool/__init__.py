"""rbxtool — read, inspect, diff and check Roblox place and model files without Studio."""

from .instance import Document, RobloxInstance
from .loader import EXTENSIONS, load, load_bytes

__version__ = "2.0.0.dev0"

__all__ = ["Document", "RobloxInstance", "EXTENSIONS", "load", "load_bytes", "__version__"]
