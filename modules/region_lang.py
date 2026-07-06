"""
region_lang.py
Captures and restores region, locale, and keyboard layout settings.

Sources:
  - HKCU\\Control Panel\\International ............ locale, formats, calendar
  - HKCU\\Keyboard Layout\\Preload ................ active keyboard layouts (1, 2, 3, ...)
  - HKCU\\Keyboard Layout\\Substitutes ............ optional layout substitutions

Layout codes are Windows hex KLIDs (e.g. "00000409" = en-US, "00000401" = ar-SA).
The user must have the language pack installed on the target system for the
layout to load -- we cannot install language packs from a script in v1.

restore() returns a report.Report dict with one item per registry value
(international settings, keyboard layout preloads, layout substitutes).
verify() re-reads the international and Preload values live and compares
them against the snapshot.
"""

import ctypes
import winreg
from pathlib import Path

from modules.report import Report


_INTL_PATH = r"Control Panel\International"
_LAYOUT_PRELOAD = r"Keyboard Layout\Preload"
_LAYOUT_SUBS = r"Keyboard Layout\Substitutes"

# International values worth saving. Most are REG_SZ; a few are REG_DWORD.
# We don't enumerate everything because some entries are runtime-only.
_INTL_FIELDS = [
    "Locale", "LocaleName", "sLanguage", "sCountry",
    "iCountry", "iCurrDigits", "iCurrency", "iDate", "iDigits",
    "iFirstDayOfWeek", "iFirstWeekOfYear", "iLZero", "iMeasure",
    "iNegCurr", "iNegNumber", "iPaperSize", "iTime", "iTimePrefix", "iTLZero",
    "s1159", "s2359", "sCurrency", "sDate", "sDecimal", "sGrouping",
    "sList", "sLongDate", "sMonDecimalSep", "sMonGrouping", "sMonThousandSep",
    "sNativeDigits", "sNegativeSign", "sPositiveSign", "sShortDate",
    "sShortTime", "sThousand", "sTime", "sTimeFormat", "sYearMonth",
    "NumShape", "Calendars",
]


def _read_str(path: str, name: str):
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
        val, reg_type = winreg.QueryValueEx(key, name)
        winreg.CloseKey(key)
        return val, reg_type
    except OSError:
        return None, None


def _read_all_values(path: str) -> dict:
    out = {}
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
    except OSError:
        return out
    i = 0
    while True:
        try:
            name, value, reg_type = winreg.EnumValue(key, i)
            out[name] = {"value": value, "type": reg_type}
            i += 1
        except OSError:
            break
    winreg.CloseKey(key)
    return out


def _write_value(path: str, name: str, value, reg_type: int) -> bool:
    try:
        winreg.CreateKey(winreg.HKEY_CURRENT_USER, path)
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0,
                             winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, name, 0, reg_type, value)
        winreg.CloseKey(key)
        return True
    except OSError as e:
        print(f"[region_lang] Could not write {path}\\{name}: {e}")
        return False


def export(snapshot_dir: Path) -> dict:
    intl = {}
    for field in _INTL_FIELDS:
        val, reg_type = _read_str(_INTL_PATH, field)
        if val is not None:
            intl[field] = {"value": val, "type": reg_type}

    layouts   = _read_all_values(_LAYOUT_PRELOAD)
    subs      = _read_all_values(_LAYOUT_SUBS)

    print(f"[region_lang] Captured {len(intl)} region/format settings, "
          f"{len(layouts)} keyboard layouts.")

    return {
        "international": intl,
        "keyboard_layouts": layouts,
        "layout_substitutes": subs,
    }


def restore(snapshot: dict, snapshot_dir: Path) -> dict:
    report = Report("region_lang", "restore")
    intl    = snapshot.get("international") or {}
    layouts = snapshot.get("keyboard_layouts") or {}
    subs    = snapshot.get("layout_substitutes") or {}

    for name, info in intl.items():
        item_name = f"intl:{name}"
        if isinstance(info, dict) and "value" in info:
            if _write_value(_INTL_PATH, name, info["value"],
                            info.get("type", winreg.REG_SZ)):
                report.add_matched(item_name, detail="written")
            else:
                report.add_failed(item_name, detail="registry write failed")
        else:
            report.add_skipped(item_name, detail="malformed entry")

    for name, info in layouts.items():
        item_name = f"layout:{name}"
        if isinstance(info, dict) and "value" in info:
            if _write_value(_LAYOUT_PRELOAD, name, info["value"],
                            info.get("type", winreg.REG_SZ)):
                report.add_matched(item_name, detail="written")
            else:
                report.add_failed(item_name, detail="registry write failed")
        else:
            report.add_skipped(item_name, detail="malformed entry")

    for name, info in subs.items():
        item_name = f"substitute:{name}"
        if isinstance(info, dict) and "value" in info:
            if _write_value(_LAYOUT_SUBS, name, info["value"],
                            info.get("type", winreg.REG_SZ)):
                report.add_matched(item_name, detail="written")
            else:
                report.add_failed(item_name, detail="registry write failed")
        else:
            report.add_skipped(item_name, detail="malformed entry")

    # Notify Windows that the locale changed
    ctypes.windll.user32.SendMessageTimeoutW(
        0xFFFF, 0x001A, 0, "intl", 0x0002, 1000, None
    )
    print("[region_lang] Note: keyboard layouts only load if the matching "
          "language pack is installed on this PC.")
    result = report.finalize()
    print(f"[region_lang] restore: {result['status']} "
          f"({len(report.items)} item(s)).")
    return result


def verify(data: dict, snapshot_dir: Path) -> dict:
    """Read-only: re-reads the international and keyboard-layout-preload
    values live and compares them against the snapshot."""
    report = Report("region_lang", "verify")
    intl    = data.get("international") or {}
    layouts = data.get("keyboard_layouts") or {}

    if not intl and not layouts:
        return report.skip_all("no region/language settings in snapshot")

    for name, info in intl.items():
        item_name = f"intl:{name}"
        if not (isinstance(info, dict) and "value" in info):
            report.add_skipped(item_name, detail="malformed entry")
            continue
        expected = info["value"]
        actual, _actual_type = _read_str(_INTL_PATH, name)
        if actual == expected:
            report.add_matched(item_name, expected=expected, actual=actual)
        else:
            report.add_failed(item_name, detail="value mismatch",
                               expected=expected, actual=actual)

    for name, info in layouts.items():
        item_name = f"layout:{name}"
        if not (isinstance(info, dict) and "value" in info):
            report.add_skipped(item_name, detail="malformed entry")
            continue
        expected = info["value"]
        actual, _actual_type = _read_str(_LAYOUT_PRELOAD, name)
        if actual == expected:
            report.add_matched(item_name, expected=expected, actual=actual)
        else:
            report.add_failed(item_name, detail="value mismatch",
                               expected=expected, actual=actual)

    return report.finalize()
