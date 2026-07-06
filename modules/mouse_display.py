"""
mouse_display.py
Captures and restores mouse and keyboard preferences, applying the changes
to the live session via SystemParametersInfoW so pointer speed, double-click
time, and keyboard repeat settings take effect immediately -- no logoff
required (Req 12).

All values live in the registry under HKEY_CURRENT_USER.

This module does NOT cover DPI/display scaling. The old `LogPixels` write
was a no-op on modern Windows without its DPI-override sibling values, and
`DpiScaling` was captured but never restored -- both were dead/fake coverage
and have been removed (Req 11.1). Cursor scheme is owned exclusively by
modules/cursors.py; it is not read or written here (Req 11.2). Snapshots
captured before this change may still carry the legacy `"display"` /
`"cursor_scheme"` keys -- restore() ignores them without error, and both
restore() and verify() report DPI as an explicitly skipped, not-covered
aspect rather than presenting it as matched (Req 11.3, 11.4).
"""

import ctypes
import winreg
from pathlib import Path

from modules.report import Report

# ---------------------------------------------------------------------------
# SystemParametersInfo action codes and flags (Req 12)
# ---------------------------------------------------------------------------

SPI_GETMOUSESPEED = 0x0070
SPI_SETMOUSE = 0x0004
SPI_SETMOUSESPEED = 0x0071
SPI_SETDOUBLECLICKTIME = 0x0020
SPI_SETKEYBOARDDELAY = 0x0017
SPI_SETKEYBOARDSPEED = 0x000B

SPIF_UPDATEINIFILE = 0x0001
SPIF_SENDCHANGE = 0x0002
_SPIF_APPLY = SPIF_UPDATEINIFILE | SPIF_SENDCHANGE


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


def _write_reg_value(hive, path: str, name: str, value, reg_type=winreg.REG_SZ) -> bool:
    """Writes a registry value. Returns True on success, False on failure.

    Failures used to be swallowed with only a print(); they now become
    failed report items so a silent write failure can no longer look like a
    successful restore (Req 7.4)."""
    try:
        key = winreg.OpenKey(hive, path, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, name, 0, reg_type, value)
        winreg.CloseKey(key)
        return True
    except OSError as e:
        print(f"[mouse_display] Could not write {path}\\{name}: {e}")
        return False


def _apply_spi(report: Report, item_name: str, action: int, ui_param, pv_param) -> None:
    """Calls SystemParametersInfoW with the persist-to-registry flags
    (SPIF_UPDATEINIFILE | SPIF_SENDCHANGE) so the change takes effect live.

    The registry write always stands regardless of the SPI outcome -- a
    failure here only records a failed live-apply item noting that a logoff
    may be required, it never undoes the write (Req 12.5)."""
    result = ctypes.windll.user32.SystemParametersInfoW(action, ui_param, pv_param, _SPIF_APPLY)
    if not result:
        report.add_failed(item_name, detail="live apply failed; logoff may be required")


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export(snapshot_dir: Path) -> dict:
    mouse_path = r"Control Panel\Mouse"
    kb_path = r"Control Panel\Keyboard"

    data = {
        "mouse": {
            "speed":              _read_reg_value(winreg.HKEY_CURRENT_USER, mouse_path, "MouseSensitivity"),
            "double_click_speed": _read_reg_value(winreg.HKEY_CURRENT_USER, mouse_path, "DoubleClickSpeed"),
            "swap_buttons":       _read_reg_value(winreg.HKEY_CURRENT_USER, mouse_path, "SwapMouseButtons"),
            "enhance_precision":  _read_reg_value(winreg.HKEY_CURRENT_USER, mouse_path, "MouseSpeed"),
            "scroll_lines":       _read_reg_value(winreg.HKEY_CURRENT_USER,
                                                   r"Control Panel\Desktop", "WheelScrollLines"),
            # Actual captured acceleration thresholds -- restore() must use
            # these values, never a hardcoded 6/10 (Req 12.4).
            "threshold1":         _read_reg_value(winreg.HKEY_CURRENT_USER, mouse_path, "MouseThreshold1"),
            "threshold2":         _read_reg_value(winreg.HKEY_CURRENT_USER, mouse_path, "MouseThreshold2"),
        },
        "keyboard": {
            "repeat_delay": _read_reg_value(winreg.HKEY_CURRENT_USER, kb_path, "KeyboardDelay"),
            "repeat_speed": _read_reg_value(winreg.HKEY_CURRENT_USER, kb_path, "KeyboardSpeed"),
        },
    }

    print("[mouse_display] Captured mouse and keyboard settings.")
    return data


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

def restore(snapshot: dict, snapshot_dir: Path) -> dict:
    report = Report("mouse_display", "restore")

    mouse = snapshot.get("mouse", {}) or {}
    keyboard = snapshot.get("keyboard", {}) or {}

    mouse_path = r"Control Panel\Mouse"
    kb_path = r"Control Panel\Keyboard"
    desk_path = r"Control Panel\Desktop"

    # --- Mouse pointer speed ---
    if mouse.get("speed") is not None:
        if _write_reg_value(winreg.HKEY_CURRENT_USER, mouse_path, "MouseSensitivity", mouse["speed"]):
            report.add_matched("mouse_speed")
            _apply_spi(report, "mouse_speed_live", SPI_SETMOUSESPEED, 0, int(mouse["speed"]))
        else:
            report.add_failed("mouse_speed", detail="registry write failed")

    # --- Double-click time ---
    if mouse.get("double_click_speed") is not None:
        if _write_reg_value(winreg.HKEY_CURRENT_USER, mouse_path, "DoubleClickSpeed",
                             mouse["double_click_speed"]):
            report.add_matched("double_click_speed")
            _apply_spi(report, "double_click_speed_live", SPI_SETDOUBLECLICKTIME,
                       int(mouse["double_click_speed"]), 0)
        else:
            report.add_failed("double_click_speed", detail="registry write failed")

    # --- Swap buttons (registry-only; no dedicated SPI action) ---
    if mouse.get("swap_buttons") is not None:
        if _write_reg_value(winreg.HKEY_CURRENT_USER, mouse_path, "SwapMouseButtons", mouse["swap_buttons"]):
            report.add_matched("swap_buttons")
        else:
            report.add_failed("swap_buttons", detail="registry write failed")

    # --- Wheel scroll lines (registry-only; no dedicated SPI action) ---
    if mouse.get("scroll_lines") is not None:
        if _write_reg_value(winreg.HKEY_CURRENT_USER, desk_path, "WheelScrollLines", mouse["scroll_lines"]):
            report.add_matched("scroll_lines")
        else:
            report.add_failed("scroll_lines", detail="registry write failed")

    # --- Mouse pointer acceleration (enhance precision) ---
    if mouse.get("enhance_precision") is not None:
        if _write_reg_value(winreg.HKEY_CURRENT_USER, mouse_path, "MouseSpeed", mouse["enhance_precision"]):
            report.add_matched("mouse_acceleration")
        else:
            report.add_failed("mouse_acceleration", detail="registry write failed")

        # Use the actually captured thresholds -- never a hardcoded 6/10
        # (Req 12.4). Snapshots predating threshold capture fall back to
        # Windows' own "acceleration off" thresholds (0, 0) rather than
        # inventing plausible-looking numbers.
        threshold1 = mouse.get("threshold1")
        threshold2 = mouse.get("threshold2")
        threshold1 = int(threshold1) if threshold1 is not None else 0
        threshold2 = int(threshold2) if threshold2 is not None else 0
        speed = int(mouse["enhance_precision"])
        mouse_params = (ctypes.c_int * 3)(threshold1, threshold2, speed)
        _apply_spi(report, "mouse_acceleration_live", SPI_SETMOUSE, 0, mouse_params)

    # --- Keyboard repeat delay ---
    if keyboard.get("repeat_delay") is not None:
        if _write_reg_value(winreg.HKEY_CURRENT_USER, kb_path, "KeyboardDelay", keyboard["repeat_delay"]):
            report.add_matched("keyboard_delay")
            _apply_spi(report, "keyboard_delay_live", SPI_SETKEYBOARDDELAY,
                       int(keyboard["repeat_delay"]), 0)
        else:
            report.add_failed("keyboard_delay", detail="registry write failed")

    # --- Keyboard repeat speed ---
    if keyboard.get("repeat_speed") is not None:
        if _write_reg_value(winreg.HKEY_CURRENT_USER, kb_path, "KeyboardSpeed", keyboard["repeat_speed"]):
            report.add_matched("keyboard_speed")
            _apply_spi(report, "keyboard_speed_live", SPI_SETKEYBOARDSPEED,
                       int(keyboard["repeat_speed"]), 0)
        else:
            report.add_failed("keyboard_speed", detail="registry write failed")

    # --- Legacy DPI / cursor_scheme fields (Req 11.3) ---
    # Older snapshots may still carry these; they are read-and-ignored here
    # rather than raising, and reported as an explicit not-covered skip
    # instead of silently vanishing.
    if "display" in snapshot or "cursor_scheme" in snapshot:
        report.add_skipped("dpi", detail="DPI not covered")

    # Tell Windows to pick up the changes without a full reboot.
    SMTO_ABORTIFHUNG = 0x0002
    ctypes.windll.user32.SendMessageTimeoutW(
        0xFFFF,   # HWND_BROADCAST
        0x001A,   # WM_SETTINGCHANGE
        0, "Control Panel",
        SMTO_ABORTIFHUNG, 1000, None
    )

    print("[mouse_display] Mouse and keyboard settings restored.")
    return report.finalize()


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

def verify(data: dict, snapshot_dir: Path) -> dict:
    """Read-only: re-reads the Mouse/Keyboard/Desktop registry values and
    compares them against the snapshot, additionally cross-checking mouse
    speed against the live SPI_GETMOUSESPEED counterpart where available
    (Req 12.6). Fields absent from an older snapshot are reported skipped,
    never matched or failed (Req 14.2, 14.4)."""
    report = Report("mouse_display", "verify")

    mouse = data.get("mouse", {}) or {}
    keyboard = data.get("keyboard", {}) or {}

    mouse_path = r"Control Panel\Mouse"
    kb_path = r"Control Panel\Keyboard"
    desk_path = r"Control Panel\Desktop"

    def _compare(name: str, expected, hive_path: str, reg_name: str) -> None:
        if expected is None:
            report.add_skipped(name, detail=f"{reg_name}: not present in snapshot")
            return
        actual = _read_reg_value(winreg.HKEY_CURRENT_USER, hive_path, reg_name)
        if actual == expected:
            report.add_matched(name, expected=expected, actual=actual)
        else:
            report.add_failed(name, detail=f"{reg_name} mismatch",
                               expected=expected, actual=actual)

    _compare("mouse_speed", mouse.get("speed"), mouse_path, "MouseSensitivity")
    _compare("double_click_speed", mouse.get("double_click_speed"), mouse_path, "DoubleClickSpeed")
    _compare("swap_buttons", mouse.get("swap_buttons"), mouse_path, "SwapMouseButtons")
    _compare("scroll_lines", mouse.get("scroll_lines"), desk_path, "WheelScrollLines")
    _compare("mouse_acceleration", mouse.get("enhance_precision"), mouse_path, "MouseSpeed")
    _compare("mouse_threshold1", mouse.get("threshold1"), mouse_path, "MouseThreshold1")
    _compare("mouse_threshold2", mouse.get("threshold2"), mouse_path, "MouseThreshold2")
    _compare("keyboard_delay", keyboard.get("repeat_delay"), kb_path, "KeyboardDelay")
    _compare("keyboard_speed", keyboard.get("repeat_speed"), kb_path, "KeyboardSpeed")

    # Live SPI GET counterpart for mouse speed, where available (Req 12.6).
    if mouse.get("speed") is not None:
        try:
            current = ctypes.c_int()
            ctypes.windll.user32.SystemParametersInfoW(SPI_GETMOUSESPEED, 0, ctypes.byref(current), 0)
            live_speed = current.value
            if str(live_speed) == str(mouse["speed"]):
                report.add_matched("mouse_speed_live", expected=mouse["speed"], actual=live_speed)
            else:
                report.add_failed("mouse_speed_live",
                                   detail="live SPI_GETMOUSESPEED differs from snapshot",
                                   expected=mouse["speed"], actual=live_speed)
        except (OSError, AttributeError, TypeError) as e:
            report.add_skipped("mouse_speed_live", detail=f"SPI_GETMOUSESPEED unavailable: {e}")

    # DPI / cursor_scheme are not covered by this module, regardless of
    # whether the snapshot still carries the legacy keys (Req 11.3).
    if "display" in data or "cursor_scheme" in data:
        report.add_skipped("dpi", detail="DPI not covered")

    return report.finalize()
