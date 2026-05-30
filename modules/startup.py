"""
startup.py
Captures and restores user startup programs.

Sources captured (HKCU only -- HKLM requires admin and is risky to alter):
  - HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
  - HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\RunOnce
  - %APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\*.lnk

The .lnk shortcut files are bundled inside the snapshot zip under
"startup_shortcuts/" so they travel with the snapshot.

On restore:
  - Registry entries: written to HKCU. Entries whose binary doesn't exist on
    the target system are skipped with a warning (so we don't pollute Run).
  - Shortcuts: copied back to the user Startup folder. The shortcut's target
    is checked first; missing targets are reported but the shortcut is still
    placed (the user may install the app later).
"""

import os
import shutil
import winreg
from pathlib import Path


_RUN_PATHS = [
    ("Run",     r"Software\Microsoft\Windows\CurrentVersion\Run"),
    ("RunOnce", r"Software\Microsoft\Windows\CurrentVersion\RunOnce"),
]


def _startup_folder() -> Path:
    return (Path(os.environ.get("APPDATA", ""))
            / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup")


def _read_run_entries(reg_path: str) -> dict:
    entries = {}
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path)
    except OSError:
        return entries

    i = 0
    while True:
        try:
            name, value, _ = winreg.EnumValue(key, i)
            entries[name] = value
            i += 1
        except OSError:
            break
    winreg.CloseKey(key)
    return entries


def _write_run_entry(reg_path: str, name: str, value: str):
    try:
        winreg.CreateKey(winreg.HKEY_CURRENT_USER, reg_path)
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path, 0,
                             winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        winreg.CloseKey(key)
        return True
    except OSError:
        return False


def _binary_in_command_exists(command: str) -> bool:
    """
    Best-effort check whether the executable referenced in a Run command
    actually exists on disk. Handles quoted and unquoted paths.
    """
    if not command:
        return False
    cmd = command.strip()
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        if end == -1:
            return False
        binary = cmd[1:end]
    else:
        binary = cmd.split()[0]
    # Expand env vars like %ProgramFiles%
    binary = os.path.expandvars(binary)
    return Path(binary).exists()


def export(snapshot_dir: Path) -> dict:
    data = {"registry": {}, "shortcuts": []}

    # 1. Registry-based startup
    for label, path in _RUN_PATHS:
        data["registry"][label] = _read_run_entries(path)

    # 2. Startup folder .lnk files
    src_folder = _startup_folder()
    if src_folder.exists():
        bundle_dir = snapshot_dir / "startup_shortcuts"
        bundle_dir.mkdir(exist_ok=True)
        for lnk in src_folder.glob("*.lnk"):
            try:
                shutil.copy2(lnk, bundle_dir / lnk.name)
                data["shortcuts"].append({"filename": lnk.name})
            except OSError as e:
                print(f"[startup] Could not copy {lnk.name}: {e}")

    reg_count = sum(len(v) for v in data["registry"].values())
    print(f"[startup] Captured {reg_count} registry entries, "
          f"{len(data['shortcuts'])} shortcuts.")
    return data


def restore(snapshot: dict, snapshot_dir: Path):
    # 1. Registry entries -- skip ones whose binary is missing on this system
    written = 0
    skipped = 0
    for label, path in _RUN_PATHS:
        entries = (snapshot.get("registry") or {}).get(label, {})
        for name, command in entries.items():
            if _binary_in_command_exists(command):
                if _write_run_entry(path, name, command):
                    written += 1
            else:
                print(f"[startup] Skipping {name!r} (binary not found): {command}")
                skipped += 1

    # 2. Shortcuts
    bundle_dir = snapshot_dir / "startup_shortcuts"
    target_folder = _startup_folder()
    target_folder.mkdir(parents=True, exist_ok=True)
    placed = 0
    for entry in snapshot.get("shortcuts", []) or []:
        name = entry.get("filename")
        if not name:
            continue
        src = bundle_dir / name
        if not src.exists():
            continue
        try:
            shutil.copy2(src, target_folder / name)
            placed += 1
        except OSError as e:
            print(f"[startup] Could not place shortcut {name}: {e}")

    print(f"[startup] Restored {written} registry entries "
          f"({skipped} skipped), {placed} shortcuts.")
