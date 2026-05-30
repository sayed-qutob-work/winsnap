<<<<<<< HEAD
# WinSnap 🪟

Transfer your Windows personality — settings, wallpaper, fonts, apps — to any new PC.
No bloatware. No viruses. Just your preferences.

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

The apps checklist now filters out OS components, updaters, runtimes, drivers,
helpers, MSI patches, and KB updates by default — so you see ~the same list
Windows shows in **Settings → Apps**, not 100+ noisy entries. Use `--show-all`
to opt out of filtering.

---

## Requirements

- Windows 10 or 11
- Python 3.11+ (only on the export PC; the standalone `.exe` build needs no Python)
- `winget` (built into Windows 11, available for Win10 via App Installer)

No third-party Python packages needed at runtime — stdlib only.

---

## Usage

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
```

### Transfer

Copy the `.winsnap` file via USB, cloud, or network share to your new PC.
Also copy `restore.py` and the `modules/` folder (or use the `.exe` build below).

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

The `.exe` build lets you run WinSnap on a target PC that has no Python.

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
├── export.py              ← run on old PC
├── restore.py             ← run on new PC
├── build.py               ← PyInstaller build script
├── modules/
│   ├── wallpaper.py
│   ├── apps.py
│   ├── checklist.py       ← interactive app picker (TUI)
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
├── tests/
│   └── smoke_apps.py      ← unit tests for the apps filter
└── README.md
```

Every settings module exposes the same two functions:

```python
def export(snapshot_dir: Path) -> dict
def restore(snapshot: dict, snapshot_dir: Path) -> None
```

Adding a new category = drop a new file in `modules/` matching this contract,
and add it to the lists in `export.py` and `restore.py`.

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
- **No network calls** other than `winget`. WinSnap doesn't phone home.
- **No third-party Python packages** at runtime — stdlib only.

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
- [x] PyInstaller `.exe` build
- [ ] GUI (PyQt6 checklist for app selection)
- [ ] Browser bookmarks / default browser
- [ ] Pinned folders / Quick Access
- [ ] Code signing for the `.exe` build (no more SmartScreen warning)

---

## License

MIT — see [LICENSE](LICENSE).
