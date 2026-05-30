"""
sound_scheme.py
Captures and restores Windows system-sound preferences.

What is captured:
  - Active sound scheme name (e.g. ".Default", ".None", custom)
  - Per-event .Current sound file paths (so a custom scheme moves with you)
  - System Beep on/off (Control Panel\\Sound\\Beep)

Notes:
  - Custom .wav files in user-only locations are NOT bundled in v1; we save
    the path. If the path doesn't exist on the new PC, that event falls back
    to silent. A future enhancement can bundle the .wav files.
  - The active scheme name lives at the "(Default)" value of HKCU\\AppEvents\\Schemes.
"""

import winreg
from pathlib import Path


_SCHEMES_PATH = r"AppEvents\Schemes"
_APPS_PATH    = r"AppEvents\Schemes\Apps"
_BEEP_PATH    = r"Control Panel\Sound"


def _get_default(hive, path):
    try:
        key = winreg.OpenKey(hive, path)
        val, _ = winreg.QueryValueEx(key, "")
        winreg.CloseKey(key)
        return val
    except OSError:
        return None


def _set_default(hive, path, value):
    try:
        winreg.CreateKey(hive, path)
        key = winreg.OpenKey(hive, path, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, value)
        winreg.CloseKey(key)
        return True
    except OSError:
        return False


def _walk_event_sounds(hive, base_path):
    """
    Walk HKCU\\AppEvents\\Schemes\\Apps\\<App>\\<Event>\\.Current
    and return a flat dict { "<App>/<Event>": "<sound file path>" }.
    """
    sounds = {}
    try:
        apps_key = winreg.OpenKey(hive, base_path)
    except OSError:
        return sounds

    i = 0
    while True:
        try:
            app_name = winreg.EnumKey(apps_key, i)
        except OSError:
            break
        i += 1

        try:
            app_key = winreg.OpenKey(apps_key, app_name)
        except OSError:
            continue

        j = 0
        while True:
            try:
                event_name = winreg.EnumKey(app_key, j)
            except OSError:
                break
            j += 1

            current_path = f"{base_path}\\{app_name}\\{event_name}\\.Current"
            sound_file = _get_default(hive, current_path)
            if sound_file:
                sounds[f"{app_name}/{event_name}"] = sound_file

        winreg.CloseKey(app_key)

    winreg.CloseKey(apps_key)
    return sounds


def export(snapshot_dir: Path) -> dict:
    scheme = _get_default(winreg.HKEY_CURRENT_USER, _SCHEMES_PATH)
    sounds = _walk_event_sounds(winreg.HKEY_CURRENT_USER, _APPS_PATH)
    beep = None
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _BEEP_PATH)
        beep, _ = winreg.QueryValueEx(key, "Beep")
        winreg.CloseKey(key)
    except OSError:
        pass

    print(f"[sound_scheme] Active scheme: {scheme!r}, "
          f"{len(sounds)} event sounds, beep={beep}")
    return {
        "scheme": scheme,
        "event_sounds": sounds,
        "beep": beep,
    }


def restore(snapshot: dict, snapshot_dir: Path):
    scheme = snapshot.get("scheme")
    sounds = snapshot.get("event_sounds", {}) or {}
    beep   = snapshot.get("beep")

    # 1. Apply per-event sounds first (only if path is non-empty; missing
    #    files are silently tolerated by Windows).
    applied = 0
    for key, sound_file in sounds.items():
        try:
            app, event = key.split("/", 1)
        except ValueError:
            continue
        path = f"{_APPS_PATH}\\{app}\\{event}\\.Current"
        if _set_default(winreg.HKEY_CURRENT_USER, path, sound_file):
            applied += 1

    # 2. Set the active scheme name
    if scheme:
        _set_default(winreg.HKEY_CURRENT_USER, _SCHEMES_PATH, scheme)

    # 3. System beep
    if beep is not None:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _BEEP_PATH, 0,
                                 winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, "Beep", 0, winreg.REG_SZ, beep)
            winreg.CloseKey(key)
        except OSError:
            pass

    print(f"[sound_scheme] Restored scheme {scheme!r} with {applied} event sounds.")
