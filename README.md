# RBXL to Text Tool

Read, diff and check Roblox place and model files (`.rbxl`, `.rbxlx`, `.rbxm`, `.rbxmx`) **without opening Studio**.

It comes in two parts that share one parser:

- **`rbxtool`**: a command-line tool for terminals, Git and CI (GitHub Actions).
- **The desktop app** (`RBXL to Text.pyw`): browse a place as a tree and export scripts.

Why outside Studio? A plugin only sees the one place open in Studio. This tool works on files: in Git history, in CI, on a folder of downloaded models, or on a damaged file Studio refuses to open.

---

## Install

```bash
pip install git+https://github.com/undmg/rbxl-to-text-tool
```

Python 3.9 or newer. For the desktop app also run `pip install PyQt5`.

---

## Commands

| Command | What it does |
|---|---|
| `rbxtool dump place.rbxl` | Show the instance tree. `-s` adds script source, `-p` adds properties, attributes and tags, `--json` gives everything as JSON. |
| `rbxtool map place.rbxl` | One-page overview: every script, which modules it requires, and which scripts fire and handle each remote. |
| `rbxtool diff old.rbxl new.rbxl` | What was added, removed and changed, with line-by-line script diffs. `--format markdown` for PR comments. |
| `rbxtool check place.rbxl` | Find backdoors, leaked secrets, broken remotes and more (see below). Exits with code 1 when it finds errors, so CI can block a build. |
| `rbxtool stats place.rbxl` | What makes the file big: bytes on disk per class and property, largest scripts and values. |
| `rbxtool export place.rbxl out/` | Write scripts to files with Rojo naming. `--project` also writes `default.project.json` so `rojo serve` works. |
| `rbxtool git-setup --apply` | Make `git diff` show `.rbxl` files as readable text (see below). |

### Readable Git diffs

Git normally says `Binary files differ` for place files. After `rbxtool git-setup --apply` in your repository, `git diff`, `git log -p` and `git show` print the real changes:

```diff
@@ -6,8 +6,9 @@ [Workspace.Lava] Part
   Anchored = true
-  Size = 10, 1, 10
-  @Damage = 25
+  Size = 20, 1, 10
+  @Damage = 50
```

Commit the `.gitattributes` file it creates. Each teammate runs `rbxtool git-setup --apply` once (Git doesn't share config through the repository).

### Checks

| Rule | Severity | Finds |
|---|---|---|
| RBX001 | error | `require(<number>)`, the classic free-model backdoor |
| RBX002 | error | `loadstring()` |
| RBX003 | warning | `getfenv` / `setfenv` |
| RBX004 | error | `require`, `loadstring` etc. hidden with escape codes or passed around as strings |
| RBX005 | error | Discord/Slack webhooks and API keys in scripts (the secret is never printed) |
| RBX006 | warning | Obfuscated code |
| RBX007 | warning | `LoadAsset(<number>)` |
| RBX010 | info | `wait`/`spawn`/`delay` and `Instance.new(class, parent)` |
| RBX020 | warning | A remote that's fired, but nothing handles it on the other side |
| RBX021 | info | A remote no script uses |
| RBX030 | error | A damaged file |
| RBX031 | error | A file over its size budget, or growing too fast |

`rbxtool check --list-rules` prints this list. Check a whole folder of downloaded models with `rbxtool check models/`.

To silence a finding, add a comment on that line or on the line above it:

```lua
wait(1) -- rbxtool: ignore RBX010
```

`-- rbxtool: ignore-file RBX010` anywhere in a script silences that rule for the whole script.

Project-wide settings go in an `rbxtool.toml` (see [`rbxtool.example.toml`](rbxtool.example.toml)).

### GitHub Actions

```yaml
# .github/workflows/place-check.yml
on: [pull_request]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install git+https://github.com/undmg/rbxl-to-text-tool
      - run: rbxtool check game.rbxl --format github
```

`--format github` shows each finding as an annotation on the pull request. For Rojo projects, run `rojo build -o game.rbxl` first and check the built file.

### Damaged files

A file that's truncated or partly corrupted still loads: the tool skips the broken parts, recovers everything else, and reports what it lost. `rbxtool dump -s broken.rbxl` shows which scripts survived, and `rbxtool export` saves them.

---

## Desktop app

```bash
pip install PyQt5 lz4 zstandard
python "RBXL to Text.pyw"
```

On Windows you can also double-click the `.pyw` file.

1. Click **Select Roblox Place File** and open a `.rbxl`, `.rbxlx`, `.rbxm` or `.rbxmx`.
2. Browse the tree. Clicking an object shows its properties, attributes and tags.
3. Tick what you want and click **Export Selected**:
   - **TXT**: every selected script and object in one readable file, optionally split into ~90k-character parts.
   - **LUA / LUAU**: one file per script in the original folder structure. Rojo mode maps StarterPlayerScripts → `client/`, ServerScriptService → `server/` and ReplicatedStorage → `shared/`.

---

## Using it from Python

```python
import rbxtool

doc = rbxtool.load("game.rbxl")
for script in doc.scripts():
    print(script.get_path(), script.script_kind, len(script.source))

lava = doc.find("Workspace.Lava")
print(lava.properties["Size"], lava.attributes, lava.tags)
```

---

## Development

```bash
pip install -e ".[dev]"
pytest
```

The test files in `tests/fixtures` are written by [rbx-dom](https://github.com/rojo-rbx/rbx-dom), the library Rojo and Lune use, so the parser is tested against real Roblox-format output. To regenerate them, run `cargo run --manifest-path tests/fixgen/Cargo.toml -- tests/fixtures`.

## License

MIT

## Author

**Undmg** · [undmg.github.io](https://undmg.github.io)
