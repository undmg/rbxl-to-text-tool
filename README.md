# RBXL to Text Tool

A powerful desktop application for extracting scripts and data from Roblox place files (.rbxl / .rbxlx).

---

## ✨ Features

- **🔍 Visual Explorer** - Browse the entire hierarchy of your Roblox place file in a tree view
- **📄 Multiple Export Formats** - Export as TXT, LUA, or LUAU
- **🔧 Rojo Compatibility** - LUAU export with client/server/shared folder structure
- **📦 Batch Export** - Select multiple items and export them all at once

---

## 📥 Supported Formats

| Format | Description |
|--------|-------------|
| **TXT** | All scripts + descriptions + paths in a single readable text file. Includes Models, Folders, Remotes, and other objects. Supports auto-split for large exports. |
| **LUA** | Each script exported as a separate `.lua` file. Preserves Roblox folder structure. Extensions: `.client.lua`, `.server.lua`, `.module.lua` |
| **LUAU** | Rojo-compatible structure with `.luau` extension. Optional folder mapping: StarterPlayerScripts → `client/`, ServerScriptService → `server/`, ReplicatedStorage → `shared/` |

---

## 🚀 Getting Started

### Requirements

- Python 3.8+
- PyQt5

### Installation

```bash
pip install PyQt5 lz4
```

### Usage

```bash
python RBXLtoText.pyw
```

Or simply double-click the `.pyw` file on Windows.

---

## 📖 How to Use

1. **Open a Place File** - Click "Select Roblox Place File" and choose a `.rbxl` or `.rbxlx` file
2. **Browse the Tree** - Explore the hierarchy of your place file
3. **Select Items** - Use checkboxes to select scripts and other objects you want to export
4. **Export** - Click "Export Selected" and choose your preferred format
5. **Save** - Choose a destination folder and wait for the export to complete

---

## 🎯 Export Options

### TXT Export
- Human-readable format with headers and paths
- Includes all selected items (scripts, models, folders, remotes, values)
- Optional auto-split for files exceeding ~90k characters

### LUA Export  
- Raw code only (no headers or extra text)
- One file per script
- Preserves original Roblox folder structure
- Non-scripts are skipped

### LUAU Export
- Same as LUA but with `.luau` extension
- Optional Rojo-adjusted folder structure:
  - `StarterPlayerScripts` → `client/`
  - `ServerScriptService` → `server/`
  - `ReplicatedStorage` → `shared/`
- Scripts outside these services can be skipped in Rojo mode

---

## 📁 Output Structure

### Standard Export
```
ExportFolder/
├── Workspace/
│   ├── Part/
│   │   └── Script.server.lua
│   └── Model/
│       └── LocalScript.client.lua
└── ReplicatedStorage/
    └── ModuleScript.module.lua
```

### Rojo-Adjusted Export
```
ExportFolder/
├── client/
│   └── PlayerScript.client.luau
├── server/
│   └── ServerScript.server.luau
└── shared/
    └── SharedModule.module.luau
```

---

## 🛠️ Technical Details

- Supports both binary (`.rbxl`) and XML (`.rbxlx`) place files
- Uses LZ4 decompression for binary format
- Threaded loading to prevent UI freezing on large files
- Safe filename generation (replaces invalid characters)

---

## 📝 License

MIT License - Free to use and modify.

---

## 👤 Author

**Undmg**  
🌐 [undmg.github.io](https://undmg.github.io)

---
