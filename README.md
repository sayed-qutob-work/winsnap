# WinSnap 🪟

Transfer your Windows personality — settings, wallpaper, fonts, apps — to any new PC.
No bloatware. No viruses. Just your preferences.

Use it from the command line, or through the PyQt6 desktop app.

---

## What it transfers

| Feature | Details |
|---|---|
| 🖼️ Wallpaper | Copies your wallpaper image |
| 🎨 Theme & colors | Dark/light mode, accent color, transparency |
| 📌 Taskbar pins | All pinned app shortcuts |
| 🖱️ Mouse | Speed, scroll, button layout, double-click |
| ⌨️ Keyboard | Repeat delay & speed |
| 🖥️ Display | DPI / scaling preference |
| ⚡ Power plan | Your custom power scheme (admin needed) |
| 📦 Apps | winget-compatible apps auto-install; manual list for others |
| 📁 Explorer prefs | Hidden files, file extensions, launch-to, checkboxes, full path |
| 🖼️ Desktop icons | This PC, User folder, Network, Recycle Bin, Control Panel |
| 🔊 Sound scheme | Active scheme + per-event sound paths + system beep |
| 👆 Cursors | Cursor scheme name + per-role cursor paths |
| 🔤 Fonts | User-installed fonts (.ttf/.otf bundled in the snapshot) |
| 🚀 Startup programs | HKCU Run/RunOnce entries + Startup-folder shortcuts |
| 🌐 Environment vars | User env vars; PATH is **merged**, not replaced |
| 🌍 Region & keyboard | Locale, formats, keyboard layouts |

The apps checklist filters out OS components, updaters, runtimes, drivers,
helpers, MSI patches, and KB updates by default — so you see ~the same list
Windows shows in **Settings → Apps**, not 100+ noisy entries. Use `--show-all`
(CLI) to opt out of filtering.

---

## Requirements

- Windows 10 or 11
- Python 3.11+ (only on the machine you run it from; the standalone `.exe`
  build needs no Python)
- `winget` (built into Windows 11, available for Win10 via App Installer)
- `PyQt6` — only if you use the desktop app (`pip install PyQt6`)

The CLI tools (`export.py` / `restore.py`) need no third-party packages —
stdlib only.

---

## Desktop app

```
pip install PyQt6
python gui.py
```

The app has two tabs — **Export** and **Restore** — each backed by the same
module selector and the same backend functions the CLI uses, so behavior is
identical either way. It adds a few things the CLI doesn't:

- A live results view that groups each module's outcome as **Matched**,
  **Partial**, **Failed**, or **Skipped**, with the reason and per-item
  detail shown for anything that isn't a clean match.
- A **Verify after restore** option that re-checks applied settings against
  the snapshot and reports drift, without re-running restore.
- A collision check before export: if a named snapshot would overwrite an
  existing one, you're asked to confirm before anything runs.

`gui.py` is a thin PyQt6 layer — all the actual export/restore/verify logic
lives in `export.py`, `restore.py`, and `modules/`, so the GUI and CLI can
never disagree about what happened.

---

## CLI usage

### On your OLD PC (export)

```
python export.py
```

This creates a `winsnap_<timestamp>.winsnap` file on your Desktop.

**Options:**
```
python export.py --output C:\Users\You\Documents
python export.py --name my_work_setup
python export.py --skip fonts startup        # leave out heavy/risky modules
python export.py --only wallpaper taskbar    # capture just these
python export.py --show-all                  # don't filter the apps list
python export.py --name my_setup --force     # overwrite an existing snapshot
```

### Transfer

Copy the `.winsnap` file via USB, cloud, or network share to your new PC.
Also copy `restore.py`, `gui.py`, and the `modules/` folder (or use the
`.exe` build below).

### On your NEW PC (restore)

```
python restore.py winsnap_20260528_143022.winsnap
```

**Options:**
```
# Preview what would change without touching anything
python restore.py my_snapshot.winsnap --dry-run

# Skip app installation (do it manually)
python restore.py my_snapshot.winsnap --skip apps

# Only restore wallpaper and taskbar
python restore.py my_snapshot.winsnap --only wallpaper taskbar
```

---

## Building a standalone `.exe`

The `.exe` build lets you run the WinSnap CLI on a target PC that has no
Python. (The desktop app currently runs from source via `python gui.py`
and isn't packaged by `build.py`.)

```
pip install pyinstaller
python build.py
```

The built executables land in `dist/`:
- `dist/winsnap-export.exe`
- `dist/winsnap-restore.exe`

**SmartScreen warning:** unsigned `.exe`s trigger a "Windows protected your PC"
dialog the first time you run them. Click **More info → Run anyway**. To remove
the warning permanently you'd need to code-sign the binaries (paid certificate),
which is on the roadmap.

---

## Snapshot format

`.winsnap` is a plain ZIP archive. Inside:

```
winsnap_<timestamp>/
├── snapshot.json          ← all captured metadata
├── wallpaper.jpg          ← (if wallpaper module ran)
├── winget_export.json     ← (if apps module ran)
├── fonts/                 ← (if fonts module ran) bundled .ttf/.otf
└── startup_shortcuts/     ← (if startup module ran) bundled .lnk files
```

`snapshot.json` includes a `snapshot_format_version` field. The restorer
refuses snapshots whose MAJOR version is newer than it understands, so
old restorers fail loudly instead of silently mis-applying new fields.

Current format version: **0.2.0**.

---

## Project structure

```
winsnap/
├── export.py               ← CLI entry point (source PC)
├── restore.py               ← CLI entry point (target PC)
├── gui.py                   ← PyQt6 desktop app (Export/Restore tabs)
├── build.py                  ← PyInstaller build script for the CLI tools
├── modules/
│   ├── manifest.py           ← single source of truth for module order
│   ├── report.py             ← shared matched/partial/failed/skipped report shape
│   ├── winutil.py            ← shared Windows helpers (e.g. Explorer restart)
│   ├── wallpaper.py
│   ├── apps.py
│   ├── checklist.py          ← interactive app picker (CLI TUI)
│   ├── mouse_display.py
│   ├── power.py
│   ├── taskbar.py
│   ├── explorer.py
│   ├── desktop_icons.py
│   ├── sound_scheme.py
│   ├── cursors.py
│   ├── fonts.py
│   ├── startup.py
│   ├── env_vars.py
│   └── region_lang.py
├── scripts/
│   └── roundtrip_check.py    ← manual export→restore→verify sanity script
├── tests/                    ← pytest + hypothesis suite (see Development)
└── README.md
```

Every settings module exposes the same contract:

```python
def export(snapshot_dir: Path) -> dict
def restore(snapshot: dict, snapshot_dir: Path) -> dict   # report: status/reason/items
def verify(snapshot: dict, snapshot_dir: Path) -> dict    # same report shape
```

The `status` field is one of `matched`, `partial`, `failed`, or `skipped`
(see `modules/report.py`) — both the CLI and the GUI classify outcomes from
this field, never from whether the call happened to raise an exception.

Adding a new category = drop a new file in `modules/` matching this contract
and add its name to `modules/manifest.py`'s `MODULE_NAMES` — `export.py`,
`restore.py`, and `gui.py` all derive their module lists from that one place.

---

## Safety notes

- **HKCU only.** Restore writes to `HKEY_CURRENT_USER`, never `HKLM`. This avoids
  needing admin and avoids breaking other users on shared machines.
  (Power plan export uses `powercfg` and does need admin to *capture* — but
  even there, the snapshot only contains your active plan's metadata.)
- **PATH is merged, not replaced.** Your saved PATH entries are appended to
  whatever the new PC already has, so you don't lose tools the new PC's
  installers added.
- **Startup entries are validated.** Run-key entries pointing at binaries that
  don't exist on the target are skipped with a warning, so we don't pollute
  the new PC with broken startup launches.
- **Archives are extracted safely.** Restore rejects any snapshot whose zip
  entries would write outside the extraction directory (zip-slip protection).
- **No network calls** other than `winget`. WinSnap doesn't phone home.
- **No third-party Python packages** at CLI runtime — stdlib only. The
  desktop app is the one place that needs `PyQt6`.

---

## Development

```
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

The test suite is mock-heavy — it doesn't touch the real registry — and
mixes plain `pytest` cases with `hypothesis` property-based tests. GUI-related
tests need `PyQt6` installed and run headlessly via `QT_QPA_PLATFORM=offscreen`.

This project follows a spec-driven workflow for non-trivial changes; see
`CLAUDE.md` for how specs, requirements, design, and task documents fit
together under `.claude/specs/`.

---

## Roadmap

- [x] Filtered apps list (no more 100+ noisy entries)
- [x] File Explorer preferences
- [x] Desktop icon visibility
- [x] System sound scheme
- [x] Mouse cursor scheme
- [x] User-installed fonts
- [x] Startup programs
- [x] Environment variables (with smart PATH merge)
- [x] Region & keyboard layouts
- [x] Snapshot format versioning
- [x] `--dry-run` for restore
- [x] PyInstaller `.exe` build (CLI)
- [x] GUI (PyQt6 desktop app for export/restore, with verify support)
- [ ] Package the GUI as a standalone `.exe`
- [ ] Browser bookmarks / default browser
- [ ] Pinned folders / Quick Access
- [ ] Code signing for the `.exe` build (no more SmartScreen warning)

---

## License

MIT — see [LICENSE](LICENSE).
