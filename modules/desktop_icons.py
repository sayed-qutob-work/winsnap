"""
desktop_icons.py
Captures and restores which built-in desktop icons are visible
(This PC, Recycle Bin, User folder, Network, Control Panel).

Registry path:
    HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\HideDesktopIcons\\NewStartPanel

Each icon is identified by its CLSID. DWORD value:
    0 = visible
    1 = hidden

restore() returns a report.Report dict with one item per CLSID and sets
explorer_restart_required when any value was written. verify() re-reads the
live CLSID DWORDs and compares them against the snapshot, treating an absent
registry value as the Windows default (0 = visible), matching export()'s own
default-fill behavior.
"""

import winreg
from pathlib import Path

from modules.report import Report


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


def restore(snapshot: dict, snapshot_dir: Path) -> dict:
    report = Report("desktop_icons", "restore")
    for friendly, clsid in _ICONS.items():
        if friendly in snapshot:
            if _write(clsid, snapshot[friendly]):
                report.add_matched(friendly, detail="written")
            else:
                report.add_failed(friendly, detail="registry write failed")
        else:
            report.add_skipped(friendly, detail="not present in snapshot")

    if any(item["status"] == "matched" for item in report.items):
        report.require_explorer_restart()

    result = report.finalize()
    print(f"[desktop_icons] restore: {result['status']} "
          f"({len(report.items)} item(s)).")
    return result


def verify(data: dict, snapshot_dir: Path) -> dict:
    """Read-only: re-reads the live CLSID DWORDs and compares them against
    the snapshot. A missing registry value is treated as the 0 (visible)
    default, matching export()'s own fill-in behavior."""
    report = Report("desktop_icons", "verify")
    for friendly, clsid in _ICONS.items():
        if friendly not in data:
            report.add_skipped(friendly, detail="not present in snapshot")
            continue
        expected = data[friendly]
        actual = _read(clsid)
        if actual is None:
            actual = 0  # default when the value doesn't exist
        if actual == expected:
            report.add_matched(friendly, expected=expected, actual=actual)
        else:
            report.add_failed(friendly, detail="value mismatch",
                               expected=expected, actual=actual)
    return report.finalize()
