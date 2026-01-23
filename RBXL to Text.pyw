#!/usr/bin/env python3
"""Roblox Place File Explorer and Script Exporter
==============================================
Version: 10.5.0

Changes:
- Export dialog now has 2 sections:
  1) Format: TXT / LUA / LUAU
  2) File splitting (TXT only)
- LUA/LUAU exports no longer create any .txt sidecar files.
- LUAU export uses .luau extension.
- Added optional Rojo Adjusted export mapping for LUAU (client/server/shared).
- Attempts to auto-hide the console window when launched from python.exe.
"""

import sys
import os
import struct
import xml.etree.ElementTree as ET
import base64
import re
from typing import List, Optional, Dict, Any, Set, Tuple


# ============================================================================
# WINDOWS: HIDE CONSOLE (if any)
# ============================================================================

def _hide_console_window_if_present():
    """Hide the console window if this app was launched via python.exe/py launcher."""
    if os.name != "nt":
        return
    try:
        import ctypes
        # First try to hide
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
        # Also detach from console entirely
        ctypes.windll.kernel32.FreeConsole()
    except Exception:
        pass


_hide_console_window_if_present()

# ============================================================================
# CHECK LIBRARIES
# ============================================================================

missing_libs = []

try:
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QPushButton, QTreeWidget, QTreeWidgetItem, QFileDialog,
        QTextEdit, QSplitter, QMessageBox, QLabel, QProgressBar,
        QStatusBar, QHeaderView, QCheckBox, QDialog, QRadioButton,
        QButtonGroup, QGroupBox, QDialogButtonBox
    )
    from PyQt5.QtCore import Qt, QThread, pyqtSignal
    from PyQt5.QtGui import QFont, QIcon, QColor, QPixmap, QPainter, QPalette
except ImportError:
    missing_libs.append("PyQt5")

try:
    import lz4.block
    HAS_LZ4 = True
except ImportError:
    HAS_LZ4 = False
    missing_libs.append("lz4")

try:
    import zstandard as zstd
    HAS_ZSTD = True
except ImportError:
    HAS_ZSTD = False
    missing_libs.append("zstandard")

if missing_libs:
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Missing Libraries",
            f"Please run this command in Command Prompt:\n\n"
            f"py -m pip install {' '.join(missing_libs)}\n\n"
            f"Missing: {', '.join(missing_libs)}"
        )
        root.destroy()
    except:
        print(f"Missing libraries: {missing_libs}")
        print(f"Run: py -m pip install {' '.join(missing_libs)}")
    sys.exit(1)


# ============================================================================
# ROBLOX INSTANCE CLASS
# ============================================================================

class RobloxInstance:
    def __init__(self, class_name: str, name: str):
        self.class_name = class_name
        self.name = name
        self.properties: Dict[str, Any] = {}
        self.children: List['RobloxInstance'] = []
        self.parent: Optional['RobloxInstance'] = None
        self._id: int = id(self)
    
    def __eq__(self, other):
        if isinstance(other, RobloxInstance):
            return self._id == other._id
        return False
    
    def __hash__(self):
        return hash(self._id)
    
    def get_path(self) -> str:
        parts = []
        current = self
        while current is not None and current.name != "Game":
            parts.append(current.name)
            current = current.parent
        parts.reverse()
        return ".".join(parts)
    
    def is_script(self) -> bool:
        return self.class_name in ("Script", "LocalScript", "ModuleScript")
    
    def get_script_source(self) -> str:
        if not self.is_script():
            return ""
        for key in ["Source", "source", "ScriptSource", "LinkedSource"]:
            if key in self.properties and self.properties[key]:
                val = self.properties[key]
                if isinstance(val, str):
                    return val
                elif isinstance(val, bytes):
                    return val.decode('utf-8', errors='replace')
        return ""
    
    def is_service(self) -> bool:
        return self.class_name in (
            "Workspace", "Players", "Lighting", "MaterialService",
            "NetworkClient", "ReplicatedFirst", "ReplicatedStorage",
            "ServerScriptService", "ServerStorage", "StarterGui",
            "StarterPack", "StarterPlayer", "Teams", "SoundService",
            "TextChatService", "Chat", "LocalizationService", "TestService",
            "CoreGui", "HttpService", "RunService", "UserInputService",
            "TweenService", "Debris", "PhysicsService", "PathfindingService",
            "CollectionService", "BadgeService", "MarketplaceService",
            "TeleportService", "DataStoreService", "MemoryStoreService",
            "MessagingService", "PolicyService", "SocialService",
            "VoiceChatService", "AvatarEditorService", "InsertService"
        )
    
    def is_remote_or_bindable(self) -> bool:
        return self.class_name in (
            "RemoteEvent", "RemoteFunction",
            "BindableEvent", "BindableFunction",
            "UnreliableRemoteEvent"
        )
    
    def is_value_object(self) -> bool:
        return self.class_name in (
            "StringValue", "IntValue", "NumberValue", "BoolValue",
            "ObjectValue", "CFrameValue", "Vector3Value", "Color3Value",
            "BrickColorValue", "RayValue"
        )
    
    def count_children_recursive(self) -> int:
        count = len(self.children)
        for child in self.children:
            count += child.count_children_recursive()
        return count
    
    def find_scripts_recursive(self) -> List['RobloxInstance']:
        scripts = []
        if self.is_script():
            scripts.append(self)
        for child in self.children:
            scripts.extend(child.find_scripts_recursive())
        return scripts


# ============================================================================
# RBXLX PARSER (XML FORMAT)
# ============================================================================

class RbxlxParser:
    def __init__(self):
        self.root = RobloxInstance(class_name="DataModel", name="Game")
        self.debug_messages = []
    
    def parse(self, file_path: str) -> RobloxInstance:
        self.debug_messages.append(f"Parsing XML file: {file_path}")
        
        try:
            tree = ET.parse(file_path)
            xml_root = tree.getroot()
            
            for element in xml_root:
                if element.tag == "Item":
                    instance = self._parse_item_recursive(element)
                    if instance:
                        instance.parent = self.root
                        self.root.children.append(instance)
            
            self.debug_messages.append(f"Parsed {len(self.root.children)} root services")
            return self.root
            
        except ET.ParseError as e:
            raise ValueError(f"XML Parse Error: {e}")
    
    def _parse_item_recursive(self, element: ET.Element) -> Optional[RobloxInstance]:
        class_name = element.get("class", "Unknown")
        instance = RobloxInstance(class_name=class_name, name=class_name)
        
        props_elem = element.find("Properties")
        if props_elem is not None:
            for prop in props_elem:
                prop_name = prop.get("name", "")
                if not prop_name:
                    continue
                
                value = self._get_property_value(prop)
                instance.properties[prop_name] = value
                
                if prop_name == "Name" and isinstance(value, str) and value:
                    instance.name = value
        
        for child_elem in element:
            if child_elem.tag == "Item":
                child_instance = self._parse_item_recursive(child_elem)
                if child_instance:
                    child_instance.parent = instance
                    instance.children.append(child_instance)
        
        return instance
    
    def _get_property_value(self, prop: ET.Element) -> Any:
        tag = prop.tag
        text = prop.text or ""
        
        if tag in ("string", "ProtectedString"):
            return text
        elif tag == "bool":
            return text.lower() == "true"
        elif tag in ("int", "int64"):
            try:
                return int(text)
            except:
                return 0
        elif tag in ("float", "double"):
            try:
                return float(text)
            except:
                return 0.0
        elif tag == "BinaryString":
            if text:
                try:
                    decoded = base64.b64decode(text)
                    return decoded.decode('utf-8', errors='replace')
                except:
                    return text
            return ""
        else:
            return text


# ============================================================================
# DECOMPRESSION HELPER
# ============================================================================

def decompress_chunk(data: bytes, decompressed_size: int) -> Optional[bytes]:
    if len(data) == 0:
        return b''
    
    if len(data) >= 4 and data[0:4] == b'\x28\xb5\x2f\xfd':
        if HAS_ZSTD:
            try:
                dctx = zstd.ZstdDecompressor()
                return dctx.decompress(data, max_output_size=decompressed_size + 1000)
            except:
                pass
        return None
    
    if HAS_LZ4:
        try:
            return lz4.block.decompress(data, uncompressed_size=decompressed_size)
        except:
            pass
    
    if HAS_ZSTD:
        try:
            dctx = zstd.ZstdDecompressor()
            return dctx.decompress(data, max_output_size=decompressed_size + 1000)
        except:
            pass
    
    return None


# ============================================================================
# RBXL PARSER (BINARY FORMAT)
# ============================================================================

class RbxlParser:
    MAGIC = b"<roblox!"
    SIGNATURE = b"\x89\xff\r\n\x1a\n"
    
    def __init__(self):
        self.root = RobloxInstance(class_name="DataModel", name="Game")
        self.instances_by_id: Dict[int, RobloxInstance] = {}
        self.class_names: Dict[int, str] = {}
        self.class_instance_ids: Dict[int, List[int]] = {}
        self.shared_strings: Dict[int, str] = {}
        self.debug_messages = []
        self.children_added: Set[int] = set()
    
    def parse(self, file_path: str) -> RobloxInstance:
        with open(file_path, 'rb') as f:
            data = f.read()
        
        file_size = len(data)
        self.debug_messages.append(f"File size: {file_size:,} bytes")
        
        if not data.startswith(self.MAGIC):
            raise ValueError("Not a valid .rbxl file")
        
        pos = 8
        
        if data[pos:pos+6] == self.SIGNATURE:
            pos += 6
            self.debug_messages.append("Has signature: YES")
        
        version = struct.unpack('<H', data[pos:pos+2])[0]
        pos += 2
        
        class_count = struct.unpack('<I', data[pos:pos+4])[0]
        pos += 4
        
        instance_count = struct.unpack('<I', data[pos:pos+4])[0]
        pos += 4
        
        self.debug_messages.append(f"Version: {version}, Classes: {class_count}, Instances: {instance_count}")
        
        pos += 8
        
        self.debug_messages.append(f"Starting chunk parsing at position: {pos}")
        
        prnt_chunks = []
        chunks_parsed = 0
        inst_count = 0
        prop_count = 0
        sstr_count = 0
        
        while pos < len(data) - 4:
            if pos + 16 > len(data):
                break
            
            chunk_name_bytes = data[pos:pos+4]
            chunk_name = chunk_name_bytes.decode('ascii', errors='ignore').rstrip('\x00')
            
            compressed_len = struct.unpack('<I', data[pos+4:pos+8])[0]
            decompressed_len = struct.unpack('<I', data[pos+8:pos+12])[0]
            reserved = struct.unpack('<I', data[pos+12:pos+16])[0]
            pos += 16
            
            if chunk_name == "END":
                self.debug_messages.append("Found END chunk")
                break
            
            if compressed_len > file_size or pos + compressed_len > len(data):
                break
            
            chunk_data_raw = data[pos:pos+compressed_len]
            pos += compressed_len
            
            if compressed_len > 0 and compressed_len != decompressed_len:
                chunk_data = decompress_chunk(chunk_data_raw, decompressed_len)
                if chunk_data is None:
                    continue
            else:
                chunk_data = chunk_data_raw
            
            try:
                if chunk_name == "SSTR":
                    self._parse_sstr_chunk(chunk_data)
                    sstr_count += 1
                elif chunk_name == "INST":
                    self._parse_inst_chunk(chunk_data)
                    inst_count += 1
                elif chunk_name == "PROP":
                    self._parse_prop_chunk(chunk_data)
                    prop_count += 1
                elif chunk_name == "PRNT":
                    prnt_chunks.append(chunk_data)
            except Exception as e:
                self.debug_messages.append(f"Error parsing {chunk_name}: {e}")
            
            chunks_parsed += 1
        
        prnt_count = 0
        for chunk_data in prnt_chunks:
            try:
                self._parse_prnt_chunk(chunk_data)
                prnt_count += 1
            except Exception as e:
                self.debug_messages.append(f"Error parsing PRNT: {e}")
        
        self.debug_messages.append(f"Chunks: {chunks_parsed} (SSTR:{sstr_count}, INST:{inst_count}, PROP:{prop_count}, PRNT:{prnt_count})")
        self.debug_messages.append(f"Instances created: {len(self.instances_by_id)}")
        self.debug_messages.append(f"Root children: {len(self.root.children)}")
        
        for child in self.root.children[:5]:
            self.debug_messages.append(f"  Root child: {child.name} ({child.class_name})")
        
        return self.root
    
    def _parse_sstr_chunk(self, data: bytes):
        if len(data) < 8:
            return
        
        pos = 0
        version = struct.unpack('<I', data[pos:pos+4])[0]
        pos += 4
        
        count = struct.unpack('<I', data[pos:pos+4])[0]
        pos += 4
        
        for i in range(count):
            if pos + 20 > len(data):
                break
            
            pos += 16
            
            if pos + 4 > len(data):
                break
            str_len = struct.unpack('<I', data[pos:pos+4])[0]
            pos += 4
            
            if str_len > 10000000 or pos + str_len > len(data):
                break
            
            string_data = data[pos:pos+str_len].decode('utf-8', errors='replace')
            pos += str_len
            
            self.shared_strings[i] = string_data
    
    def _parse_inst_chunk(self, data: bytes):
        if len(data) < 10:
            return
        
        pos = 0
        
        class_id = struct.unpack('<I', data[pos:pos+4])[0]
        pos += 4
        
        name_len = struct.unpack('<I', data[pos:pos+4])[0]
        pos += 4
        
        if name_len > 200 or pos + name_len > len(data):
            return
        
        class_name = data[pos:pos+name_len].decode('utf-8', errors='ignore')
        pos += name_len
        
        self.class_names[class_id] = class_name
        
        if pos >= len(data):
            return
        pos += 1
        
        if pos + 4 > len(data):
            return
        count = struct.unpack('<I', data[pos:pos+4])[0]
        pos += 4
        
        if count > 1000000 or pos + count * 4 > len(data):
            return
        
        ids = self._read_referent_array(data[pos:pos+count*4], count)
        
        self.class_instance_ids[class_id] = ids
        
        for inst_id in ids:
            instance = RobloxInstance(class_name=class_name, name=class_name)
            self.instances_by_id[inst_id] = instance
    
    def _parse_prop_chunk(self, data: bytes):
        if len(data) < 10:
            return
        
        pos = 0
        
        class_id = struct.unpack('<I', data[pos:pos+4])[0]
        pos += 4
        
        name_len = struct.unpack('<I', data[pos:pos+4])[0]
        pos += 4
        
        if name_len > 200 or pos + name_len > len(data):
            return
        
        prop_name = data[pos:pos+name_len].decode('utf-8', errors='ignore')
        pos += name_len
        
        if pos >= len(data):
            return
        prop_type = data[pos]
        pos += 1
        
        if class_id not in self.class_instance_ids:
            return
        
        instance_ids = self.class_instance_ids[class_id]
        instances = [self.instances_by_id[iid] for iid in instance_ids if iid in self.instances_by_id]
        
        if not instances:
            return
        
        try:
            values = self._read_property_values(data[pos:], prop_type, len(instances))
            
            for i, inst in enumerate(instances):
                if i < len(values):
                    inst.properties[prop_name] = values[i]
                    if prop_name == "Name" and values[i]:
                        if isinstance(values[i], str):
                            inst.name = values[i]
                        elif isinstance(values[i], bytes):
                            inst.name = values[i].decode('utf-8', errors='replace')
        except:
            pass
    
    def _parse_prnt_chunk(self, data: bytes):
        if len(data) < 5:
            return
        
        pos = 0
        
        version = data[pos]
        pos += 1
        
        count = struct.unpack('<I', data[pos:pos+4])[0]
        pos += 4
        
        if count > 1000000:
            return
        
        if len(data) < pos + count * 8:
            return
        
        child_ids = self._read_referent_array(data[pos:pos+count*4], count)
        pos += count * 4
        
        parent_ids = self._read_referent_array(data[pos:pos+count*4], count)
        
        for i in range(count):
            child_id = child_ids[i]
            parent_id = parent_ids[i]
            
            if child_id not in self.instances_by_id:
                continue
            
            if child_id in self.children_added:
                continue
            
            child = self.instances_by_id[child_id]
            
            if parent_id in self.instances_by_id:
                parent = self.instances_by_id[parent_id]
                child.parent = parent
                parent.children.append(child)
            else:
                child.parent = self.root
                self.root.children.append(child)
            
            self.children_added.add(child_id)
    
    def _read_referent_array(self, data: bytes, count: int) -> List[int]:
        if len(data) < count * 4:
            return []
        
        result = []
        accumulator = 0
        
        for i in range(count):
            b0 = data[i] if i < len(data) else 0
            b1 = data[count + i] if count + i < len(data) else 0
            b2 = data[count * 2 + i] if count * 2 + i < len(data) else 0
            b3 = data[count * 3 + i] if count * 3 + i < len(data) else 0
            
            value = (b0 << 24) | (b1 << 16) | (b2 << 8) | b3
            value = (value >> 1) ^ (-(value & 1))
            
            accumulator += value
            result.append(accumulator)
        
        return result
    
    def _read_property_values(self, data: bytes, prop_type: int, count: int) -> List[Any]:
        values = []
        pos = 0
        
        if prop_type == 0x01:
            for _ in range(count):
                if pos + 4 > len(data):
                    values.append("")
                    continue
                slen = struct.unpack('<I', data[pos:pos+4])[0]
                pos += 4
                if slen > 10000000 or pos + slen > len(data):
                    values.append("")
                    continue
                values.append(data[pos:pos+slen].decode('utf-8', errors='replace'))
                pos += slen
        
        elif prop_type == 0x02:
            for i in range(count):
                if pos + i < len(data):
                    values.append(bool(data[pos + i]))
                else:
                    values.append(False)
        
        elif prop_type == 0x03:
            values = self._read_referent_array(data[pos:], count)
        
        elif prop_type == 0x04:
            for i in range(count):
                if pos + count * 4 <= len(data):
                    b0 = data[pos + i]
                    b1 = data[pos + count + i] if pos + count + i < len(data) else 0
                    b2 = data[pos + count * 2 + i] if pos + count * 2 + i < len(data) else 0
                    b3 = data[pos + count * 3 + i] if pos + count * 3 + i < len(data) else 0
                    float_bytes = bytes([b3, b2, b1, b0])
                    try:
                        values.append(struct.unpack('<f', float_bytes)[0])
                    except:
                        values.append(0.0)
                else:
                    values.append(0.0)
        
        elif prop_type == 0x05:
            for _ in range(count):
                if pos + 8 <= len(data):
                    values.append(struct.unpack('<d', data[pos:pos+8])[0])
                    pos += 8
                else:
                    values.append(0.0)
        
        elif prop_type == 0x20:
            for _ in range(count):
                if pos + 4 > len(data):
                    values.append("")
                    continue
                slen = struct.unpack('<I', data[pos:pos+4])[0]
                pos += 4
                if slen > 50000000 or pos + slen > len(data):
                    values.append("")
                    continue
                values.append(data[pos:pos+slen].decode('utf-8', errors='replace'))
                pos += slen
        
        elif prop_type == 0x21:
            indices = self._read_referent_array(data[pos:], count)
            for idx in indices:
                values.append(self.shared_strings.get(idx, ""))
        
        else:
            values = [""] * count
        
        return values


# ============================================================================
# ICON GENERATOR
# ============================================================================

class IconGenerator:
    COLORS = {
        "Script": "#4CAF50",
        "LocalScript": "#2196F3",
        "ModuleScript": "#9C27B0",
        "Workspace": "#607D8B",
        "Players": "#03A9F4",
        "Lighting": "#FFEB3B",
        "MaterialService": "#795548",
        "ReplicatedFirst": "#8BC34A",
        "ReplicatedStorage": "#00BCD4",
        "ServerScriptService": "#F44336",
        "ServerStorage": "#673AB7",
        "StarterGui": "#3F51B5",
        "StarterPack": "#009688",
        "StarterPlayer": "#E91E63",
        "Teams": "#FF9800",
        "SoundService": "#CDDC39",
        "TextChatService": "#00ACC1",
        "Chat": "#00ACC1",
        "RemoteEvent": "#FF5722",
        "RemoteFunction": "#FF7043",
        "BindableEvent": "#FFAB91",
        "BindableFunction": "#FFCCBC",
        "UnreliableRemoteEvent": "#E64A19",
        "Folder": "#FFC107",
        "Model": "#FF9800",
        "Configuration": "#A1887F",
        "Camera": "#90A4AE",
        "Terrain": "#8D6E63",
        "Part": "#8D6E63",
        "MeshPart": "#A1887F",
        "UnionOperation": "#BCAAA4",
        "SpawnLocation": "#81D4FA",
        "ScreenGui": "#CE93D8",
        "Frame": "#B39DDB",
        "TextLabel": "#9FA8DA",
        "TextButton": "#90CAF9",
        "TextBox": "#80DEEA",
        "ImageLabel": "#80CBC4",
        "ImageButton": "#A5D6A7",
        "ScrollingFrame": "#C5E1A5",
        "StringValue": "#81C784",
        "IntValue": "#64B5F6",
        "NumberValue": "#7986CB",
        "BoolValue": "#4DB6AC",
        "ObjectValue": "#DCE775",
        "Sound": "#7E57C2",
        "Animation": "#26A69A",
        "default": "#9E9E9E"
    }
    
    @classmethod
    def get_icon(cls, class_name: str, size: int = 18) -> QIcon:
        color = cls.COLORS.get(class_name, cls.COLORS["default"])
        
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor(color))
        painter.setPen(QColor(color).darker(120))
        painter.drawRoundedRect(1, 1, size-2, size-2, 3, 3)
        
        painter.setPen(QColor("white"))
        font = QFont("Arial", size // 2, QFont.Bold)
        painter.setFont(font)
        letter = class_name[0] if class_name else "?"
        painter.drawText(0, 0, size, size, Qt.AlignCenter, letter)
        
        painter.end()
        return QIcon(pixmap)


# ============================================================================
# TREE ITEM
# ============================================================================

class RobloxTreeItem(QTreeWidgetItem):
    def __init__(self, instance: RobloxInstance, parent=None):
        super().__init__(parent)
        self.instance = instance
        
        self.setText(0, f"{instance.name} [{instance.class_name}]")
        self.setIcon(0, IconGenerator.get_icon(instance.class_name))
        
        self.setFlags(self.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
        self.setCheckState(0, Qt.Unchecked)
        
        tip = f"Name: {instance.name}\nClass: {instance.class_name}\nPath: {instance.get_path()}"
        if instance.is_script():
            src = instance.get_script_source()
            lines = len(src.split('\n')) if src else 0
            tip += f"\nScript Lines: {lines}"
        tip += f"\nChildren: {len(instance.children)}"
        self.setToolTip(0, tip)


# ============================================================================
# LOADER THREAD
# ============================================================================

class LoaderThread(QThread):
    finished = pyqtSignal(object, list)
    progress = pyqtSignal(str)
    
    def __init__(self, file_path: str):
        super().__init__()
        self.file_path = file_path
    
    def run(self):
        try:
            self.progress.emit("Reading file...")
            
            ext = os.path.splitext(self.file_path)[1].lower()
            
            if ext == ".rbxlx":
                parser = RbxlxParser()
            elif ext == ".rbxl":
                parser = RbxlParser()
            else:
                self.finished.emit(ValueError(f"Unknown file type: {ext}"), [])
                return
            
            result = parser.parse(self.file_path)
            self.finished.emit(result, parser.debug_messages)
            
        except Exception as e:
            import traceback
            self.finished.emit(e, [traceback.format_exc()])


# ============================================================================
# EXPORT OPTIONS DIALOG
# ============================================================================

class ExportOptionsDialog(QDialog):
    """
    Export Options Dialog
    
    Provides 3 main export formats:
    1. TXT  - All content in a single text file (with descriptions)
    2. LUA  - Each script as a separate .lua file
    3. LUAU - Rojo-compatible structure (.luau extension, client/server/shared folders)
    """
    
    def __init__(self, parent=None, script_count: int = 0, nonscript_count: int = 0, total_count: int = 0):
        super().__init__(parent)
        self.script_count = script_count
        self.nonscript_count = nonscript_count
        self.total_count = total_count
        self.setWindowTitle("Export Options")
        self.setMinimumWidth(580)
        self.setup_ui()
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        
        # ===== Selection info =====
        info_label = QLabel(f"📦 Selected: {self.total_count} items ({self.script_count} scripts, {self.nonscript_count} other)")
        info_label.setStyleSheet("font-weight: bold; font-size: 13px; padding: 8px; background-color: #3a3a3a; border-radius: 5px;")
        layout.addWidget(info_label)
        
        # ===== Export Format =====
        format_group = QGroupBox("Choose Export Format")
        format_layout = QVBoxLayout(format_group)
        format_layout.setSpacing(8)
        
        self.format_button_group = QButtonGroup(self)
        
        # ─────────────────────────────────────────────────────────────
        # TXT Option
        # ─────────────────────────────────────────────────────────────
        self.radio_txt = QRadioButton("📄 TXT - Single Text File")
        self.radio_txt.setChecked(True)
        self.radio_txt.setStyleSheet("font-weight: bold; font-size: 12px;")
        self.format_button_group.addButton(self.radio_txt, 0)
        format_layout.addWidget(self.radio_txt)
        
        # TXT description
        txt_info = QLabel(
            "     • All scripts + descriptions + paths in one file\n"
            "     • Includes Models, Folders, Remotes, etc.\n"
            "     • Human-readable format with headers"
        )
        txt_info.setStyleSheet("color: #a0a0a0; font-size: 11px; margin-left: 25px;")
        format_layout.addWidget(txt_info)
        
        # TXT sub-option: File splitting
        self.chk_txt_split = QCheckBox("Auto-split large files (~90k characters)")
        self.chk_txt_split.setChecked(True)
        self.chk_txt_split.setStyleSheet("margin-left: 25px; color: #80c0ff;")
        format_layout.addWidget(self.chk_txt_split)
        
        format_layout.addSpacing(12)
        
        # ─────────────────────────────────────────────────────────────
        # LUA Option
        # ─────────────────────────────────────────────────────────────
        self.radio_lua = QRadioButton("📜 LUA - Separate Script Files (.lua)")
        self.radio_lua.setStyleSheet("font-weight: bold; font-size: 12px;")
        self.format_button_group.addButton(self.radio_lua, 1)
        format_layout.addWidget(self.radio_lua)
        
        lua_info = QLabel(
            "     • One file per script (raw code only)\n"
            f"     • Approximately {self.script_count} .lua files\n"
            "     • Preserves Roblox folder structure\n"
            "     • Extensions: .client.lua / .server.lua / .module.lua\n"
            "     • Non-scripts are skipped (no .txt created)"
        )
        lua_info.setStyleSheet("color: #a0a0a0; font-size: 11px; margin-left: 25px;")
        format_layout.addWidget(lua_info)
        
        format_layout.addSpacing(12)
        
        # ─────────────────────────────────────────────────────────────
        # LUAU Option (Rojo Compatible)
        # ─────────────────────────────────────────────────────────────
        self.radio_luau = QRadioButton("🔧 LUAU - Rojo-Compatible Structure (.luau)")
        self.radio_luau.setStyleSheet("font-weight: bold; font-size: 12px;")
        self.format_button_group.addButton(self.radio_luau, 2)
        format_layout.addWidget(self.radio_luau)
        
        luau_info = QLabel(
            "     • One file per script (raw code only)\n"
            f"     • Approximately {self.script_count} .luau files\n"
            "     • Extensions: .client.luau / .server.luau / .module.luau\n"
            "     • Non-scripts are skipped"
        )
        luau_info.setStyleSheet("color: #a0a0a0; font-size: 11px; margin-left: 25px;")
        format_layout.addWidget(luau_info)
        
        # LUAU sub-option: Rojo folder structure
        self.chk_rojo_adjusted = QCheckBox("Use Rojo folder structure (client / server / shared)")
        self.chk_rojo_adjusted.setChecked(True)
        self.chk_rojo_adjusted.setStyleSheet("margin-left: 25px; color: #80c0ff; font-weight: bold;")
        format_layout.addWidget(self.chk_rojo_adjusted)
        
        rojo_mapping = QLabel(
            "          StarterPlayerScripts → client/\n"
            "          ServerScriptService → server/\n"
            "          ReplicatedStorage → shared/\n"
            "          (Scripts outside these locations are skipped)"
        )
        rojo_mapping.setStyleSheet("color: #70a0ff; font-size: 10px; margin-left: 40px;")
        format_layout.addWidget(rojo_mapping)
        self.rojo_mapping_label = rojo_mapping
        
        layout.addWidget(format_group)
        
        # ===== Connect signals =====
        self.radio_txt.toggled.connect(self.on_format_changed)
        self.radio_lua.toggled.connect(self.on_format_changed)
        self.radio_luau.toggled.connect(self.on_format_changed)
        self.on_format_changed()
        
        # ===== Buttons =====
        button_box = QDialogButtonBox()
        self.btn_export = QPushButton("✅ Export")
        self.btn_export.setDefault(True)
        self.btn_cancel = QPushButton("Cancel")
        button_box.addButton(self.btn_export, QDialogButtonBox.AcceptRole)
        button_box.addButton(self.btn_cancel, QDialogButtonBox.RejectRole)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
        
        # ===== Koyu tema =====
        self.setStyleSheet("""
            QDialog {
                background-color: #2d2d2d;
                color: #c8c8c8;
            }
            QGroupBox {
                font-weight: bold;
                font-size: 13px;
                border: 1px solid #505050;
                border-radius: 6px;
                margin-top: 12px;
                padding: 15px 10px 10px 10px;
                background-color: #353535;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 8px;
                color: #e0e0e0;
            }
            QRadioButton {
                color: #c8c8c8;
                spacing: 8px;
                padding: 4px;
            }
            QRadioButton::indicator {
                width: 18px;
                height: 18px;
                border-radius: 9px;
                border: 2px solid #606060;
                background-color: #353535;
            }
            QRadioButton::indicator:checked {
                background-color: #5090d0;
                border-color: #70a0e0;
            }
            QRadioButton::indicator:hover {
                border-color: #80a0c0;
            }
            QCheckBox {
                color: #c8c8c8;
                spacing: 8px;
                padding: 4px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 3px;
                border: 2px solid #606060;
                background-color: #353535;
            }
            QCheckBox::indicator:checked {
                background-color: #5090d0;
                border-color: #70a0e0;
            }
            QCheckBox::indicator:hover {
                border-color: #80a0c0;
            }
            QCheckBox:disabled {
                color: #555555;
            }
            QCheckBox::indicator:disabled {
                border-color: #404040;
                background-color: #2a2a2a;
            }
            QLabel {
                color: #c8c8c8;
            }
            QPushButton {
                background-color: #3d3d3d;
                color: #c8c8c8;
                border: 1px solid #505050;
                border-radius: 5px;
                padding: 10px 24px;
                font-weight: bold;
                min-width: 90px;
            }
            QPushButton:hover {
                background-color: #484848;
                border-color: #606060;
            }
            QPushButton:pressed {
                background-color: #525252;
            }
            QPushButton:default {
                background-color: #4080b0;
                border-color: #5090c0;
            }
            QPushButton:default:hover {
                background-color: #5090c0;
            }
        """)
    
    def on_format_changed(self):
        """Format seçimi değiştiğinde alt seçenekleri güncelle."""
        is_txt = self.radio_txt.isChecked()
        is_luau = self.radio_luau.isChecked()
        
        # TXT seçenekleri
        self.chk_txt_split.setEnabled(is_txt)
        self.chk_txt_split.setVisible(True)  # Her zaman görünür, sadece devre dışı
        
        # LUAU seçenekleri
        self.chk_rojo_adjusted.setEnabled(is_luau)
        self.rojo_mapping_label.setEnabled(is_luau)
    
    def get_options(self) -> dict:
        """Seçilen export ayarlarını döndür."""
        if self.radio_txt.isChecked():
            fmt = "txt"
        elif self.radio_luau.isChecked():
            fmt = "luau"
        else:
            fmt = "lua"
        
        return {
            "format": fmt,
            "auto_split": self.chk_txt_split.isChecked() if fmt == "txt" else False,
            "rojo_adjusted": self.chk_rojo_adjusted.isChecked() if fmt == "luau" else False,
        }


# ============================================================================
# EXPORT GENERATOR (TXT FORMAT)
# ============================================================================

class ExportGenerator:
    TARGET_CHARS = 90000
    MAX_CHARS = 95000
    HEADER_FOOTER_SIZE = 600
    
    def __init__(self, items: List[RobloxTreeItem], source_filename: str, auto_split: bool = True):
        self.items = items
        self.source_filename = source_filename
        self.auto_split = auto_split
    
    def generate(self) -> List[Tuple[str, int]]:
        all_items_data = []
        counts = {"scripts": 0, "remotes": 0, "models": 0, "folders": 0, "values": 0, "other": 0}
        
        for tree_item in self.items:
            inst = tree_item.instance
            path = inst.get_path()
            parent_path = inst.parent.get_path() if inst.parent and inst.parent.name != "Game" else ""
            
            item_lines = []
            
            if inst.is_script():
                counts["scripts"] += 1
                source = inst.get_script_source()
                
                item_lines.append("-" * 80)
                item_lines.append(f"inside {parent_path}")
                item_lines.append(f"there is {inst.name} {inst.class_name}:")
                item_lines.append("-" * 80)
                item_lines.append("")
                item_lines.append(source if source else "-- [Empty or no source]")
                item_lines.append("")
                item_lines.append("")
            
            elif inst.is_remote_or_bindable():
                counts["remotes"] += 1
                item_lines.append(f"[{inst.class_name}] {inst.name}")
                item_lines.append(f"    Path: {path}")
                item_lines.append("")
            
            elif inst.class_name == "Model":
                counts["models"] += 1
                child_count = inst.count_children_recursive()
                script_count = len(inst.find_scripts_recursive())
                item_lines.append(f"[Model] {inst.name}")
                item_lines.append(f"    Path: {path}")
                item_lines.append(f"    Contains: {child_count} objects, {script_count} scripts")
                item_lines.append("")
            
            elif inst.class_name == "Folder":
                counts["folders"] += 1
                item_lines.append(f"[Folder] {inst.name}")
                item_lines.append(f"    Path: {path}")
                item_lines.append(f"    Children: {len(inst.children)}")
                item_lines.append("")
            
            elif inst.is_value_object():
                counts["values"] += 1
                value = inst.properties.get("Value", "")
                item_lines.append(f"[{inst.class_name}] {inst.name}")
                item_lines.append(f"    Path: {path}")
                item_lines.append(f"    Value: {value}")
                item_lines.append("")
            
            else:
                counts["other"] += 1
                item_lines.append(f"[{inst.class_name}] {inst.name}")
                item_lines.append(f"    Path: {path}")
                item_lines.append("")
            
            item_content = "\n".join(item_lines)
            all_items_data.append((item_content, len(item_content)))
        
        if not self.auto_split:
            all_contents = [item[0] for item in all_items_data]
            full_content = self._create_file_content(all_contents, counts, 1, 1)
            return [(full_content, len(self.items))]
        
        usable_space = self.MAX_CHARS - self.HEADER_FOOTER_SIZE
        
        file_parts = []
        current_file_items = []
        current_file_size = 0
        
        for item_content, item_size in all_items_data:
            projected_size = current_file_size + item_size
            
            if projected_size <= usable_space:
                current_file_items.append(item_content)
                current_file_size += item_size
            else:
                if len(current_file_items) > 0:
                    file_parts.append(current_file_items)
                    current_file_items = []
                    current_file_size = 0
                
                current_file_items.append(item_content)
                current_file_size = item_size
        
        if current_file_items:
            file_parts.append(current_file_items)
        
        file_parts = self._optimize_file_parts(file_parts, usable_space)
        
        result = []
        total_parts = len(file_parts)
        
        for part_num, items_in_part in enumerate(file_parts, 1):
            content = self._create_file_content(items_in_part, counts, part_num, total_parts)
            result.append((content, len(items_in_part)))
        
        return result
    
    def _optimize_file_parts(self, file_parts: List[List[str]], usable_space: int) -> List[List[str]]:
        if len(file_parts) <= 1:
            return file_parts
        
        optimized = []
        i = 0
        
        while i < len(file_parts):
            current = file_parts[i]
            current_size = sum(len(item) for item in current)
            
            while i + 1 < len(file_parts) and current_size < self.TARGET_CHARS - self.HEADER_FOOTER_SIZE:
                next_part = file_parts[i + 1]
                next_size = sum(len(item) for item in next_part)
                
                if current_size + next_size <= usable_space:
                    current = current + next_part
                    current_size += next_size
                    i += 1
                else:
                    break
            
            optimized.append(current)
            i += 1
        
        return optimized
    
    def _create_file_content(self, item_contents: List[str], counts: Dict[str, int], 
                             part_num: int, total_parts: int) -> str:
        lines = []
        
        lines.append("=" * 80)
        lines.append("ROBLOX PLACE EXPORT")
        if total_parts > 1:
            lines.append(f"Part {part_num} of {total_parts}")
        lines.append("=" * 80)
        lines.append(f"Source: {self.source_filename}")
        lines.append(f"Total Items in Export: {len(self.items)}")
        if total_parts > 1:
            lines.append(f"Items in This Part: {len(item_contents)}")
        lines.append("=" * 80)
        lines.append("")
        
        for item_content in item_contents:
            lines.append(item_content)
        
        if part_num == total_parts:
            lines.append("")
            lines.append("=" * 80)
            lines.append("SUMMARY")
            lines.append("=" * 80)
            lines.append(f"Scripts: {counts['scripts']}")
            lines.append(f"Remotes/Bindables: {counts['remotes']}")
            lines.append(f"Models: {counts['models']}")
            lines.append(f"Folders: {counts['folders']}")
            lines.append(f"Values: {counts['values']}")
            lines.append(f"Other: {counts['other']}")
            lines.append(f"TOTAL: {len(self.items)}")
            lines.append("=" * 80)
        else:
            lines.append("")
            lines.append(f"--- Continued in Part {part_num + 1} ---")
            lines.append("")
        
        return "\n".join(lines)


# ============================================================================
# NONSCRIPTS GENERATOR
# ============================================================================

class NonscriptsGenerator:
    """Generates content for nonscripts.txt file."""
    
    def __init__(self, items: List[RobloxTreeItem], source_filename: str):
        self.items = items
        self.source_filename = source_filename
    
    def generate(self) -> str:
        """Generate the nonscripts.txt content."""
        lines = []
        
        # Categorize items
        remotes = []
        models = []
        folders = []
        values = []
        other = []
        
        for tree_item in self.items:
            inst = tree_item.instance
            if inst.is_script():
                continue  # Skip scripts
            
            path = inst.get_path()
            
            if inst.is_remote_or_bindable():
                remotes.append((inst, path))
            elif inst.class_name == "Model":
                models.append((inst, path))
            elif inst.class_name == "Folder":
                folders.append((inst, path))
            elif inst.is_value_object():
                values.append((inst, path))
            else:
                other.append((inst, path))
        
        # Header
        lines.append("=" * 70)
        lines.append("NON-SCRIPT ITEMS")
        lines.append("=" * 70)
        lines.append(f"Source: {self.source_filename}")
        lines.append(f"Generated alongside LUA script files")
        lines.append("=" * 70)
        lines.append("")
        
        # Remotes/Bindables Section
        if remotes:
            lines.append("-" * 70)
            lines.append(f"REMOTES & BINDABLES ({len(remotes)})")
            lines.append("-" * 70)
            for inst, path in remotes:
                lines.append(f"[{inst.class_name}] {inst.name}")
                lines.append(f"    Path: {path}")
            lines.append("")
        
        # Models Section
        if models:
            lines.append("-" * 70)
            lines.append(f"MODELS ({len(models)})")
            lines.append("-" * 70)
            for inst, path in models:
                child_count = inst.count_children_recursive()
                script_count = len(inst.find_scripts_recursive())
                lines.append(f"[Model] {inst.name}")
                lines.append(f"    Path: {path}")
                lines.append(f"    Contains: {child_count} objects, {script_count} scripts")
            lines.append("")
        
        # Folders Section
        if folders:
            lines.append("-" * 70)
            lines.append(f"FOLDERS ({len(folders)})")
            lines.append("-" * 70)
            for inst, path in folders:
                lines.append(f"[Folder] {inst.name}")
                lines.append(f"    Path: {path}")
                lines.append(f"    Children: {len(inst.children)}")
            lines.append("")
        
        # Values Section
        if values:
            lines.append("-" * 70)
            lines.append(f"VALUE OBJECTS ({len(values)})")
            lines.append("-" * 70)
            for inst, path in values:
                value = inst.properties.get("Value", "")
                lines.append(f"[{inst.class_name}] {inst.name}")
                lines.append(f"    Path: {path}")
                lines.append(f"    Value: {value}")
            lines.append("")
        
        # Other Section
        if other:
            lines.append("-" * 70)
            lines.append(f"OTHER ITEMS ({len(other)})")
            lines.append("-" * 70)
            for inst, path in other:
                lines.append(f"[{inst.class_name}] {inst.name}")
                lines.append(f"    Path: {path}")
            lines.append("")
        
        # Summary
        total = len(remotes) + len(models) + len(folders) + len(values) + len(other)
        lines.append("=" * 70)
        lines.append("SUMMARY")
        lines.append("=" * 70)
        lines.append(f"Remotes/Bindables: {len(remotes)}")
        lines.append(f"Models: {len(models)}")
        lines.append(f"Folders: {len(folders)}")
        lines.append(f"Value Objects: {len(values)}")
        lines.append(f"Other: {len(other)}")
        lines.append(f"TOTAL: {total}")
        lines.append("=" * 70)
        
        return "\n".join(lines)


# ============================================================================
# MAIN WINDOW
# ============================================================================

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.root_instance: Optional[RobloxInstance] = None
        self.current_file = ""
        self.loader: Optional[LoaderThread] = None
        
        self.setup_ui()
    
    def setup_ui(self):
        self.setWindowTitle("RBXL to Text Tool")
        self.setMinimumSize(1000, 700)
        self.resize(1200, 800)
        
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        
        # Top buttons
        btn_row1 = QHBoxLayout()
        
        self.btn_load = QPushButton("📂 Select Roblox Place File")
        self.btn_load.setMinimumHeight(45)
        self.btn_load.clicked.connect(self.select_file)
        btn_row1.addWidget(self.btn_load)
        
        self.btn_export = QPushButton("💾 Export Selected")
        self.btn_export.setMinimumHeight(45)
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self.export_selected)
        btn_row1.addWidget(self.btn_export)
        
        layout.addLayout(btn_row1)
        
        # Selection buttons
        btn_row2 = QHBoxLayout()
        
        self.btn_select_all = QPushButton("☑️ Select All")
        self.btn_select_all.setMinimumHeight(35)
        self.btn_select_all.setEnabled(False)
        self.btn_select_all.clicked.connect(self.select_all)
        btn_row2.addWidget(self.btn_select_all)
        
        self.btn_deselect = QPushButton("⬜ Deselect All")
        self.btn_deselect.setMinimumHeight(35)
        self.btn_deselect.setEnabled(False)
        self.btn_deselect.clicked.connect(self.deselect_all)
        btn_row2.addWidget(self.btn_deselect)
        
        self.btn_scripts = QPushButton("📜 Scripts Only")
        self.btn_scripts.setMinimumHeight(35)
        self.btn_scripts.setEnabled(False)
        self.btn_scripts.clicked.connect(self.select_scripts)
        btn_row2.addWidget(self.btn_scripts)
        
        self.btn_remotes = QPushButton("📡 Remotes Only")
        self.btn_remotes.setMinimumHeight(35)
        self.btn_remotes.setEnabled(False)
        self.btn_remotes.clicked.connect(self.select_remotes)
        btn_row2.addWidget(self.btn_remotes)
        
        self.btn_models = QPushButton("🧱 Models Only")
        self.btn_models.setMinimumHeight(35)
        self.btn_models.setEnabled(False)
        self.btn_models.clicked.connect(self.select_models)
        btn_row2.addWidget(self.btn_models)
        
        layout.addLayout(btn_row2)
        
        # Info label
        self.lbl_info = QLabel("No file loaded - Click 'Select Roblox Place File' to begin")
        self.lbl_info.setAlignment(Qt.AlignCenter)
        self.lbl_info.setMinimumHeight(30)
        layout.addWidget(self.lbl_info)
        
        # Progress bar
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        self.progress.setMinimumHeight(20)
        layout.addWidget(self.progress)
        
        # Tree and log
        splitter = QSplitter(Qt.Vertical)
        
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Roblox Explorer - Check items to export"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setIndentation(25)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.setAnimated(True)
        splitter.addWidget(self.tree)
        
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(180)
        self.log.setFont(QFont("Consolas", 9))
        splitter.addWidget(self.log)
        
        splitter.setSizes([550, 180])
        layout.addWidget(splitter)
        
        # Status bar
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Ready")
        
        # UNIFORM DARK GRAY THEME
        self.setStyleSheet("""
            * {
                font-family: 'Segoe UI', sans-serif;
            }
            
            QMainWindow, QWidget {
                background-color: #2d2d2d;
                color: #c8c8c8;
            }
            
            QPushButton {
                background-color: #3d3d3d;
                color: #c8c8c8;
                border: 1px solid #505050;
                border-radius: 5px;
                padding: 8px 16px;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #484848;
                border-color: #606060;
            }
            QPushButton:pressed {
                background-color: #525252;
            }
            QPushButton:disabled {
                background-color: #333333;
                color: #666666;
                border-color: #404040;
            }
            
            QTreeWidget {
                background-color: #353535;
                alternate-background-color: #323232;
                color: #c8c8c8;
                border: 1px solid #505050;
                border-radius: 5px;
                font-size: 13px;
                selection-background-color: #505050;
                selection-color: #ffffff;
            }
            QTreeWidget::item {
                padding: 5px;
                border-radius: 3px;
            }
            QTreeWidget::item:hover {
                background-color: #404040;
            }
            QTreeWidget::item:selected {
                background-color: #505050;
            }
            
            QHeaderView::section {
                background-color: #3d3d3d;
                color: #c8c8c8;
                padding: 10px;
                border: none;
                border-bottom: 1px solid #505050;
                font-weight: bold;
                font-size: 13px;
            }
            
            QTextEdit {
                background-color: #353535;
                color: #b0b0b0;
                border: 1px solid #505050;
                border-radius: 5px;
                selection-background-color: #505050;
            }
            
            QLabel {
                color: #a0a0a0;
                font-size: 12px;
                background-color: transparent;
            }
            
            QStatusBar {
                background-color: #3d3d3d;
                color: #c8c8c8;
                border-top: 1px solid #505050;
            }
            
            QProgressBar {
                border: 1px solid #505050;
                border-radius: 5px;
                background-color: #353535;
                text-align: center;
                color: #c8c8c8;
            }
            QProgressBar::chunk {
                background-color: #606060;
                border-radius: 4px;
            }
            
            QSplitter::handle {
                background-color: #505050;
                height: 4px;
            }
            
            QScrollBar:vertical {
                background-color: #2d2d2d;
                width: 12px;
                border-radius: 6px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background-color: #505050;
                border-radius: 6px;
                min-height: 30px;
                margin: 2px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #606060;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background-color: transparent;
                height: 0px;
            }
            
            QScrollBar:horizontal {
                background-color: #2d2d2d;
                height: 12px;
                border-radius: 6px;
                margin: 0;
            }
            QScrollBar::handle:horizontal {
                background-color: #505050;
                border-radius: 6px;
                min-width: 30px;
                margin: 2px;
            }
            QScrollBar::handle:horizontal:hover {
                background-color: #606060;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
                background-color: transparent;
                width: 0px;
            }
            
            QToolTip {
                background-color: #3d3d3d;
                color: #c8c8c8;
                border: 1px solid #505050;
                padding: 5px;
                border-radius: 3px;
            }
        """)
    
    def add_log(self, msg: str, color: str = "#b0b0b0"):
        self.log.append(f'<span style="color:{color}">» {msg}</span>')
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())
    
    def select_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Roblox Place File", "",
            "Roblox Place Files (*.rbxl *.rbxlx);;All Files (*.*)"
        )
        if path:
            self.load_file(path)
    
    def load_file(self, path: str):
        self.current_file = path
        self.tree.clear()
        self.log.clear()
        self.add_log(f"Loading: {os.path.basename(path)}")
        
        self.progress.setVisible(True)
        self.btn_load.setEnabled(False)
        
        self.loader = LoaderThread(path)
        self.loader.finished.connect(self.on_loaded)
        self.loader.progress.connect(lambda m: self.add_log(m))
        self.loader.start()
    
    def on_loaded(self, result, debug_msgs):
        self.progress.setVisible(False)
        self.btn_load.setEnabled(True)
        
        for msg in debug_msgs:
            self.add_log(f"[DEBUG] {msg}", "#707070")
        
        if isinstance(result, Exception):
            self.add_log(f"ERROR: {result}", "#ff6b6b")
            QMessageBox.critical(self, "Error", f"Failed to load file:\n\n{result}")
            return
        
        self.root_instance = result
        self.populate_tree()
        
        self.btn_export.setEnabled(True)
        self.btn_select_all.setEnabled(True)
        self.btn_deselect.setEnabled(True)
        self.btn_scripts.setEnabled(True)
        self.btn_remotes.setEnabled(True)
        self.btn_models.setEnabled(True)
        
        stats = self.count_stats(self.root_instance)
        
        self.lbl_info.setText(
            f"📄 {os.path.basename(self.current_file)}  |  "
            f"📦 {stats['total']} objects  |  "
            f"📜 {stats['scripts']} scripts  |  "
            f"📡 {stats['remotes']} remotes  |  "
            f"🧱 {stats['models']} models  |  "
            f"📁 {stats['folders']} folders"
        )
        
        self.add_log(f"Loaded {stats['total']} objects successfully!", "#7dce82")
        self.status.showMessage(f"Loaded: {os.path.basename(self.current_file)}")
    
    def count_stats(self, inst: RobloxInstance) -> Dict[str, int]:
        stats = {"total": 0, "scripts": 0, "remotes": 0, "models": 0, "folders": 0}
        
        def count(i: RobloxInstance):
            stats["total"] += 1
            if i.is_script():
                stats["scripts"] += 1
            if i.is_remote_or_bindable():
                stats["remotes"] += 1
            if i.class_name == "Model":
                stats["models"] += 1
            if i.class_name == "Folder":
                stats["folders"] += 1
            for c in i.children:
                count(c)
        
        for child in inst.children:
            count(child)
        
        return stats
    
    def populate_tree(self):
        self.tree.clear()
        
        if not self.root_instance:
            return
        
        sorted_children = sorted(
            self.root_instance.children,
            key=lambda x: (0 if x.is_service() else 1, x.name.lower())
        )
        
        for child in sorted_children:
            item = self.create_tree_item(child)
            self.tree.addTopLevelItem(item)
        
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if isinstance(item, RobloxTreeItem) and item.instance.is_service():
                item.setExpanded(True)
    
    def create_tree_item(self, inst: RobloxInstance) -> RobloxTreeItem:
        item = RobloxTreeItem(inst)
        
        sorted_children = sorted(
            inst.children,
            key=lambda x: (0 if x.is_service() else 1, 0 if x.class_name == "Folder" else 1, x.name.lower())
        )
        
        for child in sorted_children:
            child_item = self.create_tree_item(child)
            item.addChild(child_item)
        
        return item
    
    def select_all(self):
        self._set_all_checked(Qt.Checked)
        self.add_log("Selected all items")
    
    def deselect_all(self):
        self._set_all_checked(Qt.Unchecked)
        self.add_log("Deselected all items")
    
    def _set_all_checked(self, state):
        def set_recursive(item):
            item.setCheckState(0, state)
            for i in range(item.childCount()):
                set_recursive(item.child(i))
        
        for i in range(self.tree.topLevelItemCount()):
            set_recursive(self.tree.topLevelItem(i))
    
    def select_scripts(self):
        self.deselect_all()
        count = self._select_by_class(["Script", "LocalScript", "ModuleScript"])
        self.add_log(f"Selected {count} scripts")
    
    def select_remotes(self):
        self.deselect_all()
        count = self._select_by_class([
            "RemoteEvent", "RemoteFunction",
            "BindableEvent", "BindableFunction",
            "UnreliableRemoteEvent"
        ])
        self.add_log(f"Selected {count} remotes/bindables")
    
    def select_models(self):
        self.deselect_all()
        count = self._select_by_class(["Model"])
        self.add_log(f"Selected {count} models")
    
    def _select_by_class(self, class_names: List[str]) -> int:
        count = 0
        
        def check_recursive(item):
            nonlocal count
            if isinstance(item, RobloxTreeItem):
                if item.instance.class_name in class_names:
                    item.setCheckState(0, Qt.Checked)
                    count += 1
            for i in range(item.childCount()):
                check_recursive(item.child(i))
        
        for i in range(self.tree.topLevelItemCount()):
            check_recursive(self.tree.topLevelItem(i))
        
        return count
    
    def get_checked_items(self) -> List[RobloxTreeItem]:
        checked = []
        
        def collect(item):
            if isinstance(item, RobloxTreeItem):
                if item.checkState(0) == Qt.Checked:
                    checked.append(item)
            for i in range(item.childCount()):
                collect(item.child(i))
        
        for i in range(self.tree.topLevelItemCount()):
            collect(self.tree.topLevelItem(i))
        
        return checked
    
    def export_selected(self):
        items = self.get_checked_items()
        
        if not items:
            QMessageBox.warning(self, "Nothing Selected",
                "Please check at least one item to export.")
            return
        
        # Count scripts and non-scripts in selection
        script_count = sum(1 for item in items if item.instance.is_script())
        nonscript_count = len(items) - script_count
        
        # Show options dialog
        dialog = ExportOptionsDialog(
            self, 
            script_count=script_count, 
            nonscript_count=nonscript_count,
            total_count=len(items)
        )
        if dialog.exec_() != QDialog.Accepted:
            return
        
        options = dialog.get_options()

        if options["format"] == "txt":
            self.export_as_txt(items, options["auto_split"])
        elif options["format"] == "luau":
            self.export_as_files(items, script_ext=".luau", rojo_adjusted=options.get("rojo_adjusted", False))
        else:
            # LUA
            self.export_as_files(items, script_ext=".lua")
    
    def export_as_txt(self, items: List[RobloxTreeItem], auto_split: bool):
        """Export to TXT format with headers and readability."""
        default_name = os.path.splitext(os.path.basename(self.current_file))[0]
        
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Export", f"{default_name}_export.txt",
            "Text Files (*.txt);;All Files (*.*)"
        )
        
        if not path:
            return
        
        self.add_log(f"Exporting {len(items)} items to TXT...")
        
        try:
            generator = ExportGenerator(
                items, 
                os.path.basename(self.current_file),
                auto_split=auto_split
            )
            file_parts = generator.generate()
            
            base_path = os.path.splitext(path)[0]
            ext = os.path.splitext(path)[1] or ".txt"
            
            saved_files = []
            total_chars = 0
            
            if len(file_parts) == 1:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(file_parts[0][0])
                saved_files.append(path)
                char_count = len(file_parts[0][0])
                total_chars = char_count
                self.add_log(f"Exported: {os.path.basename(path)} ({char_count:,} chars)", "#7dce82")
            else:
                for i, (content, item_count) in enumerate(file_parts):
                    part_path = f"{base_path}_part{i+1}{ext}"
                    with open(part_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                    saved_files.append(part_path)
                    char_count = len(content)
                    total_chars += char_count
                    self.add_log(f"Part {i+1}: {os.path.basename(part_path)} ({char_count:,} chars)", "#7dce82")
            
            if len(saved_files) == 1:
                QMessageBox.information(self, "Export Complete",
                    f"Exported {len(items)} items to:\n\n"
                    f"{saved_files[0]}\n\n"
                    f"Total: {total_chars:,} characters")
            else:
                file_list = "\n".join([f"  • {os.path.basename(f)}" for f in saved_files])
                QMessageBox.information(self, "Export Complete",
                    f"Exported {len(items)} items to {len(saved_files)} files:\n\n"
                    f"{file_list}\n\n"
                    f"Total: {total_chars:,} characters")
        
        except Exception as e:
            self.add_log(f"Export error: {e}", "#ff6b6b")
            QMessageBox.critical(self, "Export Error", str(e))
    
    def export_as_lua(self, items: List[RobloxTreeItem]):
        """Compatibility wrapper: exports scripts as structured .lua files (no .txt sidecars)."""
        self.export_as_files(items, script_ext=".lua")
    
    
    def export_as_files(self, items: List[RobloxTreeItem], script_ext: str = ".lua", rojo_adjusted: bool = False):
        """Export scripts as files while preserving Roblox folder structure.

        - Scripts -> one file per script containing RAW code only
          (.client{ext} / .server{ext} / .module{ext})
        - Non-scripts -> skipped (NO .txt files are created)

        Args:
            items: Checked items from the tree.
            script_ext: File extension for exported scripts. Use ".lua" or ".luau".
        """
        if not items:
            QMessageBox.warning(self, "Nothing Selected", "Please check at least one item to export.")
            return

        # Ask for output folder
        folder = QFileDialog.getExistingDirectory(
            self, "Select Output Folder for Structured Export"
        )
        if not folder:
            return

        if not script_ext.startswith("."):
            script_ext = "." + script_ext

        self.add_log(f"Script files export to: {folder} ({script_ext})")

        try:
            import bisect

            # Precompute paths for descendant checks
            paths = [item.instance.get_path() for item in items]
            paths_sorted = sorted(paths)

            def has_selected_descendant(p: str) -> bool:
                """True if any other selected path starts with p + '.'"""
                idx = bisect.bisect_right(paths_sorted, p)
                if idx < len(paths_sorted) and paths_sorted[idx].startswith(p + "."):
                    return True
                return False

            # Track duplicates within the SAME directory
            dir_name_counts = {}  # (dirpath, basename) -> count

            def unique_path(dirpath: str, filename: str) -> str:
                key = (dirpath, filename)
                if key not in dir_name_counts and not os.path.exists(os.path.join(dirpath, filename)):
                    dir_name_counts[key] = 0
                    return os.path.join(dirpath, filename)

                # Resolve collisions
                count = dir_name_counts.get(key, 0) + 1
                dir_name_counts[key] = count
                base, ext = os.path.splitext(filename)
                new_name = f"{base}_{count}{ext}"
                return os.path.join(dirpath, new_name)

            def write_text_file(path: str, content: str):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)

            saved_scripts = 0
            skipped_non_scripts = 0
            skipped_outside_rojo = 0

            # Optional Rojo Adjusted mapping (LUAU only):
            #   StarterPlayer.StarterPlayerScripts -> client/
            #   ServerScriptService               -> server/
            #   ReplicatedStorage                 -> shared/
            if rojo_adjusted:
                client_root = os.path.join(folder, "client")
                server_root = os.path.join(folder, "server")
                shared_root = os.path.join(folder, "shared")
                os.makedirs(client_root, exist_ok=True)
                os.makedirs(server_root, exist_ok=True)
                os.makedirs(shared_root, exist_ok=True)
                self.add_log("Rojo Adjusted: ON (client/server/shared)", "#70a0ff")


            for tree_item in items:
                inst = tree_item.instance
                inst_path = inst.get_path()
                raw_parts = inst_path.split(".") if inst_path else []
                safe_parts = [self._make_safe_filename(p) for p in (raw_parts if raw_parts else ["Unknown"])]

                if rojo_adjusted:
                    if not inst.is_script():
                        skipped_non_scripts += 1
                        continue

                    target_root = None
                    rel_parts = None

                    if len(raw_parts) >= 2 and raw_parts[0] == "StarterPlayer" and raw_parts[1] == "StarterPlayerScripts":
                        target_root = client_root
                        rel_parts = safe_parts[2:]
                    elif len(raw_parts) >= 1 and raw_parts[0] == "ServerScriptService":
                        target_root = server_root
                        rel_parts = safe_parts[1:]
                    elif len(raw_parts) >= 1 and raw_parts[0] == "ReplicatedStorage":
                        target_root = shared_root
                        rel_parts = safe_parts[1:]
                    else:
                        skipped_outside_rojo += 1
                        continue

                    if not rel_parts:
                        rel_parts = [safe_parts[-1]]

                    out_dir = os.path.join(target_root, *rel_parts[:-1]) if len(rel_parts) > 1 else target_root
                    os.makedirs(out_dir, exist_ok=True)

                    base_name = rel_parts[-1]
                    if inst.class_name == "LocalScript":
                        filename = f"{base_name}.client{script_ext}"
                    elif inst.class_name == "Script":
                        filename = f"{base_name}.server{script_ext}"
                    elif inst.class_name == "ModuleScript":
                        filename = f"{base_name}.module{script_ext}"
                    else:
                        filename = f"{base_name}{script_ext}"

                    filepath = unique_path(out_dir, filename)
                    source = inst.get_script_source()
                    write_text_file(filepath, source or "")
                    saved_scripts += 1
                    self.add_log(f"  ✓ {os.path.relpath(filepath, folder)}", "#7dce82")
                    continue

                # Always ensure top-level service folder exists
                if safe_parts:
                    os.makedirs(os.path.join(folder, safe_parts[0]), exist_ok=True)

                if inst.is_script():
                    out_dir = os.path.join(folder, *safe_parts[:-1]) if len(safe_parts) > 1 else folder
                    os.makedirs(out_dir, exist_ok=True)

                    base_name = safe_parts[-1]
                    if inst.class_name == "LocalScript":
                        filename = f"{base_name}.client{script_ext}"
                    elif inst.class_name == "Script":
                        filename = f"{base_name}.server{script_ext}"
                    elif inst.class_name == "ModuleScript":
                        filename = f"{base_name}.module{script_ext}"
                    else:
                        filename = f"{base_name}{script_ext}"

                    filepath = unique_path(out_dir, filename)
                    source = inst.get_script_source()
                    write_text_file(filepath, source or "")
                    saved_scripts += 1
                    self.add_log(f"  ✓ {os.path.relpath(filepath, folder)}", "#7dce82")
                else:
                    # Still create folders for containers if they contain selected descendants,
                    # so the structure looks right, but do NOT write any .txt files.
                    if inst.is_service() or has_selected_descendant(inst_path):
                        out_dir = os.path.join(folder, *safe_parts)
                        os.makedirs(out_dir, exist_ok=True)
                    skipped_non_scripts += 1

            if saved_scripts == 0:
                QMessageBox.warning(
                    self,
                    "Nothing Exported",
                    "No scripts were exported.\n\n"
                    "Tip: Select Script/LocalScript/ModuleScript items, or use the 'Scripts Only' button."
                )
                return

            QMessageBox.information(
                self,
                "Export Complete",
                f"Export finished in:\n{folder}\n\n"
                f"Created:\n  • Script files: {saved_scripts}\n\n"
                f"Skipped non-scripts: {skipped_non_scripts} (no .txt created)\n"
                f"{('Skipped outside Rojo targets: ' + str(skipped_outside_rojo)) if rojo_adjusted else ''}"
            )

        except Exception as e:
            self.add_log(f"Structured export error: {e}", "#ff6b6b")
            QMessageBox.critical(self, "Export Error", str(e))

    def _format_instance_info(self, inst: RobloxInstance) -> str:
        """Create a human-readable metadata dump for non-script instances."""
        lines = []
        lines.append(f"Name: {inst.name}")
        lines.append(f"Class: {inst.class_name}")
        lines.append(f"Path: {inst.get_path()}")
        lines.append(f"Children: {len(inst.children)}")
        lines.append("")
        lines.append("Properties:")
        if not inst.properties:
            lines.append("  (none loaded)")
        else:
            # Keep it readable and safe
            for k in sorted(inst.properties.keys()):
                if k.lower() == "source":
                    continue
                v = inst.properties.get(k)
                try:
                    s = str(v)
                except Exception:
                    s = repr(v)
                if len(s) > 500:
                    s = s[:500] + "…"
                lines.append(f"  - {k}: {s}")
        lines.append("")
        return "\n".join(lines)

    def _make_safe_filename(self, name: str) -> str:
        """Convert script name to safe filename."""
        # Remove or replace invalid characters
        safe = re.sub(r'[<>:"/\\|?*]', '_', name)
        safe = re.sub(r'\s+', '_', safe)
        safe = safe.strip('._')
        
        # Limit length
        if len(safe) > 100:
            safe = safe[:100]
        
        # Ensure not empty
        if not safe:
            safe = "unnamed_script"
        
        return safe
    
    def closeEvent(self, event):
        if self.loader and self.loader.isRunning():
            self.loader.terminate()
            self.loader.wait()
        event.accept()


# ============================================================================
# MAIN
# ============================================================================

def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    
    # ===== Splash Screen =====
    splash_dialog = QDialog()
    splash_dialog.setWindowTitle("RBXL to Text Tool")
    splash_dialog.setFixedSize(420, 260)
    splash_dialog.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
    splash_dialog.setStyleSheet("""
        QDialog {
            background-color: #1e1e1e;
            border: 2px solid #4080b0;
            border-radius: 10px;
        }
        QLabel {
            color: #ffffff;
        }
        QPushButton {
            background-color: #4080b0;
            color: white;
            border: none;
            border-radius: 5px;
            padding: 12px 40px;
            font-weight: bold;
            font-size: 13px;
        }
        QPushButton:hover {
            background-color: #5090c0;
        }
    """)
    
    splash_layout = QVBoxLayout(splash_dialog)
    splash_layout.setSpacing(12)
    splash_layout.setContentsMargins(40, 35, 40, 35)
    
    # Title
    title_label = QLabel("RBXL to Text Tool")
    title_label.setAlignment(Qt.AlignCenter)
    title_label.setStyleSheet("font-size: 26px; font-weight: bold; color: #4090d0;")
    splash_layout.addWidget(title_label)
    
    splash_layout.addSpacing(8)
    
    # Made by
    made_by = QLabel("Made by Undmg")
    made_by.setAlignment(Qt.AlignCenter)
    made_by.setStyleSheet("font-size: 16px; color: #c0c0c0;")
    splash_layout.addWidget(made_by)
    
    # Website (clickable)
    website = QLabel('<a href="https://undmg.github.io" style="color: #70a0ff; text-decoration: none;">undmg.github.io</a>')
    website.setAlignment(Qt.AlignCenter)
    website.setStyleSheet("font-size: 14px;")
    website.setOpenExternalLinks(True)
    website.setCursor(Qt.PointingHandCursor)
    splash_layout.addWidget(website)
    
    splash_layout.addSpacing(20)
    
    # Start button
    start_btn = QPushButton("Start")
    start_btn.setFixedWidth(120)
    start_btn.clicked.connect(splash_dialog.accept)
    splash_layout.addWidget(start_btn, alignment=Qt.AlignCenter)
    
    # Center the splash on screen
    splash_dialog.show()
    screen_geo = app.primaryScreen().geometry()
    splash_dialog.move(
        (screen_geo.width() - splash_dialog.width()) // 2,
        (screen_geo.height() - splash_dialog.height()) // 2
    )
    
    if splash_dialog.exec_() != QDialog.Accepted:
        sys.exit(0)
    
    # ===== Main Window =====
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()