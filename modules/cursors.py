"""
cursors.py
Captures and restores the mouse cursor scheme.

Registry:
    HKCU\\Control Panel\\Cursors

Values:
  - "" (default) ............ scheme name
  - Scheme Source (DWORD) ... 0=Windows default, 1=user, 2=system
  - Arrow, Hand, IBeam, ... . path to .cur/.ani file for each cursor role

Bundling (Req 10):
  Any cursor role whose registry value points outside the Windows default
  cursor directory (%SystemRoot%\\Cursors) is copied into the snapshot under
  a "cursors/" subfolder at export time -- otherwise that path is meaningless
  on a target machine with a different username/profile layout. Export
  records this under a "bundled" map (present, possibly empty, for every
  0.3.0 snapshot); a source file that no longer exists at export time is
  recorded with "missing": true rather than silently dropped.

  On restore, a bundled file is copied to a stable per-user location
  (%LOCALAPPDATA%\\WinSnap\\cursors\\) and the *rewritten* target path (not
  the source-machine path) is written to the registry. A "missing": true
  entry is skipped with reason -- never a dangling path write. Snapshots
  that predate bundling (no "bundled" key at all) fall back to writing the
  captured path verbatim, exactly as before, with a skipped item noting the
  snapshot predates bundling. The "cursors" map itself is always kept
  verbatim so 0.2.0 readers (and this module's own verbatim fallback) still
  work unchanged.
"""

import ctypes
import os
import shutil
import winreg
from pathlib import Path

from modules.report import Report


_PATH = r"Control Panel\Cursors"
_BUNDLE_SUBDIR = "cursors"

# Standard cursor role names exposed by Windows
_CURSOR_ROLES = [
    "Arrow", "AppStarting", "Crosshair", "Hand", "Help",
    "IBeam", "No", "NWPen", "Person", "Pin",
    "SizeAll", "SizeNESW", "SizeNS", "SizeNWSE", "SizeWE",
    "UpArrow", "Wait",
]


def _read(name: str):
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PATH)
        val, _ = winreg.QueryValueEx(key, name)
        winreg.CloseKey(key)
        return val
    except OSError:
        return None


def _write(name: str, value, reg_type=winreg.REG_EXPAND_SZ):
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PATH, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, name, 0, reg_type, value)
        winreg.CloseKey(key)
        return True
    except OSError as e:
        print(f"[cursors] Could not write {name}: {e}")
        return False


def _default_cursors_dir() -> str:
    """The Windows default cursor directory, %SystemRoot%\\Cursors."""
    return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Cursors")


def _stable_cursors_dir() -> Path:
    """Stable per-user location bundled cursor files are restored to."""
    return Path(os.environ.get("LOCALAPPDATA", "")) / "WinSnap" / "cursors"


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
    data = {
        "scheme":         _read(""),                # the (Default) value
        "scheme_source":  _read("Scheme Source"),
        "cursors":        {},
    }

    default_dir = _default_cursors_dir()
    to_bundle = {}
    for role in _CURSOR_ROLES:
        val = _read(role)
        if val:
            data["cursors"][role] = val
            if _is_outside_dir(val, default_dir):
                to_bundle[role] = val

    bundle_dir = snapshot_dir / _BUNDLE_SUBDIR
    data["bundled"] = _bundle_files(to_bundle, bundle_dir, _BUNDLE_SUBDIR)

    print(f"[cursors] Captured scheme {data['scheme']!r} "
          f"({len(data['cursors'])} cursor paths, {len(to_bundle)} bundled).")
    return data


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

def restore(snapshot: dict, snapshot_dir: Path) -> dict:
    report = Report("cursors", "restore")

    if snapshot.get("scheme") is not None:
        if _write("", snapshot["scheme"], winreg.REG_SZ):
            report.add_matched("scheme", detail=str(snapshot["scheme"]))
        else:
            report.add_failed("scheme", detail="registry write failed")

    if snapshot.get("scheme_source") is not None:
        if _write("Scheme Source", int(snapshot["scheme_source"]), winreg.REG_DWORD):
            report.add_matched("Scheme Source")
        else:
            report.add_failed("Scheme Source", detail="registry write failed")

    bundled = snapshot.get("bundled")
    if bundled is None:
        report.add_skipped("bundled files",
                            detail="bundled files: snapshot predates bundling")
        bundled = {}

    target_dir = _stable_cursors_dir()
    cursors = snapshot.get("cursors") or {}

    for role, path in cursors.items():
        entry = bundled.get(role)

        if entry is None:
            # 0.2.0 snapshot, or this role's file was inside the default
            # Windows cursor directory at export time: write verbatim.
            if _write(role, path, winreg.REG_EXPAND_SZ):
                report.add_matched(role, detail="verbatim path", actual=path)
            else:
                report.add_failed(role, detail="registry write failed")
            continue

        if entry.get("missing"):
            report.add_skipped(
                role, detail="source cursor file was missing at export time")
            continue

        filename = entry.get("filename")
        src = snapshot_dir / filename if filename else None
        if not src or not src.exists():
            report.add_skipped(
                role, detail=f"bundled file not found in snapshot: {filename}")
            continue

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            dest = target_dir / src.name
            shutil.copy2(src, dest)
        except OSError as e:
            report.add_failed(role, detail=f"could not place bundled file: {e}")
            continue

        new_path = str(dest)
        if _write(role, new_path, winreg.REG_EXPAND_SZ):
            report.add_matched(role, detail="restored from bundled file", actual=new_path)
        else:
            report.add_failed(role, detail="registry write failed")

    # Tell Windows to apply the cursor scheme immediately
    # SPI_SETCURSORS = 0x0057
    SPI_SETCURSORS = 0x0057
    SPIF_SENDCHANGE = 0x02
    try:
        ctypes.windll.user32.SystemParametersInfoW(SPI_SETCURSORS, 0, None, SPIF_SENDCHANGE)
    except OSError as e:
        report.add_failed("live apply", detail=f"SystemParametersInfoW failed: {e}")

    print("[cursors] Cursor scheme restored.")
    return report.finalize()


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

def verify(data: dict, snapshot_dir: Path) -> dict:
    """
    Read-only: confirms every restored cursor registry path (after
    env-variable expansion) points at a file that exists on the target, and
    that the scheme name/source values match. A role whose source file was
    recorded "missing" at export time (and therefore was never restored) is
    reported skipped rather than failed; a snapshot that predates bundling
    is likewise reported skipped for that aspect only (Req 10.5, 14.4).
    """
    report = Report("cursors", "verify")

    if not data:
        return report.skip_all("no cursor data in snapshot")

    expected_scheme = data.get("scheme")
    if expected_scheme is not None:
        actual_scheme = _read("")
        if actual_scheme == expected_scheme:
            report.add_matched("scheme", expected=expected_scheme, actual=actual_scheme)
        else:
            report.add_failed("scheme", detail="scheme mismatch",
                               expected=expected_scheme, actual=actual_scheme)
    else:
        report.add_skipped("scheme", detail="snapshot has no scheme recorded")

    expected_source = data.get("scheme_source")
    if expected_source is not None:
        actual_source = _read("Scheme Source")
        try:
            source_matches = actual_source is not None and int(actual_source) == int(expected_source)
        except (TypeError, ValueError):
            source_matches = actual_source == expected_source
        if source_matches:
            report.add_matched("scheme_source", expected=expected_source, actual=actual_source)
        else:
            report.add_failed("scheme_source", detail="scheme source mismatch",
                               expected=expected_source, actual=actual_source)
    else:
        report.add_skipped("scheme_source", detail="snapshot has no scheme source recorded")

    if "bundled" not in data:
        report.add_skipped("bundled files",
                            detail="bundled files: snapshot predates bundling")
    bundled = data.get("bundled") or {}

    cursors = data.get("cursors") or {}
    if not cursors:
        report.add_skipped("cursor paths", detail="no cursor paths in snapshot")

    for role in cursors:
        entry = bundled.get(role)
        if entry and entry.get("missing"):
            report.add_skipped(
                role, detail="source file was missing at export time; not restored")
            continue

        live_path = _read(role)
        if live_path is None:
            report.add_failed(role, detail="registry value missing on target")
            continue

        expanded = os.path.expandvars(live_path)
        if Path(expanded).exists():
            report.add_matched(role, actual=live_path)
        else:
            report.add_failed(
                role, detail=f"target file does not exist: {expanded}", actual=live_path)

    return report.finalize()
