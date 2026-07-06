"""
tests/test_verify_readonly.py — The D10 read-only invariant for every
module's verify() (Task 14).

Feature: backend-roundtrip-hardening, Task 14 (Req 2.1, 2.2, 2.5, 7.2, 15.4;
Design D2, D10, Testing Strategy).

Design D10 states verify() "must be read-only (registry reads, file reads,
`powercfg /getactivescheme`, `winget list`); this is a stated invariant,
enforced in tests by asserting `fake_winreg.writes == []` after verify
calls." This file exercises every module in modules/manifest.py's
MODULE_NAMES that defines a verify() function against a populated fake
registry/filesystem snapshot and asserts:

  - fake_winreg.writes == [] afterward (no module mutates the registry
    during verification), and
  - for the two modules whose verify() legitimately shells out
    (`power` -> `powercfg /getactivescheme`, `apps` -> `winget list`), no
    other subprocess invocation occurs -- in particular never an install,
    import, or setactive call.

**Validates: Requirements 2.1, 2.2, 2.5, 7.2, 15.4**
"""

import base64
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.conftest import (
    FakeSubprocess,
    FakeSubprocessResult,
    FakeUser32,
    FakeWinReg,
    _build_winreg_module,
)

from modules import (
    apps,
    cursors,
    desktop_icons,
    env_vars,
    explorer,
    fonts,
    manifest,
    mouse_display,
    power,
    region_lang,
    sound_scheme,
    startup,
    taskbar,
    wallpaper,
    winutil,
)


# ---------------------------------------------------------------------------
# Completeness: this file's module map must track modules/manifest.py, so a
# newly added module can never silently escape the read-only check below.
# ---------------------------------------------------------------------------

_ALL_MODULE_OBJECTS = {
    "env_vars": env_vars,
    "region_lang": region_lang,
    "apps": apps,
    "wallpaper": wallpaper,
    "mouse_display": mouse_display,
    "cursors": cursors,
    "sound_scheme": sound_scheme,
    "power": power,
    "fonts": fonts,
    "explorer": explorer,
    "desktop_icons": desktop_icons,
    "startup": startup,
    "taskbar": taskbar,
}


def test_module_map_covers_manifest_module_names():
    assert set(_ALL_MODULE_OBJECTS) == set(manifest.MODULE_NAMES)


def test_every_manifest_module_defines_verify():
    """Every module in the canonical order exposes verify() (Req 7.1) --
    restore.py's run_verify() would report a module without one as
    "skipped: verification not implemented" rather than defaulting to
    matched, but by this point in the feature every module has one."""
    missing = [name for name, mod in _ALL_MODULE_OBJECTS.items()
               if getattr(mod, "verify", None) is None]
    assert missing == [], f"modules in manifest without verify(): {missing}"


# ---------------------------------------------------------------------------
# env_vars
# ---------------------------------------------------------------------------

def test_env_vars_verify_is_read_only(monkeypatch, tmp_path):
    fake_reg = FakeWinReg()
    monkeypatch.setattr(env_vars, "winreg", _build_winreg_module(fake_reg))
    monkeypatch.setenv("USERPROFILE", r"C:\Users\bob")

    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, "Environment", "MYAPP_HOME")] = (
        r"%USERPROFILE%\Documents\MyApp", fake_reg.REG_EXPAND_SZ)
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, "Environment", "PATH")] = (
        r"C:\Windows\System32", fake_reg.REG_SZ)

    data = {
        "source_profile": r"C:\Users\alice",
        "vars": {
            "MYAPP_HOME": {"value": r"C:\Users\alice\Documents\MyApp", "type": 1},
            "TEMP": {"value": r"C:\Users\alice\AppData\Local\Temp", "type": 1},
        },
    }
    report = env_vars.verify(data, tmp_path)

    assert report["status"] in ("matched", "partial")
    assert fake_reg.writes == []


# ---------------------------------------------------------------------------
# region_lang
# ---------------------------------------------------------------------------

def test_region_lang_verify_is_read_only(monkeypatch, tmp_path):
    fake_reg = FakeWinReg()
    monkeypatch.setattr(region_lang, "winreg", _build_winreg_module(fake_reg))

    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, region_lang._INTL_PATH, "sCountry")] = (
        "United States", fake_reg.REG_SZ)
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, region_lang._LAYOUT_PRELOAD, "1")] = (
        "00000409", fake_reg.REG_SZ)

    data = {
        "international": {"sCountry": {"value": "United States", "type": 1}},
        "keyboard_layouts": {"1": {"value": "00000409", "type": 1}},
    }
    report = region_lang.verify(data, tmp_path)

    assert report["status"] == "matched"
    assert fake_reg.writes == []


# ---------------------------------------------------------------------------
# apps: verify shells out to `winget list` only
# ---------------------------------------------------------------------------

def test_apps_verify_is_read_only_and_only_calls_winget_list(monkeypatch, snapshot_dir):
    fake_sub = FakeSubprocess()
    monkeypatch.setattr(apps, "subprocess", fake_sub)
    monkeypatch.setattr(apps.shutil, "which", lambda name: r"C:\winget.exe")

    def matcher(args):
        return args[:2] == ["winget", "list"]
    fake_sub.script(matcher, FakeSubprocessResult(returncode=0))

    data = {
        "winget": [{"PackageIdentifier": "Git.Git"}],
        "manual": [{"name": "Some Manual App"}],
    }
    report = apps.verify(data, snapshot_dir)

    # matched winget package + skipped manual (no failed) aggregates to
    # "matched" per the D1 rule -- skipped items remain listed but don't
    # prevent the overall category from reporting success.
    assert report["status"] == "matched"
    for call_args, _kwargs in fake_sub.run_calls:
        assert call_args[:2] == ["winget", "list"], (
            f"apps.verify must only ever call `winget list`, got: {call_args}"
        )


# ---------------------------------------------------------------------------
# power: verify shells out to `powercfg /getactivescheme` only
# ---------------------------------------------------------------------------

def test_power_verify_is_read_only_and_only_calls_getactivescheme(
        monkeypatch, snapshot_dir):
    fake_sub = FakeSubprocess()
    monkeypatch.setattr(power, "subprocess", fake_sub)
    monkeypatch.setattr(power.winutil, "is_admin", lambda: True)

    guid = "11111111-1111-1111-1111-111111111111"

    def matcher(args):
        return args[:2] == ["powercfg", "/getactivescheme"]
    fake_sub.script(
        matcher,
        FakeSubprocessResult(returncode=0, stdout=f"Power Scheme GUID: {guid}  (My Plan)\n"))

    data = {"enabled": True, "guid": guid, "name": "My Plan"}
    report = power.verify(data, snapshot_dir)

    assert report["status"] == "matched"
    for call_args, _kwargs in fake_sub.run_calls:
        assert call_args[:2] == ["powercfg", "/getactivescheme"], (
            f"power.verify must only ever call `powercfg /getactivescheme`, "
            f"got: {call_args}"
        )


# ---------------------------------------------------------------------------
# wallpaper
# ---------------------------------------------------------------------------

def test_wallpaper_verify_is_read_only(monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / "Pictures" / "WinSnap").mkdir(parents=True)
    img = home / "Pictures" / "WinSnap" / "wallpaper.jpg"
    img.write_bytes(b"\xff\xd8\xff" + b"\x11" * 40)
    expected_hash = winutil.sha256_file(img)

    monkeypatch.setattr(wallpaper.Path, "home", staticmethod(lambda: home))
    fake_reg = FakeWinReg()
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, r"Control Panel\Desktop", "Wallpaper")] = (
        str(img), fake_reg.REG_SZ)
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, r"Control Panel\Desktop", "WallpaperStyle")] = (
        "10", fake_reg.REG_SZ)
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, r"Control Panel\Desktop", "TileWallpaper")] = (
        "0", fake_reg.REG_SZ)
    monkeypatch.setattr(wallpaper, "winreg", _build_winreg_module(fake_reg))
    monkeypatch.setattr(winutil, "winreg", _build_winreg_module(fake_reg))

    data = {"enabled": True, "filename": "wallpaper.jpg",
            "style": "10", "tile": "0", "sha256": expected_hash}
    report = wallpaper.verify(data, tmp_path)

    assert report["status"] == "matched"
    assert fake_reg.writes == []


# ---------------------------------------------------------------------------
# mouse_display
# ---------------------------------------------------------------------------

def test_mouse_display_verify_is_read_only(monkeypatch, tmp_path):
    fake_reg = FakeWinReg()
    monkeypatch.setattr(mouse_display, "winreg", _build_winreg_module(fake_reg))
    fake_u32 = FakeUser32()
    mock_windll = MagicMock()
    mock_windll.user32 = fake_u32
    monkeypatch.setattr(mouse_display.ctypes, "windll", mock_windll)

    mouse_path = r"Control Panel\Mouse"
    kb_path = r"Control Panel\Keyboard"
    desk_path = r"Control Panel\Desktop"
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, mouse_path, "MouseSensitivity")] = ("12", 1)
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, mouse_path, "DoubleClickSpeed")] = ("450", 1)
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, mouse_path, "SwapMouseButtons")] = ("0", 1)
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, desk_path, "WheelScrollLines")] = ("3", 1)
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, mouse_path, "MouseSpeed")] = ("1", 1)
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, mouse_path, "MouseThreshold1")] = ("4", 1)
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, mouse_path, "MouseThreshold2")] = ("9", 1)
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, kb_path, "KeyboardDelay")] = ("1", 1)
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, kb_path, "KeyboardSpeed")] = ("28", 1)

    data = {
        "mouse": {"speed": "12", "double_click_speed": "450", "swap_buttons": "0",
                  "enhance_precision": "1", "scroll_lines": "3",
                  "threshold1": "4", "threshold2": "9"},
        "keyboard": {"repeat_delay": "1", "repeat_speed": "28"},
    }
    report = mouse_display.verify(data, tmp_path)

    assert report["status"] in ("matched", "partial")
    assert fake_reg.writes == []


# ---------------------------------------------------------------------------
# cursors
# ---------------------------------------------------------------------------

def test_cursors_verify_is_read_only(monkeypatch, tmp_path):
    fake_reg = FakeWinReg()
    monkeypatch.setattr(cursors, "winreg", _build_winreg_module(fake_reg))
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))

    present = tmp_path / "present.cur"
    present.write_bytes(b"CURSOR")
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, cursors._PATH, "Arrow")] = (
        str(present), fake_reg.REG_SZ)

    data = {"scheme": None, "scheme_source": None,
            "cursors": {"Arrow": str(present)}, "bundled": {}}
    report = cursors.verify(data, tmp_path)

    assert report["status"] == "matched"
    assert fake_reg.writes == []


# ---------------------------------------------------------------------------
# sound_scheme
# ---------------------------------------------------------------------------

def test_sound_scheme_verify_is_read_only(monkeypatch, tmp_path):
    fake_reg = FakeWinReg()
    monkeypatch.setattr(sound_scheme, "winreg", _build_winreg_module(fake_reg))

    sound_file = tmp_path / "ding.wav"
    sound_file.write_bytes(b"RIFFwav")
    reg_path = f"{sound_scheme._APPS_PATH}\\Explorer\\Navigating\\.Current"

    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, sound_scheme._SCHEMES_PATH, "")] = (
        ".Default", fake_reg.REG_SZ)
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, reg_path, "")] = (
        str(sound_file), fake_reg.REG_SZ)

    data = {
        "scheme": ".Default",
        "event_sounds": {"Explorer/Navigating": str(sound_file)},
        "bundled": {},
    }
    report = sound_scheme.verify(data, tmp_path)

    assert report["status"] == "matched"
    assert fake_reg.writes == []


# ---------------------------------------------------------------------------
# fonts
# ---------------------------------------------------------------------------

def test_fonts_verify_is_read_only(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    fake_reg = FakeWinReg()
    monkeypatch.setattr(fonts, "winreg", _build_winreg_module(fake_reg))

    target_dir = fonts._user_fonts_dir()
    target_dir.mkdir(parents=True)
    dst = target_dir / "MyFont.ttf"
    dst.write_bytes(b"\x00" * 32)
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, fonts._FONTS_REG, "My Font")] = (
        str(dst), fake_reg.REG_SZ)

    data = {"fonts": [{"filename": "MyFont.ttf", "display_name": "My Font"}]}
    report = fonts.verify(data, tmp_path)

    assert report["status"] == "matched"
    assert fake_reg.writes == []


# ---------------------------------------------------------------------------
# explorer
# ---------------------------------------------------------------------------

def test_explorer_verify_is_read_only(monkeypatch, tmp_path):
    fake_reg = FakeWinReg()
    monkeypatch.setattr(explorer, "winreg", _build_winreg_module(fake_reg))

    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, explorer._ADV_PATH, "Hidden")] = (
        1, fake_reg.REG_DWORD)

    data = {"Hidden": 1}
    report = explorer.verify(data, tmp_path)

    assert report["status"] == "matched"
    assert fake_reg.writes == []


# ---------------------------------------------------------------------------
# desktop_icons
# ---------------------------------------------------------------------------

def test_desktop_icons_verify_is_read_only(monkeypatch, tmp_path):
    fake_reg = FakeWinReg()
    monkeypatch.setattr(desktop_icons, "winreg", _build_winreg_module(fake_reg))

    fake_reg.values[(fake_reg.HKEY_CURRENT_USER,
                      desktop_icons._PATH,
                      desktop_icons._ICONS["this_pc"])] = (0, fake_reg.REG_DWORD)

    data = {"this_pc": 0}
    report = desktop_icons.verify(data, tmp_path)

    assert report["status"] == "matched"
    assert fake_reg.writes == []


# ---------------------------------------------------------------------------
# startup
# ---------------------------------------------------------------------------

def test_startup_verify_is_read_only(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    fake_reg = FakeWinReg()
    monkeypatch.setattr(startup, "winreg", _build_winreg_module(fake_reg))

    run_reg_path = dict(startup._RUN_PATHS)["Run"]
    existing_binary = sys.executable
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, run_reg_path, "MyApp")] = (
        existing_binary, fake_reg.REG_SZ)

    data = {"registry": {"Run": {"MyApp": existing_binary}}, "shortcuts": []}
    report = startup.verify(data, tmp_path)

    assert report["status"] == "matched"
    assert fake_reg.writes == []


# ---------------------------------------------------------------------------
# taskbar
# ---------------------------------------------------------------------------

def test_taskbar_verify_is_read_only(monkeypatch, tmp_path):
    fake_reg = FakeWinReg()
    monkeypatch.setattr(taskbar, "winreg", _build_winreg_module(fake_reg))

    fav = b"\x00\x01\x02\xfa" * 4
    favres = b"\x10\x20\x30\x40" * 4
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, taskbar.TASKBAND_KEY_PATH, "Favorites")] = (
        fav, fake_reg.REG_BINARY)
    fake_reg.values[(fake_reg.HKEY_CURRENT_USER, taskbar.TASKBAND_KEY_PATH, "FavoritesResolve")] = (
        favres, fake_reg.REG_BINARY)

    data = {
        "pins": None,
        "taskband": {"favorites": base64.b64encode(fav).decode("ascii"),
                     "favorites_resolve": base64.b64encode(favres).decode("ascii")},
        "theme": {},
    }
    report = taskbar.verify(data, tmp_path)

    assert report["status"] == "matched"
    assert fake_reg.writes == []
