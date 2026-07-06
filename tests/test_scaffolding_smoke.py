"""
Smoke test to verify the test scaffolding fixtures work correctly.

This file validates that conftest.py fixtures are importable and functional.
"""

import json
import zipfile
from pathlib import Path

import pytest

from tests.conftest import (
    FakeWinReg,
    FakeUser32,
    FakeSubprocess,
    FakeSubprocessResult,
    FakeDesktopWallpaper,
    make_fake_desktop_wallpaper,
    populate_winget_export,
    read_winget_export,
    stage_wallpaper_file,
    stage_taskbar_pins,
    stage_snapshot_json,
    make_winsnap_zip,
    stage_v020_snapshot,
)


def test_fake_winreg_read_write():
    """FakeWinReg records writes and replays reads."""
    reg = FakeWinReg()
    # Pre-populate a value
    reg.values[(reg.HKEY_CURRENT_USER, r"Control Panel\Mouse", "MouseSpeed")] = ("1", reg.REG_SZ)

    # Read it back
    key = reg.OpenKey(reg.HKEY_CURRENT_USER, r"Control Panel\Mouse")
    val, rtype = reg.QueryValueEx(key, "MouseSpeed")
    reg.CloseKey(key)
    assert val == "1"
    assert rtype == reg.REG_SZ

    # Write a value
    key = reg.OpenKey(reg.HKEY_CURRENT_USER, r"Control Panel\Mouse", 0, reg.KEY_SET_VALUE)
    reg.SetValueEx(key, "MouseSensitivity", 0, reg.REG_SZ, "10")
    reg.CloseKey(key)

    assert len(reg.writes) == 1
    assert reg.get_writes_for("MouseSensitivity")[0][5] == "10"


def test_fake_winreg_missing_value_raises():
    """FakeWinReg raises OSError for missing values."""
    reg = FakeWinReg()
    key = reg.OpenKey(reg.HKEY_CURRENT_USER, r"Some\Path")
    try:
        reg.QueryValueEx(key, "NonExistent")
        assert False, "Should have raised OSError"
    except OSError:
        pass


def test_fake_user32_captures_spi():
    """FakeUser32 records SystemParametersInfoW calls."""
    user32 = FakeUser32()
    user32.SystemParametersInfoW(0x0014, 0, "C:\\wall.jpg", 0x03)

    assert len(user32.spi_calls) == 1
    assert user32.spi_calls[0] == (0x0014, 0, "C:\\wall.jpg", 0x03)
    assert user32.get_spi_calls_for(0x0014) == [(0x0014, 0, "C:\\wall.jpg", 0x03)]


def test_fake_user32_metrics():
    """FakeUser32 returns configured metrics."""
    user32 = FakeUser32()
    user32.metrics[80] = 2  # SM_CMONITORS = 2

    assert user32.GetSystemMetrics(80) == 2
    assert user32.GetSystemMetrics(0) == 1  # default


def test_fake_subprocess_captures_calls():
    """FakeSubprocess records run and Popen calls."""
    subproc = FakeSubprocess()
    result = subproc.run(["winget", "import", "-i", "file.json"], capture_output=True)

    assert result.returncode == 0
    assert len(subproc.run_calls) == 1
    assert subproc.get_run_calls_for("winget")[0][0] == ["winget", "import", "-i", "file.json"]

    subproc.Popen(["explorer.exe"])
    assert len(subproc.popen_calls) == 1


def test_fake_desktop_wallpaper():
    """FakeDesktopWallpaper tracks per-monitor wallpaper calls."""
    wp = make_fake_desktop_wallpaper(monitor_count=2)

    assert wp.GetMonitorDevicePathCount() == 2
    assert wp.GetMonitorDevicePathAt(0) == "\\\\.\\DISPLAY1"
    assert wp.GetMonitorDevicePathAt(1) == "\\\\.\\DISPLAY2"

    wp.SetWallpaper("\\\\.\\DISPLAY1", "C:\\wall.jpg")
    wp.SetWallpaper("\\\\.\\DISPLAY2", "C:\\wall.jpg")

    assert len(wp.set_wallpaper_calls) == 2


def test_snapshot_dir_helpers(tmp_path):
    """Snapshot directory helpers create expected file structures."""
    snap = tmp_path / "snapshot"
    snap.mkdir()

    # Winget export
    packages = [{"PackageIdentifier": "Git.Git"}, {"PackageIdentifier": "Discord.Discord"}]
    populate_winget_export(snap, packages)
    data = read_winget_export(snap)
    assert "Sources" in data
    assert data["Sources"][0]["Packages"] == packages
    assert "$schema" not in data  # no schema by default (matches the bug)

    # With schema
    populate_winget_export(snap, packages, schema="https://aka.ms/winget-packages.schema.2.0.json")
    data = read_winget_export(snap)
    assert data["$schema"] == "https://aka.ms/winget-packages.schema.2.0.json"

    # Wallpaper file
    wp = stage_wallpaper_file(snap, "wallpaper.png")
    assert wp.exists()
    assert wp.name == "wallpaper.png"

    # Taskbar pins
    pins = stage_taskbar_pins(snap, ["Notepad.lnk", "Terminal.lnk"], include_desktop_ini=True)
    assert (pins / "Notepad.lnk").exists()
    assert (pins / "Terminal.lnk").exists()
    assert (pins / "desktop.ini").exists()


def test_snapshot_dir_fixture(snapshot_dir):
    """The snapshot_dir fixture provides a usable temp directory."""
    assert snapshot_dir.exists()
    assert snapshot_dir.is_dir()


def test_fake_winreg_create_key_and_enum_value():
    """
    FakeWinReg.CreateKey and EnumValue support the enumeration/blob-write
    patterns used by env_vars (HKCU\\Environment) and taskband/accent
    (REG_BINARY) restore+verify code paths.
    """
    reg = FakeWinReg()
    reg.values[(reg.HKEY_CURRENT_USER, "Environment", "TEMP")] = (
        r"C:\Users\alice\AppData\Local\Temp", reg.REG_EXPAND_SZ)
    reg.values[(reg.HKEY_CURRENT_USER, "Environment", "PATH")] = (
        r"C:\Tools", reg.REG_SZ)

    key = reg.CreateKey(reg.HKEY_CURRENT_USER, "Environment")
    assert key.hive == reg.HKEY_CURRENT_USER
    assert key.path == "Environment"

    name0, val0, type0 = reg.EnumValue(key, 0)
    name1, val1, type1 = reg.EnumValue(key, 1)
    assert {name0, name1} == {"TEMP", "PATH"}

    # Enumeration is exhausted past the recorded value count, mirroring
    # winreg.EnumValue's OSError at the end of the list.
    with pytest.raises(OSError):
        reg.EnumValue(key, 2)

    # New registry type constants needed for Taskband/env_vars writes.
    assert reg.REG_BINARY == 3
    assert reg.REG_EXPAND_SZ == 2


def test_fake_subprocess_script_helper():
    """
    FakeSubprocess.script() composes matchers into run_side_effect: first
    matching matcher wins, unmatched calls fall back to a default success —
    the convenience needed to script winget/powercfg command sequences.
    """
    subproc = FakeSubprocess()
    subproc.script(
        lambda args: "install" in args and "Git.Git" in args,
        FakeSubprocessResult(returncode=0, stdout="Successfully installed"),
    )
    subproc.script(
        lambda args: "install" in args and "Bad.Package" in args,
        FakeSubprocessResult(returncode=1, stderr="No package found"),
    )

    ok = subproc.run(["winget", "install", "--id", "Git.Git"])
    bad = subproc.run(["winget", "install", "--id", "Bad.Package"])
    unmatched = subproc.run(["winget", "list"])

    assert ok.returncode == 0
    assert bad.returncode == 1
    assert unmatched.returncode == 0  # default success, no matcher hit
    assert len(subproc.run_calls) == 3


def test_stage_snapshot_json(tmp_path):
    """stage_snapshot_json writes a loadable snapshot.json with the requested
    format version and module data."""
    snap_dir = tmp_path / "snap"
    snap_dir.mkdir()
    json_path = stage_snapshot_json(
        snap_dir, version="0.3.0", modules={"apps": {"winget": []}})

    assert json_path.exists()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["snapshot_format_version"] == "0.3.0"
    assert data["winsnap_version"] == "0.3.0"
    assert data["modules"]["apps"] == {"winget": []}
    assert data["modules_attempted"] == ["apps"]


def test_make_winsnap_zip_with_hostile_member(tmp_path):
    """
    make_winsnap_zip builds a well-formed .winsnap archive and can inject
    hostile zip-slip member names alongside it, for restore.py extraction
    safety tests.
    """
    zip_path = make_winsnap_zip(
        tmp_path,
        folder_name="winsnap_zt",
        modules={"wallpaper": {"enabled": False}},
        member_names=["../evil.txt", "winsnap_zt/../../escaped.txt"],
    )

    assert zip_path.exists()
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()

    assert "winsnap_zt/snapshot.json" in names
    assert "../evil.txt" in names
    assert "winsnap_zt/../../escaped.txt" in names


def test_stage_v020_snapshot(tmp_path):
    """
    stage_v020_snapshot produces a full pre-0.3.0 snapshot shape (flat
    env_vars map, no Taskband/accent/style/threshold fields) for backward
    compatibility tests.
    """
    snap_dir = tmp_path / "snap020"
    snap_dir.mkdir()
    snapshot = stage_v020_snapshot(snap_dir)

    assert snapshot["snapshot_format_version"] == "0.2.0"

    # env_vars is a flat map in 0.2.0 (no "vars"/"source_profile" wrapper)
    assert "vars" not in snapshot["modules"]["env_vars"]
    assert "TEMP" in snapshot["modules"]["env_vars"]

    # taskbar predates Taskband/pins/accent capture
    assert "taskband" not in snapshot["modules"]["taskbar"]
    assert "pins" not in snapshot["modules"]["taskbar"]

    # wallpaper predates style/tile/sha256 capture
    assert "style" not in snapshot["modules"]["wallpaper"]
    assert "sha256" not in snapshot["modules"]["wallpaper"]

    # mouse_display still carries the legacy fields this feature removes
    assert "display" in snapshot["modules"]["mouse_display"]
    assert "cursor_scheme" in snapshot["modules"]["mouse_display"]

    # snapshot.json was actually written to disk
    assert (snap_dir / "snapshot.json").exists()
    on_disk = json.loads((snap_dir / "snapshot.json").read_text(encoding="utf-8"))
    assert on_disk == snapshot
