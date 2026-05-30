"""
explorer.py
Captures and restores File Explorer preferences.

All values live under:
    HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Advanced

Settings covered:
  - Show hidden files
  - Hide file extensions
  - Launch to (This PC vs Quick Access)
  - Show full path in title bar
  - Use checkboxes to select items
  - Expand to current folder in nav pane
  - Show OS protected files (super hidden)
"""

import ctypes
import winreg
from pathlib import Path


_ADV_PATH = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
_CABINET_PATH = r"Software\Microsoft\Windows\CurrentVersion\Explorer\CabinetState"

# (registry_value_name, registry_path) — all DWORD under HKCU
_FIELDS = [
    ("Hidden",                          _ADV_PATH),  # 1=show, 2=hide
    ("HideFileExt",                     _ADV_PATH),  # 0=show ext, 1=hide
    ("LaunchTo",                        _ADV_PATH),  # 1=This PC, 2=Quick Access
    ("ShowSuperHidden",                 _ADV_PATH),  # 0=hide OS, 1=show
    ("AutoCheckSelect",                 _ADV_PATH),  # 0=off, 1=on (checkboxes)
    ("NavPaneExpandToCurrentFolder",    _ADV_PATH),
    ("FullPath",                        _CABINET_PATH),  # show full path in title
]


def _read(path: str, name: str):
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
        val, _ = winreg.QueryValueEx(key, name)
        winreg.CloseKey(key)
        return val
    except OSError:
        return None


def _write(path: str, name: str, value):
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, int(value))
        winreg.CloseKey(key)
        return True
    except OSError as e:
        print(f"[explorer] Could not write {path}\\{name}: {e}")
        return False


def export(snapshot_dir: Path) -> dict:
    data = {}
    for name, path in _FIELDS:
        val = _read(path, name)
        if val is not None:
            data[name] = val
    print(f"[explorer] Captured {len(data)} File Explorer preferences.")
    return data


def restore(snapshot: dict, snapshot_dir: Path):
    written = 0
    for name, path in _FIELDS:
        if name in snapshot and snapshot[name] is not None:
            if _write(path, name, snapshot[name]):
                written += 1

    # Notify Explorer to reload its settings
    ctypes.windll.user32.SendMessageTimeoutW(
        0xFFFF,   # HWND_BROADCAST
        0x001A,   # WM_SETTINGCHANGE
        0, "Environment",
        0x0002,   # SMTO_ABORTIFHUNG
        1000, None
    )
    print(f"[explorer] Restored {written} File Explorer preferences.")
    print("[explorer] Note: changes appear after Explorer is restarted "
          "(taskbar module already does this).")
