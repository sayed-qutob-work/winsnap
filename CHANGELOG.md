# Changelog

All notable changes to WinSnap will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

Nothing yet.

---

## [0.1.0] — 2026-05-28

Initial public-ready release. The snapshot format is **0.2.0**.

### Added

- **Apps filtering** — registry scan now hides OS components, sub-components,
  MSI patches, updaters, runtimes, KB updates, and other noise. The checklist
  shows ~the same list as Windows Settings → Apps. Use `--show-all` to opt out.
- **File Explorer preferences** — hidden files, file extensions, launch-to,
  checkboxes, full path in title bar.
- **Desktop icon visibility** — This PC, User folder, Network, Recycle Bin,
  Control Panel.
- **System sound scheme** — active scheme name, per-event sound paths,
  system beep on/off.
- **Mouse cursor scheme** — scheme name and per-role cursor file paths.
- **User-installed fonts** — `.ttf` / `.otf` / `.ttc` files bundled inside
  the snapshot zip and re-registered on restore.
- **Startup programs** — `HKCU\...\Run` and `RunOnce` entries plus `.lnk`
  shortcuts from the Startup folder. Entries with missing binaries are
  skipped on restore.
- **Environment variables** — `HKCU\Environment`. PATH is **merged**, not
  replaced.
- **Region & keyboard layouts** — `Control Panel\International` and
  `Keyboard Layout\Preload` / `Substitutes`.
- **Snapshot format versioning** — `snapshot_format_version` field; restorer
  refuses snapshots with a newer MAJOR version.
- **`--dry-run`** flag for `restore.py` shows what would change without
  applying anything.
- **`--skip` / `--only`** flags on both `export.py` and `restore.py`.
- **PyInstaller build script** (`build.py`) for standalone `.exe` builds.
- **Smoke test** for the apps filter and name normalization (`tests/smoke_apps.py`).
- **MIT LICENSE**, **CONTRIBUTING.md**, **SECURITY.md**, GitHub issue
  templates, GitHub Actions CI workflow.

### Changed

- `export.py` and `restore.py` rewritten to support the module registry,
  `--skip`, `--only`, and a versioned manifest.
- Unicode-safe stdout: both entry points reconfigure stdout to UTF-8 so
  emoji and arrows in log messages don't crash on cp1252 consoles.

### Pre-v0.1.0 modules

The following modules existed before this release and continue to work:

- `wallpaper` — desktop wallpaper file
- `mouse_display` — mouse, keyboard, DPI scaling
- `power` — active power plan (admin needed to capture)
- `taskbar` — taskbar pins and theme
- `apps` — winget-managed apps with interactive checklist

---

[Unreleased]: https://github.com/<your-username>/winsnap/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/<your-username>/winsnap/releases/tag/v0.1.0
