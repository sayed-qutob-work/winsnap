"""Integration tests for ExportWorker.

Tests verify end-to-end behavior of the ExportWorker including:
- Archive creation with mocked modules
- Fatal-error cleanup (no partial archive left)
- Admin warning emission for the power module
- Module failures don't stop the operation (other modules still run)

Tests run with QT_QPA_PLATFORM=offscreen so no display is required.
ExportWorker.run() is called directly (not via QThread) for testing.

Requirements: 7.1, 7.2, 7.5, 6.1
"""

import os
import sys
import json
import zipfile
from pathlib import Path
from unittest.mock import patch, MagicMock

# Force offscreen rendering for headless CI
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

# Ensure a QApplication exists before any QObject tests
_app = QApplication.instance() or QApplication(sys.argv)

import pytest

from gui import (
    ExportWorker,
    ExportConfig,
    AppSelectionBridge,
    Severity,
    ModuleStatus,
    ResultsSummary,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(output_dir: Path, selected_modules: set[str] | None = None) -> ExportConfig:
    """Create an ExportConfig for testing."""
    if selected_modules is None:
        selected_modules = {"wallpaper", "mouse_display"}
    return ExportConfig(
        output_dir=output_dir,
        name="test_snapshot",
        show_all=False,
        selected_modules=selected_modules,
    )


def _collect_signals(worker: ExportWorker) -> dict:
    """Connect to all worker signals and collect emitted values."""
    collected = {
        "logs": [],
        "module_completed": [],
        "finished": [],
        "running_changed": [],
    }
    worker.log.connect(lambda msg, sev: collected["logs"].append((msg, sev)))
    worker.module_completed.connect(lambda o: collected["module_completed"].append(o))
    worker.finished.connect(lambda s: collected["finished"].append(s))
    worker.running_changed.connect(lambda b: collected["running_changed"].append(b))
    return collected


# ---------------------------------------------------------------------------
# Test: Archive creation with mocked modules (Requirement 7.1, 7.2)
# ---------------------------------------------------------------------------


class TestExportWorkerArchiveCreation:
    """Test that ExportWorker creates a .winsnap archive when modules succeed."""

    def test_creates_archive_on_success(self, tmp_path):
        """ExportWorker should create a .winsnap archive when modules succeed.

        Validates: Requirements 7.1, 7.2
        """
        config = _make_config(tmp_path, selected_modules={"wallpaper", "mouse_display"})
        bridge = AppSelectionBridge()
        worker = ExportWorker(config, bridge)
        collected = _collect_signals(worker)

        # Mock create_snapshot_dir to return a temp dir
        snapshot_dir = tmp_path / "test_snapshot"
        snapshot_dir.mkdir()

        # Mock zip_snapshot to create a dummy zip
        zip_path = tmp_path / "test_snapshot.winsnap"

        def fake_zip_snapshot(sdir):
            with zipfile.ZipFile(zip_path, "w") as zf:
                for f in sdir.rglob("*"):
                    if f.is_file():
                        zf.write(f, f.relative_to(sdir.parent))
            return zip_path

        # Mock modules to return simple dicts
        fake_wallpaper = MagicMock()
        fake_wallpaper.export = MagicMock(return_value={"enabled": True, "filename": "wallpaper.jpg"})

        fake_mouse_display = MagicMock()
        fake_mouse_display.export = MagicMock(return_value={"speed": 10})

        with patch("export.create_snapshot_dir", return_value=snapshot_dir), \
             patch("export.zip_snapshot", side_effect=fake_zip_snapshot), \
             patch("importlib.import_module") as mock_import:

            def import_side_effect(name):
                if name == "modules.wallpaper":
                    return fake_wallpaper
                elif name == "modules.mouse_display":
                    return fake_mouse_display
                raise ImportError(f"No module named {name}")

            mock_import.side_effect = import_side_effect

            worker.run()

        # Verify archive was created
        assert zip_path.exists(), "Expected .winsnap archive to be created"

        # Verify success log with archive path was emitted (Requirement 7.2)
        log_messages = [msg for msg, sev in collected["logs"]]
        archive_log = [msg for msg in log_messages if str(zip_path) in msg]
        assert len(archive_log) > 0, "Expected a success log containing the archive path"

        # Verify format version log was emitted
        version_log = [msg for msg in log_messages if "Format version" in msg]
        assert len(version_log) > 0, "Expected a log entry with the format version"

        # Verify finished signal was emitted with a ResultsSummary
        assert len(collected["finished"]) == 1
        summary = collected["finished"][0]
        assert isinstance(summary, ResultsSummary)

        # Verify both modules passed
        passed_names = [o.name for o in summary.passed()]
        assert "wallpaper" in passed_names
        assert "mouse_display" in passed_names

    def test_module_failure_does_not_stop_operation(self, tmp_path):
        """A module failure should not prevent other modules from running.

        Validates: Requirement 7.3
        """
        config = _make_config(tmp_path, selected_modules={"wallpaper", "mouse_display"})
        bridge = AppSelectionBridge()
        worker = ExportWorker(config, bridge)
        collected = _collect_signals(worker)

        snapshot_dir = tmp_path / "test_snapshot"
        snapshot_dir.mkdir()

        zip_path = tmp_path / "test_snapshot.winsnap"

        def fake_zip_snapshot(sdir):
            with zipfile.ZipFile(zip_path, "w") as zf:
                for f in sdir.rglob("*"):
                    if f.is_file():
                        zf.write(f, f.relative_to(sdir.parent))
            return zip_path

        # wallpaper raises, mouse_display succeeds
        fake_wallpaper = MagicMock()
        fake_wallpaper.export = MagicMock(side_effect=RuntimeError("wallpaper failed"))

        fake_mouse_display = MagicMock()
        fake_mouse_display.export = MagicMock(return_value={"speed": 10})

        with patch("export.create_snapshot_dir", return_value=snapshot_dir), \
             patch("export.zip_snapshot", side_effect=fake_zip_snapshot), \
             patch("importlib.import_module") as mock_import:

            def import_side_effect(name):
                if name == "modules.wallpaper":
                    return fake_wallpaper
                elif name == "modules.mouse_display":
                    return fake_mouse_display
                raise ImportError(f"No module named {name}")

            mock_import.side_effect = import_side_effect

            worker.run()

        # Verify both modules were attempted
        summary = collected["finished"][0]
        all_names = [o.name for o in summary.outcomes]
        assert "wallpaper" in all_names
        assert "mouse_display" in all_names

        # wallpaper should be FAILED, mouse_display should be PASSED
        failed_names = [o.name for o in summary.failed()]
        passed_names = [o.name for o in summary.passed()]
        assert "wallpaper" in failed_names
        assert "mouse_display" in passed_names

        # Archive should still be created
        assert zip_path.exists()


# ---------------------------------------------------------------------------
# Test: Fatal-error cleanup (Requirement 7.5)
# ---------------------------------------------------------------------------


class TestExportWorkerFatalErrorCleanup:
    """Test that on fatal error, no partial archive is left."""

    def test_no_partial_archive_on_fatal_error(self, tmp_path):
        """When a fatal error occurs, no partial .winsnap archive should remain.

        Validates: Requirement 7.5
        """
        config = _make_config(tmp_path, selected_modules={"wallpaper"})
        bridge = AppSelectionBridge()
        worker = ExportWorker(config, bridge)
        collected = _collect_signals(worker)

        # Make create_snapshot_dir raise to simulate output dir not writable
        with patch("export.create_snapshot_dir", side_effect=PermissionError("Output dir not writable")):
            worker.run()

        # Verify no .winsnap files exist in the output directory
        winsnap_files = list(tmp_path.glob("*.winsnap"))
        assert len(winsnap_files) == 0, "No partial archive should remain after fatal error"

        # Verify error log was emitted
        error_logs = [(msg, sev) for msg, sev in collected["logs"] if sev == Severity.ERROR]
        assert len(error_logs) > 0, "Expected an error log on fatal failure"

        # Verify finished signal was still emitted (so UI can re-enable controls)
        assert len(collected["finished"]) == 1

    def test_snapshot_dir_cleaned_on_zip_failure(self, tmp_path):
        """If zip_snapshot fails, the snapshot directory should be cleaned up.

        Validates: Requirement 7.5
        """
        config = _make_config(tmp_path, selected_modules={"wallpaper"})
        bridge = AppSelectionBridge()
        worker = ExportWorker(config, bridge)
        collected = _collect_signals(worker)

        snapshot_dir = tmp_path / "test_snapshot"
        snapshot_dir.mkdir()

        def failing_zip(sdir):
            raise OSError("Disk full")

        fake_wallpaper = MagicMock()
        fake_wallpaper.export = MagicMock(return_value={"enabled": True})

        with patch("export.create_snapshot_dir", return_value=snapshot_dir), \
             patch("export.zip_snapshot", side_effect=failing_zip), \
             patch("importlib.import_module") as mock_import:

            mock_import.return_value = fake_wallpaper

            worker.run()

        # The snapshot directory should have been cleaned up
        assert not snapshot_dir.exists(), "Snapshot dir should be removed on fatal error"

        # No .winsnap files should exist
        winsnap_files = list(tmp_path.glob("*.winsnap"))
        assert len(winsnap_files) == 0, "No archive should exist after fatal error"

        # Verify error log was emitted
        error_logs = [(msg, sev) for msg, sev in collected["logs"] if sev == Severity.ERROR]
        assert len(error_logs) > 0

        # Verify finished signal was emitted
        assert len(collected["finished"]) == 1


# ---------------------------------------------------------------------------
# Test: Admin warning emission for power module (Requirement 6.1)
# ---------------------------------------------------------------------------


class TestExportWorkerAdminWarning:
    """Test that a warning is emitted when power module is selected without admin."""

    def test_admin_warning_emitted_when_not_admin(self, tmp_path):
        """When power is selected and process is not admin, a warning log should be emitted.

        Validates: Requirement 6.1
        """
        config = _make_config(tmp_path, selected_modules={"power"})
        bridge = AppSelectionBridge()
        worker = ExportWorker(config, bridge)
        collected = _collect_signals(worker)

        snapshot_dir = tmp_path / "test_snapshot"
        snapshot_dir.mkdir()

        zip_path = tmp_path / "test_snapshot.winsnap"

        def fake_zip_snapshot(sdir):
            with zipfile.ZipFile(zip_path, "w") as zf:
                for f in sdir.rglob("*"):
                    if f.is_file():
                        zf.write(f, f.relative_to(sdir.parent))
            return zip_path

        # Power module returns not_admin skip result
        fake_power = MagicMock()
        fake_power.export = MagicMock(return_value={"enabled": False, "skip_reason": "not_admin"})

        with patch("export.create_snapshot_dir", return_value=snapshot_dir), \
             patch("export.zip_snapshot", side_effect=fake_zip_snapshot), \
             patch("importlib.import_module") as mock_import, \
             patch.object(worker, "_is_admin", return_value=False):

            mock_import.return_value = fake_power

            worker.run()

        # Verify a warning log was emitted BEFORE the power module runs
        warning_logs = [(msg, sev) for msg, sev in collected["logs"] if sev == Severity.WARNING]
        assert len(warning_logs) > 0, "Expected a warning log about admin privileges"

        # Check the warning message content
        admin_warnings = [msg for msg, sev in warning_logs
                          if "administrator" in msg.lower() or "power" in msg.lower()]
        assert len(admin_warnings) > 0, (
            "Expected warning about power plan capture being skipped due to lack of admin"
        )

    def test_no_admin_warning_when_admin(self, tmp_path):
        """When process IS admin, no admin warning should be emitted for power module.

        Validates: Requirement 6.1 (inverse case)
        """
        config = _make_config(tmp_path, selected_modules={"power"})
        bridge = AppSelectionBridge()
        worker = ExportWorker(config, bridge)
        collected = _collect_signals(worker)

        snapshot_dir = tmp_path / "test_snapshot"
        snapshot_dir.mkdir()

        zip_path = tmp_path / "test_snapshot.winsnap"

        def fake_zip_snapshot(sdir):
            with zipfile.ZipFile(zip_path, "w") as zf:
                for f in sdir.rglob("*"):
                    if f.is_file():
                        zf.write(f, f.relative_to(sdir.parent))
            return zip_path

        fake_power = MagicMock()
        fake_power.export = MagicMock(return_value={"enabled": True, "plan": "balanced"})

        with patch("export.create_snapshot_dir", return_value=snapshot_dir), \
             patch("export.zip_snapshot", side_effect=fake_zip_snapshot), \
             patch("importlib.import_module") as mock_import, \
             patch.object(worker, "_is_admin", return_value=True):

            mock_import.return_value = fake_power

            worker.run()

        # No admin-related warning should be emitted
        admin_warnings = [msg for msg, sev in collected["logs"]
                          if sev == Severity.WARNING and "administrator" in msg.lower()]
        assert len(admin_warnings) == 0, "No admin warning expected when running as admin"

    def test_no_admin_warning_when_power_not_selected(self, tmp_path):
        """When power is NOT in the selected modules, no admin warning should be emitted.

        Validates: Requirement 6.1 (power not selected case)
        """
        config = _make_config(tmp_path, selected_modules={"wallpaper"})
        bridge = AppSelectionBridge()
        worker = ExportWorker(config, bridge)
        collected = _collect_signals(worker)

        snapshot_dir = tmp_path / "test_snapshot"
        snapshot_dir.mkdir()

        zip_path = tmp_path / "test_snapshot.winsnap"

        def fake_zip_snapshot(sdir):
            with zipfile.ZipFile(zip_path, "w") as zf:
                for f in sdir.rglob("*"):
                    if f.is_file():
                        zf.write(f, f.relative_to(sdir.parent))
            return zip_path

        fake_wallpaper = MagicMock()
        fake_wallpaper.export = MagicMock(return_value={"enabled": True})

        with patch("export.create_snapshot_dir", return_value=snapshot_dir), \
             patch("export.zip_snapshot", side_effect=fake_zip_snapshot), \
             patch("importlib.import_module") as mock_import, \
             patch.object(worker, "_is_admin", return_value=False):

            mock_import.return_value = fake_wallpaper

            worker.run()

        # No admin-related warning should be emitted
        admin_warnings = [msg for msg, sev in collected["logs"]
                          if sev == Severity.WARNING and "power" in msg.lower()]
        assert len(admin_warnings) == 0, "No admin warning expected when power not selected"
