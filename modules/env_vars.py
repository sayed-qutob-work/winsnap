"""
env_vars.py
Captures and restores user environment variables (HKCU\\Environment).

Why HKCU only:
  System variables live under HKLM and altering them risks breaking the OS
  (e.g. clobbering SystemRoot or PATH for services). They also need admin.
  WinSnap stays out of HKLM by design.

PATH handling:
  PATH on the new PC is *merged*, not replaced. We append your saved entries
  that aren't already present, preserving the order of the existing ones.
  This avoids losing tools the new PC's installer added (e.g. Python, Git).

Notification:
  After writing, we broadcast WM_SETTINGCHANGE so newly opened shells pick
  up the changes. Already-open shells won't see them until restarted.
"""

import ctypes
import winreg
from pathlib import Path


_ENV_PATH = "Environment"


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


def export(snapshot_dir: Path) -> dict:
    raw = _read_all()
    # Convert tuples to a JSON-friendly shape
    out = {name: {"value": v, "type": t} for name, (v, t) in raw.items()}
    print(f"[env_vars] Captured {len(out)} user environment variables.")
    return out


def restore(snapshot: dict, snapshot_dir: Path):
    if not snapshot:
        print("[env_vars] No environment variables in snapshot.")
        return

    current = _read_all()
    written = 0
    merged_path = False

    for name, info in snapshot.items():
        if not isinstance(info, dict):
            continue
        value = info.get("value", "")
        reg_type = info.get("type", winreg.REG_SZ)

        # Special-case PATH: merge instead of replace
        if name.upper() == "PATH":
            existing_value = current.get(name, ("", reg_type))[0]
            new_value = _merge_path(existing_value, value)
            if _write(name, new_value, reg_type):
                merged_path = True
                written += 1
            continue

        if _write(name, value, reg_type):
            written += 1

    _broadcast_settings_change()
    msg = f"[env_vars] Restored {written} variables"
    if merged_path:
        msg += " (PATH was merged, not replaced)"
    msg += "."
    print(msg)
    print("[env_vars] Open a new terminal/shell to see the changes.")
