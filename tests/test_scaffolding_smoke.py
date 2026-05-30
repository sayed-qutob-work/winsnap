"""
Smoke test to verify the test scaffolding fixtures work correctly.

This file validates that conftest.py fixtures are importable and functional.
"""

import json
from pathlib import Path

from tests.conftest import (
    FakeWinReg,
    FakeUser32,
    FakeSubprocess,
    FakeDesktopWallpaper,
    make_fake_desktop_wallpaper,
    populate_winget_export,
    read_winget_export,
    stage_wallpaper_file,
    stage_taskbar_pins,
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
