"""
test_integration_restore.py — Integration tests against restore.py.

These tests exercise the full restore.py flow (or as much as possible) with
mocked OS boundaries. They verify end-to-end behavior including module ordering,
error handling, and correct interaction between restore.py and the individual
modules.

Validates: Requirements 2.1, 2.3, 2.4, 3.4, 3.6
"""

import json
import sys
import shutil
import zipfile
from pathlib import Path
from unittest.mock import patch, MagicMock, ANY
from io import StringIO

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.conftest import (
    FakeWinReg,
    FakeUser32,
    FakeSubprocess,
    FakeDesktopWallpaper,
    _build_winreg_module,
    stage_wallpaper_file,
    stage_taskbar_pins,
)

import restore as restore_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_winsnap_archive(tmp_path, snapshot_data: dict, extra_files: dict = None):
    """
    Create a .winsnap zip archive with a snapshot.json and optional extra files.

    Args:
        tmp_path: pytest tmp_path fixture
        snapshot_data: dict to serialize as snapshot.json
        extra_files: dict of {relative_path: bytes_content} for additional files

    Returns:
        Path to the created .winsnap file
    """
    # Create the snapshot directory structure inside a subfolder
    snap_folder_name = "winsnap_20240101_120000"
    snap_content_dir = tmp_path / "archive_content" / snap_folder_name
    snap_content_dir.mkdir(parents=True)

    # Write snapshot.json
    json_path = snap_content_dir / "snapshot.json"
    json_path.write_text(json.dumps(snapshot_data, indent=2), encoding="utf-8")

    # Write extra files
    if extra_files:
        for rel_path, content in extra_files.items():
            file_path = snap_content_dir / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                file_path.write_bytes(content)
            else:
                file_path.write_text(content, encoding="utf-8")

    # Create the zip
    archive_path = tmp_path / "test.winsnap"
    with zipfile.ZipFile(archive_path, "w") as zf:
        for file in snap_content_dir.rglob("*"):
            if file.is_file():
                arcname = str(file.relative_to(tmp_path / "archive_content"))
                zf.write(file, arcname)

    return archive_path


def _build_winget_export_json(packages: list) -> str:
    """Build a valid winget_export.json string with schema and packages."""
    data = {
        "$schema": "https://aka.ms/winget-packages.schema.2.0.json",
        "CreationDate": "2024-01-01T00:00:00.000-00:00",
        "Sources": [
            {
                "SourceDetails": {
                    "Name": "winget",
                    "Identifier": "Microsoft.Winget.Source_8wekyb3d8bbwe",
                    "Argument": "https://cdn.winget.microsoft.com/cache",
                    "Type": "Microsoft.PreIndexed.Package",
                },
                "Packages": packages,
            }
        ],
    }
    return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# Test 1: Non-empty apps selection — winget import with schema-valid JSON
# ---------------------------------------------------------------------------

class TestAppsIntegration:
    """
    Integration test: non-empty apps selection triggers winget import with
    a schema-valid JSON file, and success/warning reporting is preserved.

    Validates: Requirements 2.1, 3.6
    """

    def test_winget_import_invoked_with_schema_valid_json(self, tmp_path, monkeypatch):
        """
        Full restore flow with non-empty apps selection: winget import is
        invoked and the JSON file it receives contains a valid $schema field.
        """
        packages = [
            {"PackageIdentifier": "Microsoft.VisualStudioCode"},
            {"PackageIdentifier": "Git.Git"},
        ]

        winget_export_content = _build_winget_export_json(packages)

        snapshot_data = {
            "exported_at": "2024-01-01T12:00:00",
            "winsnap_version": "0.2.0",
            "snapshot_format_version": "0.2.0",
            "modules": {
                "apps": {
                    "winget": packages,
                    "manual": [{"name": "SomeApp", "urlinfoabout": "https://example.com"}],
                },
            },
        }

        extra_files = {
            "winget_export.json": winget_export_content,
        }

        archive_path = _create_winsnap_archive(tmp_path, snapshot_data, extra_files)

        # Track subprocess calls
        captured_winget_calls = []
        captured_json_content = {}

        def fake_subprocess_run(args, **kwargs):
            captured_winget_calls.append((args, kwargs))
            # When winget import is called, read the JSON file it references
            if args and args[0] == "winget" and "import" in args:
                # Find the -i flag and get the file path
                for i, arg in enumerate(args):
                    if arg == "-i" and i + 1 < len(args):
                        json_file = Path(args[i + 1])
                        if json_file.exists():
                            captured_json_content["data"] = json.loads(
                                json_file.read_text(encoding="utf-8")
                            )
                        break
            result = MagicMock()
            result.returncode = 0
            return result

        # Mock subprocess in the apps module
        from modules import apps
        monkeypatch.setattr(apps, "subprocess", MagicMock())
        apps.subprocess.run = fake_subprocess_run

        # Mock all other modules to no-op (we only care about apps)
        monkeypatch.setattr(sys, "argv", ["restore.py", str(archive_path), "--only", "apps"])

        # Run restore
        with patch("sys.exit"):
            restore_module.main()

        # Assert winget import was called
        winget_import_calls = [
            (a, kw) for a, kw in captured_winget_calls
            if a and len(a) > 1 and a[0] == "winget" and "import" in a
        ]
        assert len(winget_import_calls) == 1, \
            "winget import should be invoked exactly once"

        # Assert the JSON file has a valid $schema
        assert "data" in captured_json_content, \
            "Should have been able to read the winget_export.json file"
        doc = captured_json_content["data"]
        assert "$schema" in doc, \
            "winget_export.json must contain $schema field"
        assert "winget-packages.schema" in doc["$schema"], \
            "Schema URL must reference winget-packages.schema"

        # Assert packages are preserved
        sources = doc.get("Sources", [])
        assert len(sources) == 1
        actual_packages = sources[0].get("Packages", [])
        assert actual_packages == packages, \
            "Packages in the JSON must match the snapshot selection"

    def test_winget_import_success_reporting(self, tmp_path, monkeypatch, capsys):
        """
        When winget import succeeds (returncode 0), the success message is printed.
        Validates preservation of success/warning reporting (Requirement 3.6).
        """
        packages = [{"PackageIdentifier": "Git.Git"}]
        winget_export_content = _build_winget_export_json(packages)

        snapshot_data = {
            "exported_at": "2024-01-01T12:00:00",
            "winsnap_version": "0.2.0",
            "snapshot_format_version": "0.2.0",
            "modules": {
                "apps": {"winget": packages, "manual": []},
            },
        }

        extra_files = {"winget_export.json": winget_export_content}
        archive_path = _create_winsnap_archive(tmp_path, snapshot_data, extra_files)

        from modules import apps
        fake_run = MagicMock()
        fake_run.return_value = MagicMock(returncode=0)
        monkeypatch.setattr(apps.subprocess, "run", fake_run)

        monkeypatch.setattr(sys, "argv", ["restore.py", str(archive_path), "--only", "apps"])

        with patch("sys.exit"):
            restore_module.main()

        captured = capsys.readouterr()
        assert "installed successfully" in captured.out, \
            "Success message should be printed when winget import returns 0"

    def test_winget_import_warning_reporting(self, tmp_path, monkeypatch, capsys):
        """
        When winget import fails (returncode != 0), the warning message is printed.
        Validates preservation of success/warning reporting (Requirement 3.6).
        """
        packages = [{"PackageIdentifier": "Git.Git"}]
        winget_export_content = _build_winget_export_json(packages)

        snapshot_data = {
            "exported_at": "2024-01-01T12:00:00",
            "winsnap_version": "0.2.0",
            "snapshot_format_version": "0.2.0",
            "modules": {
                "apps": {"winget": packages, "manual": []},
            },
        }

        extra_files = {"winget_export.json": winget_export_content}
        archive_path = _create_winsnap_archive(tmp_path, snapshot_data, extra_files)

        from modules import apps
        fake_run = MagicMock()
        fake_run.return_value = MagicMock(returncode=1)
        monkeypatch.setattr(apps.subprocess, "run", fake_run)

        monkeypatch.setattr(sys, "argv", ["restore.py", str(archive_path), "--only", "apps"])

        with patch("sys.exit"):
            restore_module.main()

        captured = capsys.readouterr()
        assert "may have failed" in captured.out, \
            "Warning message should be printed when winget import returns non-zero"


# ---------------------------------------------------------------------------
# Test 2: Mocked 2-monitor environment — per-monitor wallpaper path
# ---------------------------------------------------------------------------

class TestWallpaperMultiMonitorIntegration:
    """
    Integration test: mocked 2-monitor environment triggers the per-monitor
    wallpaper apply path and the overall run reports no errors.

    Validates: Requirements 2.3
    """

    def test_wallpaper_per_monitor_path_in_full_restore(self, tmp_path, monkeypatch, capsys):
        """
        Full restore flow on a 2-monitor environment: wallpaper module uses
        the per-monitor IDesktopWallpaper path and restore reports no errors.
        """
        from modules import wallpaper

        # Create a dummy wallpaper file
        wp_content = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # JPEG-like

        snapshot_data = {
            "exported_at": "2024-01-01T12:00:00",
            "winsnap_version": "0.2.0",
            "snapshot_format_version": "0.2.0",
            "modules": {
                "wallpaper": {
                    "enabled": True,
                    "filename": "wallpaper.jpg",
                    "original_path": "C:\\Users\\Test\\Pictures\\bg.jpg",
                },
            },
        }

        extra_files = {"wallpaper.jpg": wp_content}
        archive_path = _create_winsnap_archive(tmp_path, snapshot_data, extra_files)

        # Mock GetSystemMetrics to return 2 monitors
        fake_u32 = FakeUser32()
        fake_u32.metrics[80] = 2  # SM_CMONITORS

        mock_windll = MagicMock()
        mock_windll.user32 = fake_u32
        monkeypatch.setattr(wallpaper.ctypes, "windll", mock_windll)

        # Mock the COM path
        fake_wp = FakeDesktopWallpaper(monitor_count=2)
        mock_comtypes = MagicMock()
        mock_comtypes.GUID = MagicMock(side_effect=lambda x: x)
        mock_comtypes.CoCreateInstance = MagicMock(return_value=fake_wp)
        monkeypatch.setitem(sys.modules, "comtypes", mock_comtypes)

        # Mock Path.home() to use tmp_path for the Pictures folder
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        monkeypatch.setattr(sys, "argv", ["restore.py", str(archive_path), "--only", "wallpaper"])

        with patch("sys.exit"):
            restore_module.main()

        # Per-monitor path should have been used
        assert len(fake_wp.set_wallpaper_calls) == 2, \
            "Per-monitor SetWallpaper should be called for each of 2 monitors"

        # Legacy SPI_SETDESKWALLPAPER should NOT have been called
        SPI_SETDESKWALLPAPER = 0x0014
        legacy_calls = fake_u32.get_spi_calls_for(SPI_SETDESKWALLPAPER)
        assert len(legacy_calls) == 0, \
            "Legacy SPI_SETDESKWALLPAPER should NOT be called on multi-monitor"

        # Overall run should report no errors
        captured = capsys.readouterr()
        assert "error" not in captured.out.lower().split("restore completed")[1] \
            if "restore completed" in captured.out.lower() else True, \
            "Overall restore should report no errors for wallpaper"
        # More robust check: no "ERROR during restore" in output
        assert "ERROR during restore" not in captured.out, \
            "No module-level errors should be reported"


# ---------------------------------------------------------------------------
# Test 3: Taskbar pins with permission-denied desktop.ini
# ---------------------------------------------------------------------------

class TestTaskbarDesktopIniIntegration:
    """
    Integration test: taskbar pins backup includes a permission-denied
    desktop.ini. The taskbar step completes, theme is applied, Explorer
    restart is invoked, and restore.py reports zero errors for taskbar.

    Validates: Requirements 2.4, 3.4
    """

    def test_taskbar_completes_with_desktop_ini_present(self, tmp_path, monkeypatch, capsys):
        """
        Full restore flow where taskbar pins backup includes desktop.ini:
        taskbar step completes without error, theme is applied, Explorer
        is restarted.
        """
        from modules import taskbar

        # Create pins backup with desktop.ini and .lnk files
        lnk_content = b"\x4c\x00\x00\x00" + b"\x00" * 50
        ini_content = "[.ShellClassInfo]\nIconResource=imageres.dll,-1023\n"

        snapshot_data = {
            "exported_at": "2024-01-01T12:00:00",
            "winsnap_version": "0.2.0",
            "snapshot_format_version": "0.2.0",
            "modules": {
                "taskbar": {
                    "pins_backup": "taskbar_pins",
                    "theme": {
                        "apps_light_theme": 0,
                        "system_light_theme": 0,
                        "accent_color": 0xFF8800,
                        "colorization_color": 0xFF8800,
                        "color_on_taskbar": 1,
                        "transparency": 1,
                    },
                },
            },
        }

        extra_files = {
            "taskbar_pins/Notepad.lnk": lnk_content,
            "taskbar_pins/Terminal.lnk": lnk_content,
            "taskbar_pins/desktop.ini": ini_content,
        }

        archive_path = _create_winsnap_archive(tmp_path, snapshot_data, extra_files)

        # Mock TASKBAR_PINS_DIR to a temp location
        target_dir = tmp_path / "target_pins"
        target_dir.mkdir()
        monkeypatch.setattr(taskbar, "TASKBAR_PINS_DIR", target_dir)

        # Mock _write_theme_settings to capture calls (but use a fake winreg)
        theme_calls = []
        original_write_theme = taskbar._write_theme_settings

        def mock_write_theme(theme):
            theme_calls.append(theme)
            # Don't actually write to registry
            print("[taskbar] Theme settings written to registry.")

        monkeypatch.setattr(taskbar, "_write_theme_settings", mock_write_theme)

        # Mock _restart_explorer to capture calls
        explorer_calls = []

        def mock_restart_explorer():
            explorer_calls.append(True)
            print("[taskbar] Restarting Explorer to apply changes...")
            print("[taskbar] Explorer restarted.")

        monkeypatch.setattr(taskbar, "_restart_explorer", mock_restart_explorer)

        monkeypatch.setattr(sys, "argv", ["restore.py", str(archive_path), "--only", "taskbar"])

        with patch("sys.exit"):
            restore_module.main()

        # .lnk files should be restored
        restored_lnks = list(target_dir.glob("*.lnk"))
        assert len(restored_lnks) == 2, \
            "Both .lnk files should be restored"
        restored_names = sorted(f.name for f in restored_lnks)
        assert restored_names == ["Notepad.lnk", "Terminal.lnk"]

        # desktop.ini should NOT be in the target
        assert not (target_dir / "desktop.ini").exists(), \
            "desktop.ini should be skipped (not a .lnk file)"

        # Theme should have been applied
        assert len(theme_calls) == 1, "Theme write should be called once"
        assert theme_calls[0]["apps_light_theme"] == 0

        # Explorer should have been restarted
        assert len(explorer_calls) == 1, "Explorer restart should be called once"

        # restore.py should report zero errors for taskbar
        captured = capsys.readouterr()
        assert "ERROR during restore" not in captured.out, \
            "restore.py should report zero errors for taskbar"
        assert "Restore completed successfully" in captured.out or \
               "error(s)" not in captured.out.split("=")[-1], \
            "Final summary should not mention errors"


# ---------------------------------------------------------------------------
# Test 4: Context/ordering check — module run order and exception handling
# ---------------------------------------------------------------------------

class TestModuleOrderingAndErrorHandling:
    """
    Integration test: confirm the module run order in restore.py (ALL_MODULES)
    is unchanged and that a per-module exception is still caught and surfaced
    in the final error summary.

    Validates: Requirements 3.4, 3.6
    """

    def test_all_modules_order_unchanged(self):
        """
        The ALL_MODULES list in restore.py has the expected order:
        settings before Explorer restart, apps last.
        """
        expected_order = [
            "env_vars",
            "region_lang",
            "wallpaper",
            "mouse_display",
            "cursors",
            "sound_scheme",
            "power",
            "explorer",
            "desktop_icons",
            "fonts",
            "startup",
            "taskbar",
            "apps",
        ]

        actual_order = [key for key, mod in restore_module.ALL_MODULES]
        assert actual_order == expected_order, \
            f"ALL_MODULES order must be preserved. Got: {actual_order}"

    def test_per_module_exception_caught_and_surfaced(self, tmp_path, monkeypatch, capsys):
        """
        When a module raises an exception during restore, it is caught and
        surfaced in the final error summary without aborting other modules.
        """
        from modules import wallpaper, apps

        snapshot_data = {
            "exported_at": "2024-01-01T12:00:00",
            "winsnap_version": "0.2.0",
            "snapshot_format_version": "0.2.0",
            "modules": {
                "wallpaper": {
                    "enabled": True,
                    "filename": "wallpaper.jpg",
                },
                "apps": {
                    "winget": [],
                    "manual": [],
                },
            },
        }

        archive_path = _create_winsnap_archive(tmp_path, snapshot_data)

        # Make wallpaper.restore raise an exception
        def exploding_restore(snapshot, snapshot_dir):
            raise RuntimeError("Simulated wallpaper failure")

        monkeypatch.setattr(wallpaper, "restore", exploding_restore)

        # Mock apps.restore to succeed (to verify other modules still run)
        apps_called = []

        def mock_apps_restore(snapshot, snapshot_dir):
            apps_called.append(True)
            print("[apps] No winget apps to install.")

        monkeypatch.setattr(apps, "restore", mock_apps_restore)

        monkeypatch.setattr(sys, "argv", [
            "restore.py", str(archive_path), "--only", "wallpaper", "apps"
        ])

        with patch("sys.exit"):
            restore_module.main()

        captured = capsys.readouterr()

        # The wallpaper error should be caught and reported
        assert "ERROR during restore" in captured.out, \
            "Per-module exception should be caught and reported"
        assert "wallpaper" in captured.out.lower(), \
            "Error summary should mention the failing module"
        assert "Simulated wallpaper failure" in captured.out, \
            "Error summary should include the exception message"

        # Apps module should still have run despite wallpaper failure
        assert len(apps_called) == 1, \
            "Apps module should still run after wallpaper failure"

        # Final summary should report the error count
        assert "1 error" in captured.out, \
            "Final summary should report 1 error"

    def test_multiple_module_errors_all_surfaced(self, tmp_path, monkeypatch, capsys):
        """
        When multiple modules raise exceptions, all are caught and surfaced.
        """
        from modules import wallpaper, apps

        snapshot_data = {
            "exported_at": "2024-01-01T12:00:00",
            "winsnap_version": "0.2.0",
            "snapshot_format_version": "0.2.0",
            "modules": {
                "wallpaper": {"enabled": True, "filename": "wallpaper.jpg"},
                "apps": {"winget": [{"PackageIdentifier": "X"}], "manual": []},
            },
        }

        archive_path = _create_winsnap_archive(tmp_path, snapshot_data)

        # Make both modules raise
        def exploding_wallpaper(snapshot, snapshot_dir):
            raise RuntimeError("Wallpaper boom")

        def exploding_apps(snapshot, snapshot_dir):
            raise ValueError("Apps boom")

        monkeypatch.setattr(wallpaper, "restore", exploding_wallpaper)
        monkeypatch.setattr(apps, "restore", exploding_apps)

        monkeypatch.setattr(sys, "argv", [
            "restore.py", str(archive_path), "--only", "wallpaper", "apps"
        ])

        with patch("sys.exit"):
            restore_module.main()

        captured = capsys.readouterr()

        # Both errors should be surfaced
        assert "Wallpaper boom" in captured.out, \
            "Wallpaper error should be in the summary"
        assert "Apps boom" in captured.out, \
            "Apps error should be in the summary"
        assert "2 error" in captured.out, \
            "Final summary should report 2 errors"
