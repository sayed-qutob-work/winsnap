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
git clone https://github.com/sayed-qutob-work/winsnap.git
cd winsnap

# Dev/test dependencies (pytest + hypothesis)
pip install -r requirements-dev.txt

# Optional: the PyQt6 desktop app
pip install PyQt6

# Optional: build the standalone .exe
pip install pyinstaller
python build.py
```

To run from source:

```bash
python export.py
python restore.py snapshot.winsnap
python gui.py            # PyQt6 desktop app (Export/Restore tabs)
```

---

## Running tests

```bash
python -m pytest tests/ -q
```

The suite is mock-heavy — it never touches the real registry — and mixes plain
`pytest` cases with `hypothesis` property-based tests. GUI-related tests need
`PyQt6` installed and run headlessly via `QT_QPA_PLATFORM=offscreen`.

CI additionally runs the standalone apps-filter smoke test (`python
tests/smoke_apps.py`), which exercises the filtering/normalization logic without
the interactive checklist. Pull requests adding more coverage are welcome,
especially for new modules.

---

## Project conventions

### Module pattern

Every settings category is a self-contained module under `modules/` exposing:

```python
def export(snapshot_dir: Path) -> dict
def restore(snapshot: dict, snapshot_dir: Path) -> dict   # report: status/reason/items
def verify(snapshot: dict, snapshot_dir: Path) -> dict     # same report shape
```

The dict returned by `export()` is JSON-serialized into `snapshot.json` inside
the `.winsnap` zip. `restore()` and `verify()` receive that same dict back along
with the snapshot directory (in case the module bundled auxiliary files like
fonts or shortcuts) and each return a **report** dict — `status` is one of
`matched`, `partial`, `failed`, or `skipped` (see `modules/report.py`). Both the
CLI and the GUI classify outcomes from this `status` field, never from whether
the call happened to raise.

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

1. Create `modules/your_category.py` matching the export/restore/verify
   contract.
2. Add its name to `MODULE_NAMES` in `modules/manifest.py` — the single source
   of truth for the module *set* and restore *order*. `export.py` and
   `restore.py` both derive their lists from it, so you edit exactly one place.
   Order matters: slot it in so it doesn't run after Explorer is restarted
   (the `taskbar`/Explorer-managed modules run last) unless that's intentional.
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

- [ ] `python -m pytest tests/ -q` passes (and any new tests you added)
- [ ] `python -m py_compile <changed files>` produces no errors
- [ ] You ran a manual round-trip of the affected module on your own PC
- [ ] README and CHANGELOG updated if behavior changed
- [ ] Format version bumped if the snapshot schema changed

---

## Reporting issues

Please use the issue templates under `.github/ISSUE_TEMPLATE/`.
For security-sensitive reports, see [SECURITY.md](SECURITY.md) instead.
