"""
power.py
Captures and restores the active Windows power plan.

Export: uses `powercfg /export` to dump the active plan to a .pow file,
        and saves the plan's GUID and name.

Restore: imports the .pow file and sets it as active via `powercfg /import`
         and `powercfg /setactive`.
"""

import subprocess
import re
from pathlib import Path


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def _is_admin() -> bool:
    """Returns True if the current process has administrator privileges."""
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def export(snapshot_dir: Path) -> dict:
    if not _is_admin():
        print("[power] Skipped — requires Administrator rights.")
        print("[power] Tip: re-run export.py as Administrator to capture your power plan.")
        return {"enabled": False, "skip_reason": "not_admin"}

    active_guid, active_name = _get_active_plan()

    if not active_guid:
        print("[power] Could not determine active power plan. Skipping.")
        return {"enabled": False}

    pow_file = snapshot_dir / "power_plan.pow"
    result = subprocess.run(
        ["powercfg", "/export", str(pow_file), active_guid],
        capture_output=True, text=True
    )

    if result.returncode != 0 or not pow_file.exists():
        print(f"[power] Export failed: {result.stderr.strip()}")
        return {"enabled": False}

    print(f"[power] Captured power plan: {active_name} ({active_guid})")
    return {
        "enabled": True,
        "guid": active_guid,
        "name": active_name,
        "filename": "power_plan.pow",
    }


def _get_active_plan() -> tuple[str | None, str | None]:
    """Returns (guid, name) of the currently active power plan."""
    try:
        result = subprocess.run(
            ["powercfg", "/getactivescheme"],
            capture_output=True, text=True
        )
        # Output: "Power Scheme GUID: xxxxxxxx-xxxx-...  (Balanced)"
        match = re.search(
            r"GUID:\s+([\w\-]+)\s+\((.+?)\)",
            result.stdout
        )
        if match:
            return match.group(1).strip(), match.group(2).strip()
    except FileNotFoundError:
        pass
    return None, None


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

def restore(snapshot: dict, snapshot_dir: Path):
    if not snapshot.get("enabled"):
        print("[power] Nothing to restore.")
        return

    pow_file = snapshot_dir / snapshot["filename"]
    if not pow_file.exists():
        print(f"[power] Power plan file missing: {pow_file}")
        return

    original_guid = snapshot["guid"]

    # Import the plan (Windows may assign a new GUID on import)
    result = subprocess.run(
        ["powercfg", "/import", str(pow_file), original_guid],
        capture_output=True, text=True
    )

    # Try to set original GUID as active; fallback to parsing new GUID from output
    active_guid = original_guid
    if result.returncode != 0:
        # Windows rejected the original GUID — parse new one from output
        match = re.search(r"GUID:\s+([\w\-]+)", result.stdout)
        if match:
            active_guid = match.group(1).strip()
        else:
            print(f"[power] Import may have failed: {result.stderr.strip()}")
            return

    subprocess.run(["powercfg", "/setactive", active_guid])
    print(f"[power] Power plan restored: {snapshot['name']} ({active_guid})")
