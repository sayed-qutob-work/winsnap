"""
wallpaper.py
Captures and restores the desktop wallpaper.

Export: reads the current wallpaper path from the registry, copies the image file
        into the snapshot folder, and returns metadata for snapshot.json.

Restore: copies the saved wallpaper image to a stable location and applies it.
         On single-monitor systems, uses the legacy SystemParametersInfoW API.
         On multi-monitor systems, uses the IDesktopWallpaper COM interface to
         apply the image per-monitor for a clean result.
"""

import os
import shutil
import ctypes
import winreg
from pathlib import Path


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export(snapshot_dir: Path) -> dict:
    """
    Returns a dict that will be stored under snapshot["wallpaper"].
    Copies the wallpaper image into snapshot_dir/wallpaper.<ext>.
    """
    wallpaper_path = _get_current_wallpaper_path()

    if not wallpaper_path or not os.path.isfile(wallpaper_path):
        print("[wallpaper] No wallpaper set or file not found. Skipping.")
        return {"enabled": False}

    ext = Path(wallpaper_path).suffix  # .jpg, .png, etc.
    dest = snapshot_dir / f"wallpaper{ext}"
    shutil.copy2(wallpaper_path, dest)

    print(f"[wallpaper] Captured: {wallpaper_path}")
    return {
        "enabled": True,
        "filename": dest.name,          # e.g. "wallpaper.jpg"
        "original_path": wallpaper_path,
    }


def _get_current_wallpaper_path() -> str | None:
    """Reads the wallpaper path from the Windows registry."""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Control Panel\Desktop"
        )
        value, _ = winreg.QueryValueEx(key, "Wallpaper")
        winreg.CloseKey(key)
        return value if value else None
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

SPI_SETDESKWALLPAPER = 0x0014
SPIF_UPDATEINIFILE   = 0x01
SPIF_SENDCHANGE      = 0x02
SM_CMONITORS         = 80


def _apply_wallpaper_legacy(dest: Path):
    """Apply wallpaper using the legacy single-surface SystemParametersInfoW API."""
    result = ctypes.windll.user32.SystemParametersInfoW(
        SPI_SETDESKWALLPAPER,
        0,
        str(dest),
        SPIF_UPDATEINIFILE | SPIF_SENDCHANGE,
    )
    if result:
        print(f"[wallpaper] Applied (legacy): {dest}")
    else:
        print("[wallpaper] Failed to apply wallpaper (SystemParametersInfoW returned 0).")


def _apply_wallpaper_per_monitor(dest: Path):
    """
    Apply wallpaper using the IDesktopWallpaper COM interface for multi-monitor setups.
    Enumerates all monitors and sets the same image on each.

    Raises an exception if COM is unavailable or fails, so the caller can fall back.
    """
    import comtypes

    # CLSID_DesktopWallpaper and IID_IDesktopWallpaper
    CLSID_DesktopWallpaper = comtypes.GUID("{C2CF3110-460E-4fc1-B9D0-8A1C0C9CC4BD}")
    IID_IDesktopWallpaper = comtypes.GUID("{B92B56A9-8B55-4E14-9A89-0199BBB6F93B}")

    wallpaper_obj = comtypes.CoCreateInstance(
        CLSID_DesktopWallpaper,
        interface=IID_IDesktopWallpaper,
    )

    monitor_count = wallpaper_obj.GetMonitorDevicePathCount()
    dest_str = str(dest)

    for i in range(monitor_count):
        monitor_id = wallpaper_obj.GetMonitorDevicePathAt(i)
        wallpaper_obj.SetWallpaper(monitor_id, dest_str)

    print(f"[wallpaper] Applied (per-monitor, {monitor_count} monitors): {dest}")


def restore(snapshot: dict, snapshot_dir: Path):
    """
    Applies the wallpaper from snapshot_dir onto the current Windows session.
    snapshot is the dict stored under snapshot["wallpaper"].

    On multi-monitor systems (monitor count > 1), uses the IDesktopWallpaper COM
    interface for per-monitor application. Falls back to the legacy API if COM
    is unavailable or fails.
    """
    if not snapshot.get("enabled"):
        print("[wallpaper] Nothing to restore.")
        return

    src = snapshot_dir / snapshot["filename"]
    if not src.exists():
        print(f"[wallpaper] Wallpaper file missing from snapshot: {src}")
        return

    # Copy to a permanent location (Pictures folder) so it survives USB removal
    pictures = Path.home() / "Pictures" / "WinSnap"
    pictures.mkdir(parents=True, exist_ok=True)
    dest = pictures / snapshot["filename"]
    shutil.copy2(src, dest)

    # Detect monitor count
    monitor_count = ctypes.windll.user32.GetSystemMetrics(SM_CMONITORS)

    if monitor_count <= 1:
        # Single-monitor: use the legacy API (preservation path)
        _apply_wallpaper_legacy(dest)
    else:
        # Multi-monitor: use per-monitor IDesktopWallpaper COM interface
        try:
            _apply_wallpaper_per_monitor(dest)
        except Exception as e:
            # Graceful fallback: if COM is unavailable or fails, use legacy API
            print(f"[wallpaper] COM per-monitor path failed ({e}), falling back to legacy API.")
            _apply_wallpaper_legacy(dest)
