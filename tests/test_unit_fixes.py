"""
test_unit_fixes.py — Focused unit tests for the four restore fixes.

Tests each fix with specific deterministic scenarios:
1. Winget schema: _write_filtered_winget_export produces valid JSON with $schema
2. Mouse acceleration: restore writes MouseSpeed and calls SPI_SETMOUSE
3. Wallpaper multi-monitor: restore selects per-monitor vs legacy path
4. Taskbar tolerant copy: restore skips desktop.ini and PermissionError files

Validates: Requirements 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 3.4
"""

import json
import sys
import shutil
import ctypes
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.conftest import (
    FakeWinReg,
    FakeUser32,
    FakeSubprocess,
    _build_winreg_module,
    read_winget_export,
    stage_wallpaper_file,
    stage_taskbar_pins,
)
from modules import winutil


# ===========================================================================
# 1. Winget schema fix — _write_filtered_winget_export
# ===========================================================================

class TestWingetSchemaFix:
    """Validates: Requirements 2.1, 3.3"""

    def test_schema_present_with_selected_packages(self, tmp_path):
        """_write_filtered_winget_export produces JSON with $schema field."""
        from modules import apps

        snapshot_dir = tmp_path / "snapshot"
        snapshot_dir.mkdir()

        selected = [
            {"PackageIdentifier": "Microsoft.VisualStudioCode"},
            {"PackageIdentifier": "Git.Git"},
        ]

        apps._write_filtered_winget_export(snapshot_dir, selected)

        doc = read_winget_export(snapshot_dir)
        assert "$schema" in doc, "Written JSON must contain $schema field"
        assert "winget-packages.schema" in doc["$schema"], \
            "Schema URL must reference winget-packages.schema"

    def test_selected_packages_preserved_exactly(self, tmp_path):
        """Written JSON contains exactly the selected packages, no more, no less."""
        from modules import apps

        snapshot_dir = tmp_path / "snapshot"
        snapshot_dir.mkdir()

        selected = [
            {"PackageIdentifier": "Discord.Discord"},
            {"PackageIdentifier": "Valve.Steam"},
            {"PackageIdentifier": "Mozilla.Firefox"},
        ]

        apps._write_filtered_winget_export(snapshot_dir, selected)

        doc = read_winget_export(snapshot_dir)
        sources = doc.get("Sources", [])
        assert len(sources) == 1
        packages = sources[0].get("Packages", [])
        assert packages == selected

    def test_single_package_selection(self, tmp_path):
        """Works correctly with a single selected package."""
        from modules import apps

        snapshot_dir = tmp_path / "snapshot"
        snapshot_dir.mkdir()

        selected = [{"PackageIdentifier": "Notepad++.Notepad++"}]

        apps._write_filtered_winget_export(snapshot_dir, selected)

        doc = read_winget_export(snapshot_dir)
        assert "$schema" in doc
        packages = doc["Sources"][0]["Packages"]
        assert packages == selected

    def test_empty_selection_apps_restore_noop(self, tmp_path, monkeypatch):
        """Empty winget selection: apps.restore prints no-op message and skips import."""
        from modules import apps

        snapshot_dir = tmp_path / "snapshot"
        snapshot_dir.mkdir()

        fake_sub = FakeSubprocess()
        monkeypatch.setattr(apps, "subprocess", fake_sub)

        snapshot = {"winget": [], "manual": []}
        apps.restore(snapshot, snapshot_dir)

        # No winget import should be called
        winget_calls = fake_sub.get_run_calls_for("winget")
        assert len(winget_calls) == 0, "Empty selection should not invoke winget import"

    def test_source_details_structure(self, tmp_path):
        """Written JSON has proper SourceDetails structure."""
        from modules import apps

        snapshot_dir = tmp_path / "snapshot"
        snapshot_dir.mkdir()

        selected = [{"PackageIdentifier": "Git.Git"}]
        apps._write_filtered_winget_export(snapshot_dir, selected)

        doc = read_winget_export(snapshot_dir)
        source = doc["Sources"][0]
        sd = source["SourceDetails"]
        assert sd["Name"] == "winget"
        assert "Identifier" in sd
        assert "Argument" in sd
        assert "Type" in sd


# ===========================================================================
# 2. Mouse acceleration fix — mouse_display.restore
# ===========================================================================

class TestMouseAccelerationFix:
    """Validates: Requirements 2.2, 3.2"""

    def _setup_mouse_restore(self, monkeypatch, snapshot, fake_reg, fake_u32):
        """Helper to run mouse_display.restore with mocked OS boundaries."""
        from modules import mouse_display

        monkeypatch.setattr(mouse_display, "winreg", _build_winreg_module(fake_reg))

        # Mock ctypes.windll.user32
        mock_windll = MagicMock()
        mock_windll.user32 = fake_u32
        monkeypatch.setattr(mouse_display.ctypes, "windll", mock_windll)

        # We also need ctypes.c_int and ctypes.byref to work
        monkeypatch.setattr(mouse_display.ctypes, "c_int", ctypes.c_int)
        monkeypatch.setattr(mouse_display.ctypes, "byref", ctypes.byref)

        mouse_display.restore(snapshot, Path("/fake/snapshot"))

    def test_writes_mouse_speed_when_enhance_precision_set(self, monkeypatch):
        """When enhance_precision is '1', MouseSpeed is written to registry."""
        fake_reg = FakeWinReg()
        fake_u32 = FakeUser32()

        snapshot = {
            "mouse": {"enhance_precision": "1"},
            "keyboard": {},
            "display": {},
        }

        self._setup_mouse_restore(monkeypatch, snapshot, fake_reg, fake_u32)

        mouse_speed_writes = fake_reg.get_writes_for("MouseSpeed")
        assert len(mouse_speed_writes) == 1, "MouseSpeed must be written once"
        _, _, _, _, _, value = mouse_speed_writes[0]
        assert value == "1"

    def test_calls_spi_setmouse_when_enhance_precision_on(self, monkeypatch):
        """When enhance_precision is '1', SPI_SETMOUSE (0x0004) is called."""
        fake_reg = FakeWinReg()
        fake_u32 = FakeUser32()

        snapshot = {
            "mouse": {"enhance_precision": "1"},
            "keyboard": {},
            "display": {},
        }

        self._setup_mouse_restore(monkeypatch, snapshot, fake_reg, fake_u32)

        SPI_SETMOUSE = 0x0004
        spi_calls = fake_u32.get_spi_calls_for(SPI_SETMOUSE)
        assert len(spi_calls) >= 1, "SPI_SETMOUSE must be called"

    def test_calls_spi_setmouse_when_enhance_precision_off(self, monkeypatch):
        """When enhance_precision is '0', SPI_SETMOUSE is still called (to disable)."""
        fake_reg = FakeWinReg()
        fake_u32 = FakeUser32()

        snapshot = {
            "mouse": {"enhance_precision": "0"},
            "keyboard": {},
            "display": {},
        }

        self._setup_mouse_restore(monkeypatch, snapshot, fake_reg, fake_u32)

        SPI_SETMOUSE = 0x0004
        spi_calls = fake_u32.get_spi_calls_for(SPI_SETMOUSE)
        assert len(spi_calls) >= 1, "SPI_SETMOUSE must be called even for value '0'"

        mouse_speed_writes = fake_reg.get_writes_for("MouseSpeed")
        assert len(mouse_speed_writes) == 1
        _, _, _, _, _, value = mouse_speed_writes[0]
        assert value == "0"

    def test_no_extra_writes_when_enhance_precision_none(self, monkeypatch):
        """When enhance_precision is None, no MouseSpeed write and no SPI_SETMOUSE."""
        fake_reg = FakeWinReg()
        fake_u32 = FakeUser32()

        snapshot = {
            "mouse": {
                "speed": "10",
                "double_click_speed": "500",
                "enhance_precision": None,
            },
            "keyboard": {},
            "display": {},
        }

        self._setup_mouse_restore(monkeypatch, snapshot, fake_reg, fake_u32)

        mouse_speed_writes = fake_reg.get_writes_for("MouseSpeed")
        assert len(mouse_speed_writes) == 0, \
            "MouseSpeed must NOT be written when enhance_precision is None"

        SPI_SETMOUSE = 0x0004
        spi_calls = fake_u32.get_spi_calls_for(SPI_SETMOUSE)
        assert len(spi_calls) == 0, \
            "SPI_SETMOUSE must NOT be called when enhance_precision is None"

    def test_other_fields_still_written(self, monkeypatch):
        """Other mouse fields (speed, double_click_speed) are still written regardless."""
        fake_reg = FakeWinReg()
        fake_u32 = FakeUser32()

        snapshot = {
            "mouse": {
                "speed": "10",
                "double_click_speed": "500",
                "swap_buttons": "0",
                "enhance_precision": "1",
            },
            "keyboard": {},
            "display": {},
        }

        self._setup_mouse_restore(monkeypatch, snapshot, fake_reg, fake_u32)

        sensitivity_writes = fake_reg.get_writes_for("MouseSensitivity")
        assert len(sensitivity_writes) == 1
        _, _, _, _, _, val = sensitivity_writes[0]
        assert val == "10"

        dblclick_writes = fake_reg.get_writes_for("DoubleClickSpeed")
        assert len(dblclick_writes) == 1


# ===========================================================================
# 3. Wallpaper multi-monitor fix — wallpaper.restore
# ===========================================================================

class TestWallpaperMultiMonitorFix:
    """Validates: Requirements 2.3, 3.1

    Note (backend-roundtrip-hardening, Task 6, Req 5.5/5.6): the per-monitor
    `IDesktopWallpaper` COM path this class used to exercise was removed
    entirely -- it was provably dead code (comtypes.CoCreateInstance was
    handed a bare GUID rather than an interface class, so it threw on every
    real machine). `wallpaper.restore()` now always drives the legacy
    SystemParametersInfoW(SPI_SETDESKWALLPAPER) path regardless of monitor
    count. The former `test_per_monitor_path_when_multiple_monitors` and
    `test_com_fallback_to_legacy` tests exercised that deleted COM surface
    and have been removed; the current contract (no comtypes import, legacy
    SPI call regardless of monitor count) is covered by
    `tests/test_wallpaper_multimon_bug.py`.
    """

    def test_legacy_path_when_single_monitor(self, tmp_path, monkeypatch):
        """When monitor count <= 1, the legacy SPI_SETDESKWALLPAPER path is used."""
        from modules import wallpaper

        snapshot_dir = tmp_path / "snapshot"
        snapshot_dir.mkdir()
        stage_wallpaper_file(snapshot_dir, "wallpaper.jpg")

        snapshot = {"enabled": True, "filename": "wallpaper.jpg"}

        # Mock GetSystemMetrics to return 1 monitor
        fake_u32 = FakeUser32()
        fake_u32.metrics[80] = 1  # SM_CMONITORS

        mock_windll = MagicMock()
        mock_windll.user32 = fake_u32
        monkeypatch.setattr(wallpaper.ctypes, "windll", mock_windll)

        # Mock Path.home() to use tmp_path
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        wallpaper.restore(snapshot, snapshot_dir)

        # Legacy SPI_SETDESKWALLPAPER should have been called
        SPI_SETDESKWALLPAPER = 0x0014
        legacy_calls = fake_u32.get_spi_calls_for(SPI_SETDESKWALLPAPER)
        assert len(legacy_calls) == 1, \
            "Legacy SPI_SETDESKWALLPAPER should be called on single monitor"

    def test_disabled_wallpaper_short_circuits(self, tmp_path, monkeypatch):
        """When wallpaper is disabled, restore returns immediately."""
        from modules import wallpaper

        fake_u32 = FakeUser32()
        mock_windll = MagicMock()
        mock_windll.user32 = fake_u32
        monkeypatch.setattr(wallpaper.ctypes, "windll", mock_windll)

        snapshot = {"enabled": False}
        snapshot_dir = tmp_path / "snapshot"
        snapshot_dir.mkdir()

        wallpaper.restore(snapshot, snapshot_dir)

        # No API calls should be made
        assert len(fake_u32.spi_calls) == 0, \
            "No SPI calls when wallpaper is disabled"

    def test_missing_file_short_circuits(self, tmp_path, monkeypatch):
        """When wallpaper file is missing from snapshot, restore returns immediately."""
        from modules import wallpaper

        fake_u32 = FakeUser32()
        mock_windll = MagicMock()
        mock_windll.user32 = fake_u32
        monkeypatch.setattr(wallpaper.ctypes, "windll", mock_windll)

        snapshot = {"enabled": True, "filename": "nonexistent.jpg"}
        snapshot_dir = tmp_path / "snapshot"
        snapshot_dir.mkdir()
        # Don't create the file

        wallpaper.restore(snapshot, snapshot_dir)

        # No API calls should be made
        assert len(fake_u32.spi_calls) == 0, \
            "No SPI calls when wallpaper file is missing"

# ===========================================================================
# 4. Taskbar tolerant copy fix — taskbar.restore
# ===========================================================================

class TestTaskbarTolerantCopyFix:
    """Validates: Requirements 2.4, 3.4"""

    def test_skips_desktop_ini(self, tmp_path, monkeypatch):
        """desktop.ini is skipped during pin restore."""
        from modules import taskbar

        snapshot_dir = tmp_path / "snapshot"
        snapshot_dir.mkdir()
        pins_dir = stage_taskbar_pins(
            snapshot_dir,
            lnk_names=["Notepad.lnk", "Terminal.lnk"],
            include_desktop_ini=True,
        )

        # Mock the target directory
        target_dir = tmp_path / "target_pins"
        target_dir.mkdir()
        monkeypatch.setattr(taskbar, "TASKBAR_PINS_DIR", target_dir)

        # Mock theme writes and explorer restart
        theme_mock = MagicMock()
        explorer_mock = MagicMock()
        monkeypatch.setattr(taskbar, "_write_theme_settings", theme_mock)
        monkeypatch.setattr(winutil, "restart_explorer", explorer_mock)

        snapshot = {
            "pins_backup": "taskbar_pins",
            "theme": {"apps_light_theme": 0},
        }

        taskbar.restore(snapshot, snapshot_dir)

        # .lnk files should be restored
        restored_files = list(target_dir.glob("*.lnk"))
        assert len(restored_files) == 2, "Both .lnk files should be restored"
        restored_names = sorted(f.name for f in restored_files)
        assert restored_names == ["Notepad.lnk", "Terminal.lnk"]

        # desktop.ini should NOT be in the target
        assert not (target_dir / "desktop.ini").exists(), \
            "desktop.ini should be skipped"

    def test_tolerates_permission_error_on_individual_file(self, tmp_path, monkeypatch):
        """Per-file PermissionError is tolerated; other .lnk files still restore."""
        from modules import taskbar

        snapshot_dir = tmp_path / "snapshot"
        snapshot_dir.mkdir()
        pins_dir = stage_taskbar_pins(
            snapshot_dir,
            lnk_names=["Good.lnk", "Bad.lnk", "Also_Good.lnk"],
            include_desktop_ini=False,
        )

        target_dir = tmp_path / "target_pins"
        target_dir.mkdir()
        monkeypatch.setattr(taskbar, "TASKBAR_PINS_DIR", target_dir)

        theme_mock = MagicMock()
        explorer_mock = MagicMock()
        monkeypatch.setattr(taskbar, "_write_theme_settings", theme_mock)
        monkeypatch.setattr(winutil, "restart_explorer", explorer_mock)

        # Patch shutil.copy2 to raise PermissionError for Bad.lnk
        original_copy2 = shutil.copy2

        def selective_copy2(src, dst, **kwargs):
            if Path(src).name == "Bad.lnk":
                raise PermissionError(13, "Permission denied", str(src))
            return original_copy2(src, dst, **kwargs)

        monkeypatch.setattr(shutil, "copy2", selective_copy2)

        snapshot = {
            "pins_backup": "taskbar_pins",
            "theme": {"apps_light_theme": 1},
        }

        # Should NOT raise
        taskbar.restore(snapshot, snapshot_dir)

        # Good.lnk and Also_Good.lnk should be restored
        restored_files = list(target_dir.glob("*.lnk"))
        restored_names = sorted(f.name for f in restored_files)
        assert "Good.lnk" in restored_names
        assert "Also_Good.lnk" in restored_names

    def test_theme_write_and_explorer_restart_still_called(self, tmp_path, monkeypatch):
        """Theme write and Explorer restart run even when files are skipped."""
        from modules import taskbar

        snapshot_dir = tmp_path / "snapshot"
        snapshot_dir.mkdir()
        pins_dir = stage_taskbar_pins(
            snapshot_dir,
            lnk_names=["App.lnk"],
            include_desktop_ini=True,
        )

        target_dir = tmp_path / "target_pins"
        target_dir.mkdir()
        monkeypatch.setattr(taskbar, "TASKBAR_PINS_DIR", target_dir)

        theme_mock = MagicMock()
        explorer_mock = MagicMock()
        monkeypatch.setattr(taskbar, "_write_theme_settings", theme_mock)
        monkeypatch.setattr(winutil, "restart_explorer", explorer_mock)

        snapshot = {
            "pins_backup": "taskbar_pins",
            "theme": {"apps_light_theme": 0, "accent_color": 0xFF00FF},
        }

        taskbar.restore(snapshot, snapshot_dir)

        # Theme write must be called
        theme_mock.assert_called_once()
        theme_args = theme_mock.call_args[0][0]
        assert theme_args["apps_light_theme"] == 0
        assert theme_args["accent_color"] == 0xFF00FF

        # Explorer restart must be called
        explorer_mock.assert_called_once()

    def test_restores_all_lnk_pins_normally(self, tmp_path, monkeypatch):
        """Normal case: all .lnk files are restored when no errors occur."""
        from modules import taskbar

        snapshot_dir = tmp_path / "snapshot"
        snapshot_dir.mkdir()
        pins_dir = stage_taskbar_pins(
            snapshot_dir,
            lnk_names=["Chrome.lnk", "VSCode.lnk", "Slack.lnk"],
            include_desktop_ini=False,
        )

        target_dir = tmp_path / "target_pins"
        target_dir.mkdir()
        monkeypatch.setattr(taskbar, "TASKBAR_PINS_DIR", target_dir)

        theme_mock = MagicMock()
        explorer_mock = MagicMock()
        monkeypatch.setattr(taskbar, "_write_theme_settings", theme_mock)
        monkeypatch.setattr(winutil, "restart_explorer", explorer_mock)

        snapshot = {
            "pins_backup": "taskbar_pins",
            "theme": {},
        }

        taskbar.restore(snapshot, snapshot_dir)

        restored_files = list(target_dir.glob("*.lnk"))
        assert len(restored_files) == 3
        restored_names = sorted(f.name for f in restored_files)
        assert restored_names == ["Chrome.lnk", "Slack.lnk", "VSCode.lnk"]

        theme_mock.assert_called_once()
        explorer_mock.assert_called_once()

    def test_no_pins_backup_still_writes_theme(self, tmp_path, monkeypatch):
        """When pins_backup is None, theme and Explorer restart still run."""
        from modules import taskbar

        snapshot_dir = tmp_path / "snapshot"
        snapshot_dir.mkdir()

        target_dir = tmp_path / "target_pins"
        monkeypatch.setattr(taskbar, "TASKBAR_PINS_DIR", target_dir)

        theme_mock = MagicMock()
        explorer_mock = MagicMock()
        monkeypatch.setattr(taskbar, "_write_theme_settings", theme_mock)
        monkeypatch.setattr(winutil, "restart_explorer", explorer_mock)

        snapshot = {
            "pins_backup": None,
            "theme": {"system_light_theme": 1},
        }

        taskbar.restore(snapshot, snapshot_dir)

        theme_mock.assert_called_once()
        explorer_mock.assert_called_once()
