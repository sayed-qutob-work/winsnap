"""
taskbar.py
Captures and restores taskbar pins and Windows theme/accent color settings.

Note on taskbar pins:
  Windows 11 stores taskbar pins in a binary database at:
    %APPDATA%/Microsoft/Internet Explorer/Quick Launch/User Pinned/TaskBar/
  The most reliable method is to copy that entire folder.
  Windows 10 uses a similar location but also references the registry.

  We copy the folder on export and restore it on the new machine,
  then restart Explorer so Windows picks up the changes.
"""

import os
import shutil
import ctypes
import winreg
from pathlib import Path


# Taskbar pins folder (works for both Win10 and Win11)
TASKBAR_PINS_DIR = Path(os.environ.get("APPDATA", "")) / \
    "Microsoft" / "Internet Explorer" / "Quick Launch" / "User Pinned" / "TaskBar"


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export(snapshot_dir: Path) -> dict:
    data = {}

    # --- Taskbar pins ---
    pins_backup = snapshot_dir / "taskbar_pins"
    if TASKBAR_PINS_DIR.exists():
        shutil.copytree(TASKBAR_PINS_DIR, pins_backup, dirs_exist_ok=True)
        pin_count = len(list(pins_backup.glob("*.lnk")))
        print(f"[taskbar] Captured {pin_count} pinned shortcuts.")
        data["pins_backup"] = "taskbar_pins"
    else:
        print("[taskbar] Taskbar pins folder not found. Skipping pins.")
        data["pins_backup"] = None

    # --- Theme / accent color ---
    data["theme"] = _read_theme_settings()
    print(f"[taskbar] Captured theme settings.")

    return data


def _read_theme_settings() -> dict:
    theme = {}
    reg_map = {
        # (hive, path, value_name) : output_key
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize",
         "AppsUseLightTheme"): "apps_light_theme",

        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize",
         "SystemUsesLightTheme"): "system_light_theme",

        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\Windows\DWM",
         "AccentColor"): "accent_color",

        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\Windows\DWM",
         "ColorizationColor"): "colorization_color",

        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize",
         "ColorPrevalence"): "color_on_taskbar",

        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize",
         "EnableTransparency"): "transparency",
    }

    for (hive, path, name), key in reg_map.items():
        try:
            reg_key = winreg.OpenKey(hive, path)
            val, _ = winreg.QueryValueEx(reg_key, name)
            winreg.CloseKey(reg_key)
            theme[key] = val
        except OSError:
            theme[key] = None

    return theme


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

def restore(snapshot: dict, snapshot_dir: Path):
    # --- Taskbar pins ---
    pins_backup_name = snapshot.get("pins_backup")
    if pins_backup_name:
        pins_src = snapshot_dir / pins_backup_name
        if pins_src.exists():
            TASKBAR_PINS_DIR.mkdir(parents=True, exist_ok=True)
            _copy_pins_tolerant(pins_src, TASKBAR_PINS_DIR)
        else:
            print("[taskbar] Pins backup folder not found in snapshot.")

    # --- Theme ---
    theme = snapshot.get("theme", {})
    _write_theme_settings(theme)

    # Restart Explorer so taskbar & theme changes take effect
    _restart_explorer()


def _copy_pins_tolerant(src_dir: Path, dst_dir: Path):
    """Copy only .lnk shortcuts from src_dir to dst_dir, skipping
    non-essential files (desktop.ini, etc.) and tolerating per-file
    PermissionError/OSError without aborting."""
    copied = 0
    skipped = []

    for item in src_dir.iterdir():
        # Only restore .lnk shortcut files; skip desktop.ini and other
        # non-essential hidden/system files explicitly.
        if item.suffix.lower() != ".lnk":
            skipped.append(item.name)
            continue

        try:
            shutil.copy2(item, dst_dir / item.name)
            copied += 1
        except (PermissionError, OSError) as e:
            print(f"[taskbar] Warning: could not copy {item.name}: {e}")
            skipped.append(item.name)

    if skipped:
        print(f"[taskbar] Skipped {len(skipped)} non-essential file(s): {skipped}")
    print(f"[taskbar] Restored {copied} pinned shortcut(s) to {dst_dir}")


def _write_theme_settings(theme: dict):
    personalize_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize"
    dwm_path         = r"SOFTWARE\Microsoft\Windows\DWM"

    writes = [
        (winreg.HKEY_CURRENT_USER, personalize_path, "AppsUseLightTheme",   theme.get("apps_light_theme")),
        (winreg.HKEY_CURRENT_USER, personalize_path, "SystemUsesLightTheme",theme.get("system_light_theme")),
        (winreg.HKEY_CURRENT_USER, personalize_path, "ColorPrevalence",     theme.get("color_on_taskbar")),
        (winreg.HKEY_CURRENT_USER, personalize_path, "EnableTransparency",  theme.get("transparency")),
        (winreg.HKEY_CURRENT_USER, dwm_path,          "AccentColor",         theme.get("accent_color")),
        (winreg.HKEY_CURRENT_USER, dwm_path,          "ColorizationColor",   theme.get("colorization_color")),
    ]

    for hive, path, name, value in writes:
        if value is None:
            continue
        try:
            key = winreg.OpenKey(hive, path, 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, int(value))
            winreg.CloseKey(key)
        except OSError as e:
            print(f"[taskbar] Could not write {name}: {e}")

    print("[taskbar] Theme settings written to registry.")


def _restart_explorer():
    """Kills and restarts Explorer so all shell changes take effect."""
    import subprocess
    print("[taskbar] Restarting Explorer to apply changes...")
    subprocess.run(["taskkill", "/f", "/im", "explorer.exe"],
                   capture_output=True)
    subprocess.Popen(["explorer.exe"])
    print("[taskbar] Explorer restarted.")
