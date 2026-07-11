# Changelog

All notable changes to WinSnap will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

Snapshot format bumped to **0.3.0** (older 0.2.0 snapshots still restore).

### Added

- **PyQt6 desktop app** (`gui.py`) — Export and Restore tabs backed by the same
  module selector and backend functions the CLI uses, so behavior is identical
  either way. Includes a live results view that groups each module's outcome as
  Matched / Partial / Failed / Skipped, and a collision check before export.
- **Verify after restore** — each module now exposes `verify(snapshot,
  snapshot_dir) -> report`, which re-checks applied settings against the
  snapshot and reports drift without re-running restore. Surfaced in the GUI as
  a "Verify after restore" option.
- **Richer captures (format 0.3.0)** — taskband blob + pins list, accent
  colour palette fields, wallpaper style/tile/sha256/image-format, bundled
  cursor and sound-scheme files, mouse-acceleration thresholds, and an
  `env_vars` `source_profile`/`vars` wrapper.

### Changed

- Module contract is now `export -> dict`, `restore -> report`, `verify ->
  report`; both CLI and GUI classify outcomes from the report's `status` field
  rather than from raised exceptions.
- Module order and the export/restore module *set* are now derived from a
  single `MODULE_NAMES` list in `modules/manifest.py`.
- Restore reporting reworked with a per-run results summary and clearer
  per-item detail.

### Fixed

- Backend round-trip hardening: multiple export→restore→verify fidelity fixes
  (wallpaper multi-monitor handling, mouse acceleration, cursor/sound file
  bundling, winget schema, env-var PATH rewrite) with regression tests.

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

[Unreleased]: https://github.com/sayed-qutob-work/winsnap/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/sayed-qutob-work/winsnap/releases/tag/v0.1.0
