# Contributing to WinSnap

Thanks for your interest in WinSnap! This guide explains how to set up a
development environment, the project's conventions, and what makes a good
contribution.

---

## Development setup

WinSnap targets Windows 10 / 11 with Python 3.11+ and uses **only the standard
library** at runtime. There's no `requirements.txt` for runtime; everything
WinSnap needs ships with Python.

```bash
git clone https://github.com/<your-username>/winsnap.git
cd winsnap

# Optional: build the standalone .exe
pip install pyinstaller
python build.py
```

To run from source:

```bash
python export.py
python restore.py snapshot.winsnap
```

---

## Running tests

```bash
python tests/smoke_apps.py
```

The test suite is minimal today — mostly unit tests for the apps filter and
normalization logic. Pull requests adding more coverage are welcome, especially
for new modules.

---

## Project conventions

### Module pattern

Every settings category is a self-contained module under `modules/` exposing:

```python
def export(snapshot_dir: Path) -> dict
def restore(snapshot: dict, snapshot_dir: Path) -> None
```

That's the entire contract. The dict returned by `export()` is JSON-serialized
into `snapshot.json` inside the `.winsnap` zip. `restore()` receives that same
dict back along with the snapshot directory (in case the module bundled
auxiliary files like fonts or shortcuts).

### Stdlib only

Runtime code must not depend on third-party packages. PyInstaller (build tool
only) and any future dev tools are exempt.

### HKCU-only writes

Restore code must not write to `HKEY_LOCAL_MACHINE`. Reading HKLM during export
is sometimes necessary, but applying changes there requires admin and risks
breaking other users on shared machines. Stick to `HKCU\...`.

### Idempotent restore

Running `restore.py` twice in a row should produce the same end state. Don't
duplicate PATH entries, startup items, or list-style values. Where merging is
needed (PATH, keyboard layouts), document the merge behavior in the module
docstring.

### Encoding

Both entry points reconfigure stdout to UTF-8 to avoid cp1252 crashes on
default Windows consoles. New modules can freely print Unicode (✓, →, etc.)
in messages.

### Logging style

One concise line per major action. Prefix with the module name:

```
[fonts] Bundled 12 user fonts (4.3 MB).
[startup] Skipping 'OldVPN' (binary not found): C:\Old\Path.exe
```

Avoid stack traces in normal output — let the parent script catch and report.

---

## Adding a new settings category

1. Create `modules/your_category.py` matching the export/restore contract.
2. Add it to the modules list in `export.py` (`_build_modules`) and
   `restore.py` (`ALL_MODULES`). Order in `restore.py` matters — slot it in
   so it doesn't run after Explorer is restarted unless that's intentional.
3. Add a `_summarize` clause to `restore.py` so `--dry-run` produces a
   useful one-liner.
4. Bump `SNAPSHOT_FORMAT_VERSION` in `export.py`:
   - **MINOR** for additive changes (new module, new fields). Old restorers
     ignore the new key gracefully.
   - **MAJOR** for breaking changes (renaming a key, changing a type).
5. Add a row to the feature table in `README.md`.
6. Add a smoke test under `tests/` if the module has non-trivial logic.

---

## Pull request checklist

- [ ] `python tests/smoke_apps.py` passes (and any new tests you added)
- [ ] `python -m py_compile <changed files>` produces no errors
- [ ] You ran a manual round-trip of the affected module on your own PC
- [ ] README and CHANGELOG updated if behavior changed
- [ ] Format version bumped if the snapshot schema changed

---

## Reporting issues

Please use the issue templates under `.github/ISSUE_TEMPLATE/`.
For security-sensitive reports, see [SECURITY.md](SECURITY.md) instead.
