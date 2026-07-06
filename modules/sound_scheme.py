"""
sound_scheme.py
Captures and restores Windows system-sound preferences.

What is captured:
  - Active sound scheme name (e.g. ".Default", ".None", custom)
  - Per-event .Current sound file paths (so a custom scheme moves with you)
  - System Beep on/off (Control Panel\\Sound\\Beep)

Bundling (Req 10):
  Any event sound whose .Current path points outside the Windows default
  media directory (%SystemRoot%\\Media) is copied into the snapshot under a
  "media/" subfolder at export time -- otherwise that path is meaningless on
  a target machine with a different username/profile layout. Export records
  this under a "bundled" map (present, possibly empty, for every 0.3.0
  snapshot); a source file that no longer exists at export time is recorded
  with "missing": true rather than silently dropped.

  On restore, a bundled file is copied to a stable per-user location
  (%LOCALAPPDATA%\\WinSnap\\media\\) and the *rewritten* target path (not the
  source-machine path) is written to the registry. A "missing": true entry
  is skipped with reason -- never a dangling path write. Snapshots that
  predate bundling (no "bundled" key at all) fall back to writing the
  captured path verbatim, exactly as before, with a skipped item noting the
  snapshot predates bundling.

  The active scheme name lives at the "(Default)" value of HKCU\\AppEvents\\Schemes.
"""

import os
import shutil
import winreg
from pathlib import Path

from modules.report import Report


_SCHEMES_PATH = r"AppEvents\Schemes"
_APPS_PATH    = r"AppEvents\Schemes\Apps"
_BEEP_PATH    = r"Control Panel\Sound"
_BUNDLE_SUBDIR = "media"


def _get_default(hive, path):
    try:
        key = winreg.OpenKey(hive, path)
        val, _ = winreg.QueryValueEx(key, "")
        winreg.CloseKey(key)
        return val
    except OSError:
        return None


def _set_default(hive, path, value):
    try:
        winreg.CreateKey(hive, path)
        key = winreg.OpenKey(hive, path, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, value)
        winreg.CloseKey(key)
        return True
    except OSError:
        return False


def _walk_event_sounds(hive, base_path):
    """
    Walk HKCU\\AppEvents\\Schemes\\Apps\\<App>\\<Event>\\.Current
    and return a flat dict { "<App>/<Event>": "<sound file path>" }.
    """
    sounds = {}
    try:
        apps_key = winreg.OpenKey(hive, base_path)
    except OSError:
        return sounds

    i = 0
    while True:
        try:
            app_name = winreg.EnumKey(apps_key, i)
        except OSError:
            break
        i += 1

        try:
            app_key = winreg.OpenKey(apps_key, app_name)
        except OSError:
            continue

        j = 0
        while True:
            try:
                event_name = winreg.EnumKey(app_key, j)
            except OSError:
                break
            j += 1

            current_path = f"{base_path}\\{app_name}\\{event_name}\\.Current"
            sound_file = _get_default(hive, current_path)
            if sound_file:
                sounds[f"{app_name}/{event_name}"] = sound_file

        winreg.CloseKey(app_key)

    winreg.CloseKey(apps_key)
    return sounds


def _default_media_dir() -> str:
    """The Windows default media directory, %SystemRoot%\\Media."""
    return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Media")


def _stable_media_dir() -> Path:
    """Stable per-user location bundled sound files are restored to."""
    return Path(os.environ.get("LOCALAPPDATA", "")) / "WinSnap" / "media"


def _is_outside_dir(path_str: str, directory: str) -> bool:
    """True if `path_str`'s (env-expanded) parent directory differs from
    `directory`, case-insensitively. Does not require the file to exist."""
    expanded = os.path.expandvars(path_str)
    parent = os.path.normcase(os.path.normpath(os.path.dirname(expanded)))
    target = os.path.normcase(os.path.normpath(directory))
    return parent != target


def _bundle_files(paths_by_key: dict, bundle_dir: Path, bundle_prefix: str) -> dict:
    """
    Copy each referenced file (if it still exists) into bundle_dir.

    Returns {key: {"filename": "<bundle_prefix>/<name>" | None,
                   "original_path": <original path as captured>,
                   "missing": bool}}.

    Files bundled from an identical (env-expanded) source path are reused
    rather than copied twice; a filename collision between two distinct
    source files is disambiguated with a numeric prefix.
    """
    bundled = {}
    dest_by_source: dict[str, str] = {}
    used_names: set[str] = set()

    for key, original_path in paths_by_key.items():
        if not original_path:
            continue
        expanded = os.path.expandvars(original_path)

        if expanded in dest_by_source:
            bundled[key] = {
                "filename": dest_by_source[expanded],
                "original_path": original_path,
                "missing": False,
            }
            continue

        src = Path(expanded)
        if not src.exists():
            bundled[key] = {
                "filename": None,
                "original_path": original_path,
                "missing": True,
            }
            continue

        name = src.name
        suffix = 1
        while name in used_names:
            name = f"{suffix}_{src.name}"
            suffix += 1
        used_names.add(name)

        bundle_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, bundle_dir / name)

        rel_filename = f"{bundle_prefix}/{name}"
        dest_by_source[expanded] = rel_filename
        bundled[key] = {
            "filename": rel_filename,
            "original_path": original_path,
            "missing": False,
        }

    return bundled


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export(snapshot_dir: Path) -> dict:
    scheme = _get_default(winreg.HKEY_CURRENT_USER, _SCHEMES_PATH)
    sounds = _walk_event_sounds(winreg.HKEY_CURRENT_USER, _APPS_PATH)
    beep = None
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _BEEP_PATH)
        beep, _ = winreg.QueryValueEx(key, "Beep")
        winreg.CloseKey(key)
    except OSError:
        pass

    default_dir = _default_media_dir()
    to_bundle = {key: path for key, path in sounds.items()
                 if path.lower().endswith(".wav") and _is_outside_dir(path, default_dir)}

    bundle_dir = snapshot_dir / _BUNDLE_SUBDIR
    bundled = _bundle_files(to_bundle, bundle_dir, _BUNDLE_SUBDIR)

    print(f"[sound_scheme] Active scheme: {scheme!r}, "
          f"{len(sounds)} event sounds ({len(to_bundle)} bundled), beep={beep}")
    return {
        "scheme": scheme,
        "event_sounds": sounds,
        "beep": beep,
        "bundled": bundled,
    }


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

def restore(snapshot: dict, snapshot_dir: Path) -> dict:
    report = Report("sound_scheme", "restore")

    sounds = snapshot.get("event_sounds", {}) or {}
    scheme = snapshot.get("scheme")
    beep   = snapshot.get("beep")

    bundled = snapshot.get("bundled")
    if bundled is None:
        report.add_skipped("bundled files",
                            detail="bundled files: snapshot predates bundling")
        bundled = {}

    target_dir = _stable_media_dir()

    # 1. Apply per-event sounds first (matches the original ordering: sounds
    #    before the active scheme name, so the scheme switch doesn't clobber
    #    the per-event overrides we just wrote).
    for key, sound_file in sounds.items():
        try:
            app, event = key.split("/", 1)
        except ValueError:
            report.add_failed(key, detail="malformed event key")
            continue

        entry = bundled.get(key)
        write_path = sound_file
        detail = "verbatim path"

        if entry is not None:
            if entry.get("missing"):
                report.add_skipped(
                    key, detail="source sound file was missing at export time")
                continue

            filename = entry.get("filename")
            src = snapshot_dir / filename if filename else None
            if not src or not src.exists():
                report.add_skipped(
                    key, detail=f"bundled file not found in snapshot: {filename}")
                continue

            try:
                target_dir.mkdir(parents=True, exist_ok=True)
                dest = target_dir / src.name
                shutil.copy2(src, dest)
            except OSError as e:
                report.add_failed(key, detail=f"could not place bundled file: {e}")
                continue

            write_path = str(dest)
            detail = "restored from bundled file"

        path = f"{_APPS_PATH}\\{app}\\{event}\\.Current"
        if _set_default(winreg.HKEY_CURRENT_USER, path, write_path):
            report.add_matched(key, detail=detail, actual=write_path)
        else:
            report.add_failed(key, detail="registry write failed")

    # 2. Set the active scheme name
    if scheme:
        if _set_default(winreg.HKEY_CURRENT_USER, _SCHEMES_PATH, scheme):
            report.add_matched("scheme", detail=str(scheme))
        else:
            report.add_failed("scheme", detail="registry write failed")

    # 3. System beep
    if beep is not None:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _BEEP_PATH, 0,
                                 winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, "Beep", 0, winreg.REG_SZ, beep)
            winreg.CloseKey(key)
            report.add_matched("beep", detail=str(beep))
        except OSError as e:
            report.add_failed("beep", detail=f"registry write failed: {e}")

    print(f"[sound_scheme] Restored scheme {scheme!r}.")
    return report.finalize()


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

def verify(data: dict, snapshot_dir: Path) -> dict:
    """
    Read-only: confirms the active scheme name matches and that every
    restored event-sound registry path (after env-variable expansion) points
    at a file that exists on the target. An event whose source file was
    recorded "missing" at export time (and therefore was never restored) is
    reported skipped rather than failed; a snapshot that predates bundling
    is likewise reported skipped for that aspect only (Req 10.5, 14.4).
    """
    report = Report("sound_scheme", "verify")

    if not data:
        return report.skip_all("no sound scheme data in snapshot")

    expected_scheme = data.get("scheme")
    if expected_scheme:
        actual_scheme = _get_default(winreg.HKEY_CURRENT_USER, _SCHEMES_PATH)
        if actual_scheme == expected_scheme:
            report.add_matched("scheme", expected=expected_scheme, actual=actual_scheme)
        else:
            report.add_failed("scheme", detail="scheme mismatch",
                               expected=expected_scheme, actual=actual_scheme)
    else:
        report.add_skipped("scheme", detail="snapshot has no active scheme recorded")

    if "bundled" not in data:
        report.add_skipped("bundled files",
                            detail="bundled files: snapshot predates bundling")
    bundled = data.get("bundled") or {}

    sounds = data.get("event_sounds") or {}
    if not sounds:
        report.add_skipped("event sounds", detail="no event sounds in snapshot")

    for key, expected_path in sounds.items():
        entry = bundled.get(key)
        if entry and entry.get("missing"):
            report.add_skipped(
                key, detail="source file was missing at export time; not restored")
            continue

        try:
            app, event = key.split("/", 1)
        except ValueError:
            report.add_skipped(key, detail="malformed event key")
            continue

        reg_path = f"{_APPS_PATH}\\{app}\\{event}\\.Current"
        actual_path = _get_default(winreg.HKEY_CURRENT_USER, reg_path)
        if actual_path is None:
            report.add_failed(key, detail="registry value missing on target",
                               expected=expected_path)
            continue

        expanded = os.path.expandvars(actual_path)
        if Path(expanded).exists():
            report.add_matched(key, expected=expected_path, actual=actual_path)
        else:
            report.add_failed(key, detail=f"target file does not exist: {expanded}",
                               expected=expected_path, actual=actual_path)

    return report.finalize()
