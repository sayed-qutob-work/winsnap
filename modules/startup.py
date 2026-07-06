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
    the target system are skipped -- recorded as a skipped item that
    includes the command path, never silently dropped (Req 2.4) -- so we
    don't pollute Run with dangling commands.
  - Shortcuts: copied back to the user Startup folder. Shortcuts missing
    from the snapshot bundle are recorded as skipped; copy failures are
    recorded as failed items.

restore() returns a report.Report dict (matched/failed/skipped per item)
instead of only printing. verify() re-reads the live Run/RunOnce values and
the Startup folder's shortcut files and compares them against the snapshot.
"""

import os
import shutil
import winreg
from pathlib import Path

from modules.report import Report


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


def restore(snapshot: dict, snapshot_dir: Path) -> dict:
    report = Report("startup", "restore")

    # 1. Registry entries -- skip ones whose binary is missing on this system
    for label, path in _RUN_PATHS:
        entries = (snapshot.get("registry") or {}).get(label, {})
        for name, command in entries.items():
            item_name = f"{label}:{name}"
            if _binary_in_command_exists(command):
                if _write_run_entry(path, name, command):
                    report.add_matched(item_name, detail="written")
                else:
                    report.add_failed(item_name, detail="registry write failed")
            else:
                # Skipped entry must carry the command path (Req 2.4) so a
                # dropped Run entry is never silently invisible.
                report.add_skipped(item_name, detail=f"binary not found: {command}")

    # 2. Shortcuts
    bundle_dir = snapshot_dir / "startup_shortcuts"
    target_folder = _startup_folder()
    target_folder.mkdir(parents=True, exist_ok=True)
    for entry in snapshot.get("shortcuts", []) or []:
        name = entry.get("filename")
        if not name:
            continue
        item_name = f"shortcut:{name}"
        src = bundle_dir / name
        if not src.exists():
            report.add_skipped(item_name, detail="missing in snapshot bundle")
            continue
        try:
            shutil.copy2(src, target_folder / name)
            report.add_matched(item_name, detail="placed in Startup folder")
        except OSError as e:
            report.add_failed(item_name, detail=f"could not place shortcut: {e}")

    result = report.finalize()
    print(f"[startup] restore: {result['status']} "
          f"({len(report.items)} item(s)).")
    return result


def verify(data: dict, snapshot_dir: Path) -> dict:
    """Read-only: re-reads the live Run/RunOnce values and Startup folder
    contents and compares them against the snapshot. Entries whose binary is
    still missing on this system are reported skipped (never a false
    mismatch), consistent with restore()'s own skip logic."""
    report = Report("startup", "verify")

    for label, path in _RUN_PATHS:
        entries = (data.get("registry") or {}).get(label, {})
        live = _read_run_entries(path)
        for name, command in entries.items():
            item_name = f"{label}:{name}"
            if not _binary_in_command_exists(command):
                report.add_skipped(item_name, detail=f"binary not found: {command}")
                continue
            actual = live.get(name)
            if actual == command:
                report.add_matched(item_name, expected=command, actual=actual)
            else:
                report.add_failed(item_name, detail="value mismatch",
                                   expected=command, actual=actual)

    target_folder = _startup_folder()
    for entry in data.get("shortcuts", []) or []:
        name = entry.get("filename")
        if not name:
            continue
        item_name = f"shortcut:{name}"
        if (target_folder / name).exists():
            report.add_matched(item_name, detail="shortcut present")
        else:
            report.add_failed(item_name, detail="shortcut file missing on target",
                               expected=name, actual=None)

    return report.finalize()
