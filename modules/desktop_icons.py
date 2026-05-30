"""
desktop_icons.py
Captures and restores which built-in desktop icons are visible
(This PC, Recycle Bin, User folder, Network, Control Panel).

Registry path:
    HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\HideDesktopIcons\\NewStartPanel

Each icon is identified by its CLSID. DWORD value:
    0 = visible
    1 = hidden
"""

import winreg
from pathlib import Path


_PATH = r"Software\Microsoft\Windows\CurrentVersion\Explorer\HideDesktopIcons\NewStartPanel"

# Friendly name -> CLSID
_ICONS = {
    "this_pc":       "{20D04FE0-3AEA-1069-A2D8-08002B30309D}",
    "user_folder":   "{59031a47-3f72-44a7-89c5-5595fe6b30ee}",
    "network":       "{F02C1A0D-BE21-4350-88B0-7367FC96EF3C}",
    "recycle_bin":   "{645FF040-5081-101B-9F08-00AA002F954E}",
    "control_panel": "{5399E694-6CE5-4D6C-8FCE-1D8870FDCBA0}",
}


def _read(name: str):
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PATH)
        val, _ = winreg.QueryValueEx(key, name)
        winreg.CloseKey(key)
        return val
    except OSError:
        return None


def _write(name: str, value: int):
    # Ensure the key exists
    try:
        winreg.CreateKey(winreg.HKEY_CURRENT_USER, _PATH)
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PATH, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, int(value))
        winreg.CloseKey(key)
        return True
    except OSError as e:
        print(f"[desktop_icons] Could not write {name}: {e}")
        return False


def export(snapshot_dir: Path) -> dict:
    data = {}
    for friendly, clsid in _ICONS.items():
        # 0 (visible) is the default if the value doesn't exist
        val = _read(clsid)
        data[friendly] = val if val is not None else 0
    visible = sum(1 for v in data.values() if v == 0)
    print(f"[desktop_icons] {visible}/{len(_ICONS)} desktop icons visible.")
    return data


def restore(snapshot: dict, snapshot_dir: Path):
    written = 0
    for friendly, clsid in _ICONS.items():
        if friendly in snapshot:
            if _write(clsid, snapshot[friendly]):
                written += 1
    print(f"[desktop_icons] Restored visibility for {written} desktop icons.")
    print("[desktop_icons] Refresh desktop (F5) to see the change.")
