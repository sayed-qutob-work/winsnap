"""
taskbar.py
Captures and restores taskbar pins and Windows theme/accent color settings.

Note on taskbar pins:
  Windows 11 stores taskbar pins in a binary database at:
    %APPDATA%/Microsoft/Internet Explorer/Quick Launch/User Pinned/TaskBar/
  The most reliable method is to copy that entire folder.
  Windows 10 uses a similar location but also references the registry.

  We copy the folder on export and restore it on the new machine. Copying
  the .lnk files alone is not sufficient, though: Windows itself decides
  what appears pinned from the `Favorites`/`FavoritesResolve` REG_BINARY
  blobs under `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\
  Taskband` (Req 1) -- without restoring those, Explorer rebuilds a default
  taskbar layout on the target machine even though the .lnk files are
  present on disk. Both are captured/restored here.

Accent color (Req 9):
  In addition to the DWM `AccentColor`/`ColorizationColor` values, the full
  accent state includes `AccentPalette` (REG_BINARY), `AccentColorMenu`, and
  `StartColorMenu` under `HKCU\\...\\Explorer\\Accent`. These are captured
  and restored together with the rest of the theme settings so the accent
  does not partially revert to defaults after migration.

Honest caveat (Req 1.6/D6): Explorer may rewrite the Taskband blobs on its
  own during the restart that follows a restore -- most commonly when one of
  the pinned `.lnk` targets can no longer be resolved (e.g. the referenced
  app was not (yet) reinstalled). When that happens, `verify()` will
  correctly report the taskband/pins portion as failed/partial rather than
  matched. That is the honest outcome this module aims for; it is not
  something this module tries to paper over by skipping the comparison.

Explorer restart (Req 1.3, 2.2, D2):
  `restore()` restarts Explorer itself only when `INLINE_EXPLORER_RESTART`
  is True (the legacy/default behavior, preserved for direct callers such as
  the GUI, which calls `taskbar.restore()` directly and relies on the inline
  restart). `restore.py`'s orchestrated run sets this flag to False so it can
  perform a single Explorer restart after *all* modules have run (and before
  verification); in that mode this module instead marks
  `explorer_restart_required` on its report.
"""

import base64
import os
import shutil
import winreg
from pathlib import Path

from modules.report import Report
from modules import winutil


# Taskbar pins folder (works for both Win10 and Win11)
TASKBAR_PINS_DIR = Path(os.environ.get("APPDATA", "")) / \
    "Microsoft" / "Internet Explorer" / "Quick Launch" / "User Pinned" / "TaskBar"

# Registry paths used by this module.
_PERSONALIZE_PATH = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize"
_DWM_PATH = r"SOFTWARE\Microsoft\Windows\DWM"
TASKBAND_KEY_PATH = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Taskband"
ACCENT_KEY_PATH = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Accent"

# Legacy/GUI default: taskbar.restore() restarts Explorer itself, exactly as
# it always has. restore.py's orchestrated run flips this to False (in a
# `finally`-guarded block) so it can perform exactly one restart after every
# module has run instead of one restart per Explorer-affecting module
# (Req 1.3, 2.2, Design D2).
INLINE_EXPLORER_RESTART = True


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export(snapshot_dir: Path) -> dict:
    data = {}

    # --- Taskbar pins ---
    pins_backup = snapshot_dir / "taskbar_pins"
    if TASKBAR_PINS_DIR.exists():
        shutil.copytree(TASKBAR_PINS_DIR, pins_backup, dirs_exist_ok=True)
        pins = sorted(p.name for p in pins_backup.glob("*.lnk"))
        print(f"[taskbar] Captured {len(pins)} pinned shortcuts.")
        data["pins_backup"] = "taskbar_pins"
        data["pins"] = pins
    else:
        print("[taskbar] Taskbar pins folder not found. Skipping pins.")
        data["pins_backup"] = None
        data["pins"] = []

    # --- Taskband Favorites/FavoritesResolve blob (Req 1.1) ---
    data["taskband"] = _read_taskband_blob()
    if data["taskband"] is None:
        print("[taskbar] Taskband Favorites/FavoritesResolve not found; "
              "pin layout will not be fully restorable from this snapshot.")
    else:
        print("[taskbar] Captured Taskband Favorites/FavoritesResolve blob.")

    # --- Theme / accent color ---
    data["theme"] = _read_theme_settings()
    print("[taskbar] Captured theme settings.")

    return data


def _read_taskband_blob() -> dict | None:
    """Reads the `Favorites`/`FavoritesResolve` REG_BINARY values from the
    Taskband key and returns them base64-encoded as
    {"favorites": "<b64>", "favorites_resolve": "<b64>"}.

    Returns None if the key or either value is absent -- shared by export()
    (to populate the snapshot) and verify() (to compare live state).
    """
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, TASKBAND_KEY_PATH)
    except OSError:
        return None
    try:
        favorites, _ = winreg.QueryValueEx(key, "Favorites")
        favorites_resolve, _ = winreg.QueryValueEx(key, "FavoritesResolve")
    except OSError:
        return None
    finally:
        winreg.CloseKey(key)

    return {
        "favorites": base64.b64encode(favorites).decode("ascii"),
        "favorites_resolve": base64.b64encode(favorites_resolve).decode("ascii"),
    }


def _read_theme_settings() -> dict:
    theme = {}
    reg_map = {
        # (hive, path, value_name) : output_key
        (winreg.HKEY_CURRENT_USER,
         _PERSONALIZE_PATH,
         "AppsUseLightTheme"): "apps_light_theme",

        (winreg.HKEY_CURRENT_USER,
         _PERSONALIZE_PATH,
         "SystemUsesLightTheme"): "system_light_theme",

        (winreg.HKEY_CURRENT_USER,
         _DWM_PATH,
         "AccentColor"): "accent_color",

        (winreg.HKEY_CURRENT_USER,
         _DWM_PATH,
         "ColorizationColor"): "colorization_color",

        (winreg.HKEY_CURRENT_USER,
         _PERSONALIZE_PATH,
         "ColorPrevalence"): "color_on_taskbar",

        (winreg.HKEY_CURRENT_USER,
         _PERSONALIZE_PATH,
         "EnableTransparency"): "transparency",

        # --- Accent color fidelity (Req 9.1) ---
        (winreg.HKEY_CURRENT_USER,
         ACCENT_KEY_PATH,
         "AccentColorMenu"): "accent_color_menu",

        (winreg.HKEY_CURRENT_USER,
         ACCENT_KEY_PATH,
         "StartColorMenu"): "start_color_menu",
    }

    for (hive, path, name), key in reg_map.items():
        try:
            reg_key = winreg.OpenKey(hive, path)
            val, _ = winreg.QueryValueEx(reg_key, name)
            winreg.CloseKey(reg_key)
            theme[key] = val
        except OSError:
            theme[key] = None

    # AccentPalette is REG_BINARY -- handled separately (base64), since the
    # generic loop above assumes JSON-serializable (int/str) values.
    try:
        reg_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, ACCENT_KEY_PATH)
        val, _ = winreg.QueryValueEx(reg_key, "AccentPalette")
        winreg.CloseKey(reg_key)
        theme["accent_palette"] = base64.b64encode(val).decode("ascii")
    except OSError:
        theme["accent_palette"] = None

    return theme


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

def restore(snapshot: dict, snapshot_dir: Path) -> dict:
    """Restores taskbar pins, the Taskband blob, and theme/accent settings.

    Returns a finalized Report (Req 7.1). See the module docstring for the
    honest caveat that Explorer may rewrite the Taskband blob on restart --
    a `verify()` partial/failed after that happens is the intended, honest
    outcome, not a defect in this restore step.
    """
    report = Report("taskbar", "restore")

    # --- Taskbar pins: copy .lnk files first (Req 1.2, D6 ordering) ---
    pins_backup_name = snapshot.get("pins_backup")
    if pins_backup_name:
        pins_src = snapshot_dir / pins_backup_name
        if pins_src.exists():
            TASKBAR_PINS_DIR.mkdir(parents=True, exist_ok=True)
            _copy_pins_tolerant(pins_src, TASKBAR_PINS_DIR, report)
        else:
            report.add_failed(
                "pins", detail=f"pins backup folder not found in snapshot: {pins_src}")
    else:
        report.add_skipped("pins", detail="no pins backup recorded in snapshot")

    # --- Taskband blob (Req 1.2, 1.4, 1.5) ---
    taskband = snapshot.get("taskband")
    if taskband:
        _write_taskband_blob(taskband, report)
    else:
        # 0.2.0 snapshots never captured this -- restore .lnk files only and
        # say so explicitly rather than silently reporting success (Req 1.5).
        report.add_skipped(
            "taskband", detail="pin state: snapshot predates Taskband capture")

    # --- Theme (incl. accent, Req 9.2/9.3) ---
    theme = snapshot.get("theme") or {}
    _write_theme_settings(theme, report)

    # --- Explorer restart (Req 1.3, 2.2, D2) ---
    if INLINE_EXPLORER_RESTART:
        winutil.restart_explorer()
    else:
        report.require_explorer_restart()

    return report.finalize()


def _copy_pins_tolerant(src_dir: Path, dst_dir: Path, report: Report) -> None:
    """Copy only .lnk shortcuts from src_dir to dst_dir, skipping
    non-essential files (desktop.ini, etc.) and tolerating per-file
    PermissionError/OSError without aborting. Each .lnk copy attempt becomes
    a report item (Req 7.4) instead of only a printed warning."""
    copied = 0
    skipped_non_essential = []

    for item in src_dir.iterdir():
        # Only restore .lnk shortcut files; skip desktop.ini and other
        # non-essential hidden/system files explicitly.
        if item.suffix.lower() != ".lnk":
            skipped_non_essential.append(item.name)
            continue

        try:
            shutil.copy2(item, dst_dir / item.name)
            report.add_matched(item.name, detail="pin shortcut copied")
            copied += 1
        except (PermissionError, OSError) as e:
            print(f"[taskbar] Warning: could not copy {item.name}: {e}")
            report.add_failed(item.name, detail=f"could not copy pin shortcut: {e}")

    if skipped_non_essential:
        print(f"[taskbar] Skipped {len(skipped_non_essential)} non-essential "
              f"file(s): {skipped_non_essential}")
    print(f"[taskbar] Restored {copied} pinned shortcut(s) to {dst_dir}")


def _write_taskband_blob(taskband: dict, report: Report) -> None:
    """Writes the Favorites/FavoritesResolve REG_BINARY values back to the
    Taskband key. A write failure (bad base64, registry error) records a
    single failed item so the category becomes partial/failed, never a
    silent success (Req 1.4)."""
    favorites_b64 = taskband.get("favorites")
    favorites_resolve_b64 = taskband.get("favorites_resolve")

    if not favorites_b64 or not favorites_resolve_b64:
        report.add_failed(
            "taskband", detail="taskband data in snapshot is incomplete")
        return

    try:
        favorites = base64.b64decode(favorites_b64)
        favorites_resolve = base64.b64decode(favorites_resolve_b64)
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, TASKBAND_KEY_PATH)
        try:
            winreg.SetValueEx(key, "Favorites", 0, winreg.REG_BINARY, favorites)
            winreg.SetValueEx(key, "FavoritesResolve", 0, winreg.REG_BINARY,
                               favorites_resolve)
        finally:
            winreg.CloseKey(key)
        report.add_matched(
            "taskband", detail="Favorites/FavoritesResolve written")
    except (OSError, ValueError) as e:
        report.add_failed("taskband", detail=f"could not write Taskband blob: {e}")


def _write_theme_settings(theme: dict, report: Report) -> None:
    """Writes theme/accent registry values, recording a matched/failed item
    per value (values absent from the snapshot are left untouched -- not
    every theme value is guaranteed to have been captured). The three
    Accent-palette values (Req 9) are additive to 0.2.0 snapshots and are
    reported as a single skipped item when the snapshot predates their
    capture (Req 9.3)."""
    legacy_writes = [
        ("apps_light_theme", _PERSONALIZE_PATH, "AppsUseLightTheme"),
        ("system_light_theme", _PERSONALIZE_PATH, "SystemUsesLightTheme"),
        ("color_on_taskbar", _PERSONALIZE_PATH, "ColorPrevalence"),
        ("transparency", _PERSONALIZE_PATH, "EnableTransparency"),
        ("accent_color", _DWM_PATH, "AccentColor"),
        ("colorization_color", _DWM_PATH, "ColorizationColor"),
    ]

    for item_name, path, reg_name in legacy_writes:
        value = theme.get(item_name)
        if value is None:
            continue
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, reg_name, 0, winreg.REG_DWORD, int(value))
            winreg.CloseKey(key)
            report.add_matched(item_name, detail=f"{reg_name} written")
        except OSError as e:
            report.add_failed(item_name, detail=f"could not write {reg_name}: {e}")

    # --- Accent palette trio (Req 9.2, 9.3) ---
    if "accent_palette" not in theme:
        report.add_skipped(
            "accent_palette", detail="accent palette: snapshot predates capture")
        return

    accent_palette_b64 = theme.get("accent_palette")
    accent_color_menu = theme.get("accent_color_menu")
    start_color_menu = theme.get("start_color_menu")

    if accent_palette_b64 is None and accent_color_menu is None and start_color_menu is None:
        # 0.3.0 snapshot, but the source machine itself had nothing to
        # capture (e.g. Accent key absent there too) -- honest skip, not a
        # false match.
        report.add_skipped(
            "accent_palette", detail="accent palette not captured on source machine")
        return

    try:
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, ACCENT_KEY_PATH)
        try:
            if accent_palette_b64 is not None:
                winreg.SetValueEx(key, "AccentPalette", 0, winreg.REG_BINARY,
                                   base64.b64decode(accent_palette_b64))
            if accent_color_menu is not None:
                winreg.SetValueEx(key, "AccentColorMenu", 0, winreg.REG_DWORD,
                                   int(accent_color_menu))
            if start_color_menu is not None:
                winreg.SetValueEx(key, "StartColorMenu", 0, winreg.REG_DWORD,
                                   int(start_color_menu))
        finally:
            winreg.CloseKey(key)
        report.add_matched(
            "accent_palette",
            detail="AccentPalette/AccentColorMenu/StartColorMenu written")
    except (OSError, ValueError) as e:
        report.add_failed("accent_palette", detail=f"could not write accent palette: {e}")


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

def verify(data: dict, snapshot_dir: Path) -> dict:
    """Read-only: re-reads the Taskband blob, the pinned .lnk filename set,
    and the theme/accent registry values, comparing each against the
    snapshot. 0.2.0 snapshots (missing "pins"/"taskband"/"accent_palette")
    report those aspects as skipped, never as failed (Req 1.6, 9.4, 14.4).

    As noted in the module docstring, Explorer can rewrite the Taskband blob
    on restart; a failed/partial result here after a real restart is the
    honest reflection of that, not a bug in verify() itself.
    """
    report = Report("taskbar", "verify")

    # --- Pinned .lnk filename set (Req 1.6) ---
    expected_pins = data.get("pins")
    if expected_pins is None:
        report.add_skipped(
            "pins", detail="pin filenames: snapshot predates pin-name capture")
    else:
        actual_pins = (sorted(p.name for p in TASKBAR_PINS_DIR.glob("*.lnk"))
                        if TASKBAR_PINS_DIR.exists() else [])
        expected_sorted = sorted(expected_pins)
        if actual_pins == expected_sorted:
            report.add_matched("pins", expected=expected_sorted, actual=actual_pins)
        else:
            report.add_failed(
                "pins", detail="pinned .lnk filenames differ from snapshot",
                expected=expected_sorted, actual=actual_pins)

    # --- Taskband blob, byte-for-byte (Req 1.6) ---
    taskband = data.get("taskband")
    if not taskband:
        report.add_skipped(
            "taskband", detail="pin state: snapshot predates Taskband capture")
    else:
        live = _read_taskband_blob()
        if live is None:
            report.add_failed(
                "taskband",
                detail="Taskband Favorites/FavoritesResolve not present on target",
                expected=taskband, actual=None)
        elif live == taskband:
            report.add_matched(
                "taskband", detail="Favorites/FavoritesResolve match byte-for-byte")
        else:
            report.add_failed(
                "taskband", detail="Taskband Favorites/FavoritesResolve differ from snapshot",
                expected=taskband, actual=live)

    # --- Theme + accent (Req 9.4) ---
    _verify_theme_settings(data.get("theme") or {}, report)

    return report.finalize()


def _verify_theme_settings(theme: dict, report: Report) -> None:
    """Re-reads legacy theme/DWM values plus the Req 9 accent trio and
    compares each against the snapshot's expected value."""
    legacy_checks = [
        ("apps_light_theme", _PERSONALIZE_PATH, "AppsUseLightTheme"),
        ("system_light_theme", _PERSONALIZE_PATH, "SystemUsesLightTheme"),
        ("color_on_taskbar", _PERSONALIZE_PATH, "ColorPrevalence"),
        ("transparency", _PERSONALIZE_PATH, "EnableTransparency"),
        ("accent_color", _DWM_PATH, "AccentColor"),
        ("colorization_color", _DWM_PATH, "ColorizationColor"),
    ]

    for item_name, path, reg_name in legacy_checks:
        expected = theme.get(item_name)
        if expected is None:
            report.add_skipped(item_name, detail=f"{reg_name}: not present in snapshot")
            continue
        actual = _read_reg_value(winreg.HKEY_CURRENT_USER, path, reg_name)
        if actual == expected:
            report.add_matched(item_name, expected=expected, actual=actual)
        else:
            report.add_failed(
                item_name, detail=f"{reg_name} mismatch", expected=expected, actual=actual)

    # --- Accent palette trio (Req 9.4: AccentPalette byte-for-byte) ---
    if "accent_palette" not in theme:
        report.add_skipped(
            "accent_palette", detail="accent palette: snapshot predates capture")
        return

    expected_palette = theme.get("accent_palette")
    expected_menu = theme.get("accent_color_menu")
    expected_start = theme.get("start_color_menu")

    if expected_palette is None and expected_menu is None and expected_start is None:
        report.add_skipped(
            "accent_palette", detail="accent palette not captured on source machine")
        return

    live_palette_raw = _read_reg_value(winreg.HKEY_CURRENT_USER, ACCENT_KEY_PATH, "AccentPalette")
    live_palette = (base64.b64encode(live_palette_raw).decode("ascii")
                    if isinstance(live_palette_raw, (bytes, bytearray)) else None)
    live_menu = _read_reg_value(winreg.HKEY_CURRENT_USER, ACCENT_KEY_PATH, "AccentColorMenu")
    live_start = _read_reg_value(winreg.HKEY_CURRENT_USER, ACCENT_KEY_PATH, "StartColorMenu")

    mismatches = []
    if expected_palette is not None and live_palette != expected_palette:
        mismatches.append("AccentPalette")
    if expected_menu is not None and live_menu != expected_menu:
        mismatches.append("AccentColorMenu")
    if expected_start is not None and live_start != expected_start:
        mismatches.append("StartColorMenu")

    expected_blob = {
        "accent_palette": expected_palette,
        "accent_color_menu": expected_menu,
        "start_color_menu": expected_start,
    }
    actual_blob = {
        "accent_palette": live_palette,
        "accent_color_menu": live_menu,
        "start_color_menu": live_start,
    }

    if mismatches:
        report.add_failed(
            "accent_palette", detail=f"mismatch: {', '.join(mismatches)}",
            expected=expected_blob, actual=actual_blob)
    else:
        report.add_matched(
            "accent_palette",
            detail="AccentPalette/AccentColorMenu/StartColorMenu match",
            expected=expected_blob, actual=actual_blob)


def _read_reg_value(hive, path: str, name: str):
    """Reads a single registry value, returning None if the key/value is
    absent. Local helper (kept module-local, matching this module's existing
    direct-winreg style) so verify() can re-read one value at a time."""
    try:
        key = winreg.OpenKey(hive, path)
        val, _ = winreg.QueryValueEx(key, name)
        winreg.CloseKey(key)
        return val
    except OSError:
        return None
