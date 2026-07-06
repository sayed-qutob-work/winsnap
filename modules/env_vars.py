"""
env_vars.py
Captures and restores user environment variables (HKCU\\Environment).

Why HKCU only:
  System variables live under HKLM and altering them risks breaking the OS
  (e.g. clobbering SystemRoot or PATH for services). They also need admin.
  WinSnap stays out of HKLM by design.

Safety (Req 4):
  A handful of variables are machine-specific -- TEMP/TMP, the OneDrive
  family, and the profile-identity variables (USERPROFILE, HOMEPATH,
  HOMEDRIVE, APPDATA, LOCALAPPDATA, USERNAME) -- and must never be copied
  verbatim from a source machine onto a target with a different username or
  profile layout. Those are skipped outright (RESTORE_DENYLIST /
  _is_denylisted). Every other captured value that contains the source
  profile path is rewritten to the target's %USERPROFILE% before writing
  (rewrite_profile_paths), so a restore never leaves TEMP-adjacent paths
  pointing at a nonexistent user directory.

PATH handling:
  PATH on the new PC is *merged*, not replaced. We rewrite and validate the
  saved (incoming) entries -- dropping any whose target directory doesn't
  exist -- then append what's left that isn't already present, preserving
  the order of the existing entries. Existing target entries are never
  rewritten or dropped. This avoids losing tools the new PC's installer
  added (e.g. Python, Git).

Snapshot shape (0.3.0):
  export() wraps the captured variables as
  {"source_profile": "<C:\\Users\\alice>", "vars": {name: {"value", "type"}}}
  so restore/verify know which profile path to rewrite away from. Snapshots
  from 0.2.0 are a bare {name: {"value", "type"}} map with no wrapper;
  restore/verify sniff this via the absence of the "vars" key and try to
  recover source_profile from the snapshot's own captured USERPROFILE value
  (0.2.0 always captured it). When that's not derivable, the rewrite step is
  skipped for that entry and recorded as a skipped item with reason -- never
  a silent verbatim write of a source-machine path (Req 14.2).

Notification:
  After writing, we broadcast WM_SETTINGCHANGE so newly opened shells pick
  up the changes. Already-open shells won't see them until restarted.
"""

import ctypes
import os
import re
import winreg
from pathlib import Path

from modules.report import Report

_ENV_PATH = "Environment"

# Variables that are always machine/profile-specific and must never be
# copied verbatim onto a different machine (Req 4.1).
RESTORE_DENYLIST: frozenset[str] = frozenset({
    "TEMP", "TMP", "USERPROFILE", "HOMEPATH", "HOMEDRIVE",
    "APPDATA", "LOCALAPPDATA", "USERNAME",
})


def _is_denylisted(name: str) -> bool:
    """True if `name` must be skipped on restore: an exact denylist match,
    or any OneDrive variant (OneDrive, OneDriveConsumer, OneDriveCommercial),
    case-insensitively."""
    u = name.upper()
    return u in RESTORE_DENYLIST or u.startswith("ONEDRIVE")


def _read_all() -> dict:
    """Read all values from HKCU\\Environment as {name: (value, reg_type)}."""
    out = {}
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _ENV_PATH)
    except OSError:
        return out

    i = 0
    while True:
        try:
            name, value, reg_type = winreg.EnumValue(key, i)
            out[name] = (value, reg_type)
            i += 1
        except OSError:
            break
    winreg.CloseKey(key)
    return out


def _write(name: str, value: str, reg_type: int) -> bool:
    try:
        winreg.CreateKey(winreg.HKEY_CURRENT_USER, _ENV_PATH)
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _ENV_PATH, 0,
                             winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, name, 0, reg_type, value)
        winreg.CloseKey(key)
        return True
    except OSError as e:
        print(f"[env_vars] Could not write {name}: {e}")
        return False


def _broadcast_settings_change():
    ctypes.windll.user32.SendMessageTimeoutW(
        0xFFFF,   # HWND_BROADCAST
        0x001A,   # WM_SETTINGCHANGE
        0, "Environment",
        0x0002,   # SMTO_ABORTIFHUNG
        1000, None
    )


def _merge_path(existing: str, incoming: str) -> str:
    """
    Append entries from `incoming` that aren't already in `existing`.
    Preserves existing order. Case-insensitive comparison since Windows
    paths are case-insensitive.

    NOTE: this only rewrites/drops *incoming* entries (done by the caller
    before this is invoked) -- existing target entries are never touched.
    """
    seen = set()
    merged = []
    for raw in (existing or "").split(";"):
        raw = raw.strip()
        if raw and raw.lower() not in seen:
            seen.add(raw.lower())
            merged.append(raw)
    for raw in (incoming or "").split(";"):
        raw = raw.strip()
        if raw and raw.lower() not in seen:
            seen.add(raw.lower())
            merged.append(raw)
    return ";".join(merged)


def rewrite_profile_paths(value: str, source_profile: str,
                           target_profile: str) -> tuple[str, bool]:
    """
    Replace every occurrence of `source_profile` in `value` at a path
    boundary with the expandable %USERPROFILE% token.

    Matching is case-insensitive (Windows paths are case-insensitive). A
    path boundary means the matched prefix is immediately followed by a
    backslash, a semicolon, a double quote, or the end of the string --
    this prevents "C:\\Users\\alice" from clobbering "C:\\Users\\alice2".

    A no-op (returns (value, False) unchanged) when `source_profile` and
    `target_profile` refer to the same profile (case-insensitive, ignoring
    a trailing backslash), so a same-machine round trip stays byte-identical.

    Returns (new_value, changed).
    """
    if not value or not source_profile:
        return value, False

    src = source_profile.rstrip("\\")
    tgt = target_profile.rstrip("\\") if target_profile else ""

    if not src:
        return value, False

    if tgt and src.lower() == tgt.lower():
        return value, False

    pattern = re.compile(
        re.escape(src) + r'(?=[\\;"]|$)', re.IGNORECASE
    )
    new_value, count = pattern.subn("%USERPROFILE%", value)
    return (new_value, True) if count else (value, False)


def _unwrap(snapshot: dict) -> tuple[dict, str]:
    """
    Normalize the snapshot shape to (vars_map, source_profile), handling
    both the 0.3.0 wrapped shape ({"source_profile", "vars"}) and the bare
    0.2.0 flat map (Req 14.2). For 0.2.0 snapshots, source_profile is
    recovered from the snapshot's own captured USERPROFILE value when
    present; otherwise an empty string is returned (callers must treat that
    as "rewrite is underivable" and skip the rewrite step).
    """
    if isinstance(snapshot, dict) and "vars" in snapshot:
        vars_map = snapshot.get("vars") or {}
        source_profile = snapshot.get("source_profile") or ""
        return vars_map, source_profile

    # 0.2.0 shape: the snapshot dict *is* the vars map.
    vars_map = snapshot or {}
    userprofile_info = vars_map.get("USERPROFILE")
    source_profile = ""
    if isinstance(userprofile_info, dict):
        source_profile = userprofile_info.get("value") or ""
    return vars_map, source_profile


def export(snapshot_dir: Path) -> dict:
    raw = _read_all()
    # Convert tuples to a JSON-friendly shape
    var_out = {name: {"value": v, "type": t} for name, (v, t) in raw.items()}
    source_profile = os.environ.get("USERPROFILE", "")
    print(f"[env_vars] Captured {len(var_out)} user environment variables.")
    return {"source_profile": source_profile, "vars": var_out}


def restore(snapshot: dict, snapshot_dir: Path) -> dict:
    report = Report("env_vars", "restore")

    if not snapshot:
        return report.skip_all("no environment variables in snapshot")

    vars_map, source_profile = _unwrap(snapshot)
    target_profile = os.environ.get("USERPROFILE", "")
    can_rewrite = bool(source_profile)

    current = _read_all()

    for name, info in vars_map.items():
        if not isinstance(info, dict):
            continue
        value = info.get("value", "")
        reg_type = info.get("type", winreg.REG_SZ)

        if _is_denylisted(name):
            report.add_skipped(name, detail="machine-specific (denylist)")
            continue

        # Special-case PATH: two pre-passes over the *incoming* entries only
        # (rewrite, then drop missing directories), then merge with the
        # existing target PATH -- existing entries are never rewritten or
        # dropped (Req 4.3, 4.4).
        if name.upper() == "PATH":
            entries = [e.strip() for e in (value or "").split(";") if e.strip()]
            kept_entries = []
            for entry in entries:
                if can_rewrite:
                    rewritten_entry, _entry_changed = rewrite_profile_paths(
                        entry, source_profile, target_profile)
                else:
                    rewritten_entry = entry
                expanded = os.path.expandvars(rewritten_entry)
                if not os.path.isdir(expanded):
                    report.add_skipped(
                        f"PATH:{entry}",
                        detail=f"PATH entry dropped, directory missing: {entry}")
                    continue
                kept_entries.append(rewritten_entry)

            incoming_path = ";".join(kept_entries)
            existing_value = current.get(name, ("", reg_type))[0]
            merged_value = _merge_path(existing_value, incoming_path)
            if _write(name, merged_value, reg_type):
                report.add_matched(name, detail="PATH merged with existing entries")
            else:
                report.add_failed(name, detail="registry write failed")
            continue

        # Rewrite the source-profile prefix (if any) before writing.
        if can_rewrite:
            write_value, changed = rewrite_profile_paths(
                value, source_profile, target_profile)
        else:
            write_value, changed = value, False
            report.add_skipped(
                f"{name} (rewrite)",
                detail="source profile could not be determined; wrote value verbatim")

        write_type = reg_type
        if changed and reg_type == winreg.REG_SZ:
            # Promote to REG_EXPAND_SZ so %USERPROFILE% expands (Req 4.2).
            write_type = winreg.REG_EXPAND_SZ

        if _write(name, write_value, write_type):
            detail = "rewritten to target profile" if changed else None
            report.add_matched(name, detail=detail)
        else:
            report.add_failed(name, detail="registry write failed")

    _broadcast_settings_change()
    print("[env_vars] Open a new terminal/shell to see the changes.")
    return report.finalize()


def verify(data: dict, snapshot_dir: Path) -> dict:
    """Read-only: compares the live HKCU\\Environment values against the
    post-rewrite values the restore phase should have written. Denylisted
    variables are reported skipped, never as mismatches (Req 4.5)."""
    report = Report("env_vars", "verify")

    if not data:
        return report.skip_all("no environment variables in snapshot")

    vars_map, source_profile = _unwrap(data)
    target_profile = os.environ.get("USERPROFILE", "")
    can_rewrite = bool(source_profile)

    current = _read_all()

    for name, info in vars_map.items():
        if not isinstance(info, dict):
            continue
        value = info.get("value", "")

        if _is_denylisted(name):
            report.add_skipped(name, detail="machine-specific (denylist)")
            continue

        if can_rewrite:
            expected_value, _changed = rewrite_profile_paths(
                value, source_profile, target_profile)
        else:
            expected_value = value

        live = current.get(name)
        actual_value = live[0] if live else None

        if name.upper() == "PATH":
            # Superset check: every kept incoming entry must be present in
            # live PATH (the target may have added its own entries).
            expected_entries = [e.strip() for e in (expected_value or "").split(";") if e.strip()]
            live_entries = {e.strip().lower() for e in (actual_value or "").split(";") if e.strip()}
            missing = [e for e in expected_entries
                       if os.path.isdir(os.path.expandvars(e)) and e.lower() not in live_entries]
            if missing:
                report.add_failed(
                    name, detail=f"missing entries: {missing}",
                    expected=expected_value, actual=actual_value)
            else:
                report.add_matched(name, expected=expected_value, actual=actual_value)
            continue

        if live is None:
            report.add_failed(name, detail="value missing on target",
                               expected=expected_value, actual=None)
        elif actual_value == expected_value:
            report.add_matched(name, expected=expected_value, actual=actual_value)
        else:
            report.add_failed(name, detail="value mismatch",
                               expected=expected_value, actual=actual_value)

    return report.finalize()
