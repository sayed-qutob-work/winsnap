"""
mouse_display.py
Captures and restores mouse, keyboard, and display preferences.

All values live in the registry under HKEY_CURRENT_USER.
Display scaling is read via the registry (no external deps needed).
"""

import ctypes
import winreg
from pathlib import Path


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------

def _read_reg_value(hive, path: str, name: str):
    try:
        key = winreg.OpenKey(hive, path)
        val, _ = winreg.QueryValueEx(key, name)
        winreg.CloseKey(key)
        return val
    except OSError:
        return None


def _write_reg_value(hive, path: str, name: str, value, reg_type=winreg.REG_SZ):
    try:
        key = winreg.OpenKey(hive, path, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, name, 0, reg_type, value)
        winreg.CloseKey(key)
    except OSError as e:
        print(f"[mouse_display] Could not write {path}\\{name}: {e}")


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export(snapshot_dir: Path) -> dict:
    data = {}

    # --- Mouse ---
    mouse_path = r"Control Panel\Mouse"
    data["mouse"] = {
        "speed":             _read_reg_value(winreg.HKEY_CURRENT_USER, mouse_path, "MouseSensitivity"),
        "double_click_speed":_read_reg_value(winreg.HKEY_CURRENT_USER, mouse_path, "DoubleClickSpeed"),
        "swap_buttons":      _read_reg_value(winreg.HKEY_CURRENT_USER, mouse_path, "SwapMouseButtons"),
        "enhance_precision": _read_reg_value(winreg.HKEY_CURRENT_USER, mouse_path, "MouseSpeed"),
        "scroll_lines":      _read_reg_value(winreg.HKEY_CURRENT_USER,
                                              r"Control Panel\Desktop", "WheelScrollLines"),
    }

    # --- Keyboard ---
    kb_path = r"Control Panel\Keyboard"
    data["keyboard"] = {
        "repeat_delay": _read_reg_value(winreg.HKEY_CURRENT_USER, kb_path, "KeyboardDelay"),
        "repeat_speed":  _read_reg_value(winreg.HKEY_CURRENT_USER, kb_path, "KeyboardSpeed"),
    }

    # --- Display scaling (DPI) ---
    # LogPixels under the per-user DPI key (Windows 10+)
    dpi_path = r"Control Panel\Desktop"
    data["display"] = {
        "log_pixels": _read_reg_value(winreg.HKEY_CURRENT_USER, dpi_path, "LogPixels"),
        "dpi_scaling": _read_reg_value(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers",
            "DpiScaling"
        ),
    }

    # --- Cursor scheme ---
    data["cursor_scheme"] = _read_reg_value(
        winreg.HKEY_CURRENT_USER,
        r"Control Panel\Cursors",
        "Scheme Source"
    )

    print(f"[mouse_display] Captured mouse, keyboard, display settings.")
    return data


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

def restore(snapshot: dict, snapshot_dir: Path):
    mouse    = snapshot.get("mouse", {})
    keyboard = snapshot.get("keyboard", {})
    display  = snapshot.get("display", {})

    mouse_path = r"Control Panel\Mouse"
    kb_path    = r"Control Panel\Keyboard"
    desk_path  = r"Control Panel\Desktop"

    # --- Mouse ---
    if mouse.get("speed") is not None:
        _write_reg_value(winreg.HKEY_CURRENT_USER, mouse_path, "MouseSensitivity", mouse["speed"])
    if mouse.get("double_click_speed") is not None:
        _write_reg_value(winreg.HKEY_CURRENT_USER, mouse_path, "DoubleClickSpeed", mouse["double_click_speed"])
    if mouse.get("swap_buttons") is not None:
        _write_reg_value(winreg.HKEY_CURRENT_USER, mouse_path, "SwapMouseButtons", mouse["swap_buttons"])
    if mouse.get("scroll_lines") is not None:
        _write_reg_value(winreg.HKEY_CURRENT_USER, desk_path, "WheelScrollLines", mouse["scroll_lines"])

    # --- Mouse pointer acceleration (enhance precision) ---
    if mouse.get("enhance_precision") is not None:
        _write_reg_value(winreg.HKEY_CURRENT_USER, mouse_path, "MouseSpeed", mouse["enhance_precision"])
        # Apply to the live session via SPI_SETMOUSE
        speed = int(mouse["enhance_precision"])
        # Use conventional thresholds when acceleration is on (speed >= 1)
        if speed >= 1:
            threshold1, threshold2 = 6, 10
        else:
            threshold1, threshold2 = 0, 0
        mouse_params = (ctypes.c_int * 3)(threshold1, threshold2, speed)
        SPI_SETMOUSE = 0x0004
        SPIF_UPDATEINIFILE = 0x0001
        SPIF_SENDCHANGE = 0x0002
        ctypes.windll.user32.SystemParametersInfoW(
            SPI_SETMOUSE, 0, ctypes.byref(mouse_params),
            SPIF_UPDATEINIFILE | SPIF_SENDCHANGE
        )

    # --- Keyboard ---
    if keyboard.get("repeat_delay") is not None:
        _write_reg_value(winreg.HKEY_CURRENT_USER, kb_path, "KeyboardDelay", keyboard["repeat_delay"])
    if keyboard.get("repeat_speed") is not None:
        _write_reg_value(winreg.HKEY_CURRENT_USER, kb_path, "KeyboardSpeed", keyboard["repeat_speed"])

    # --- Display DPI ---
    if display.get("log_pixels") is not None:
        _write_reg_value(winreg.HKEY_CURRENT_USER, desk_path, "LogPixels",
                         display["log_pixels"], winreg.REG_DWORD)

    # Tell Windows to pick up the changes without a full reboot
    SMTO_ABORTIFHUNG = 0x0002
    ctypes.windll.user32.SendMessageTimeoutW(
        0xFFFF,   # HWND_BROADCAST
        0x001A,   # WM_SETTINGCHANGE
        0, "Control Panel",
        SMTO_ABORTIFHUNG, 1000, None
    )

    print("[mouse_display] Mouse, keyboard, display settings restored.")
