"""
cursors.py
Captures and restores the mouse cursor scheme.

Registry:
    HKCU\\Control Panel\\Cursors

Values:
  - "" (default) ............ scheme name
  - Scheme Source (DWORD) ... 0=Windows default, 1=user, 2=system
  - Arrow, Hand, IBeam, ... . path to .cur/.ani file for each cursor role

Note (v1):
  Custom cursor files (.cur / .ani) outside the system path are NOT bundled
  into the snapshot in this version. The paths are saved verbatim. If the
  files don't exist on the new PC, Windows falls back to its default.
  Bundling custom cursor files is a planned enhancement.
"""

import ctypes
import winreg
from pathlib import Path


_PATH = r"Control Panel\Cursors"

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


def export(snapshot_dir: Path) -> dict:
    data = {
        "scheme":         _read(""),                # the (Default) value
        "scheme_source":  _read("Scheme Source"),
        "cursors":        {},
    }
    for role in _CURSOR_ROLES:
        val = _read(role)
        if val:
            data["cursors"][role] = val
    print(f"[cursors] Captured scheme {data['scheme']!r} "
          f"({len(data['cursors'])} cursor paths).")
    return data


def restore(snapshot: dict, snapshot_dir: Path):
    if snapshot.get("scheme") is not None:
        _write("", snapshot["scheme"], winreg.REG_SZ)

    if snapshot.get("scheme_source") is not None:
        _write("Scheme Source", int(snapshot["scheme_source"]),
               winreg.REG_DWORD)

    for role, path in (snapshot.get("cursors") or {}).items():
        _write(role, path, winreg.REG_EXPAND_SZ)

    # Tell Windows to apply the cursor scheme immediately
    # SPI_SETCURSORS = 0x0057
    SPI_SETCURSORS = 0x0057
    SPIF_SENDCHANGE = 0x02
    ctypes.windll.user32.SystemParametersInfoW(SPI_SETCURSORS, 0, None, SPIF_SENDCHANGE)

    print("[cursors] Cursor scheme restored.")
