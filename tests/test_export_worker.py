"""Integration tests for ExportWorker.

Tests verify end-to-end behavior of the ExportWorker including:
- Archive creation with monkeypatched module export() functions
- Fatal-error cleanup (no partial archive left)
- Admin warning emission for the power module
- Module failures don't stop the operation (other modules still run)

Tests run with QT_QPA_PLATFORM=offscreen so no display is required.
ExportWorker.run() is called directly (not via QThread) for testing.

ExportWorker.run() is now a thin adapter over export.py's importable
pipeline functions (export.resolve_snapshot_dir, export.run_export_modules,
export.build_snapshot_metadata, export.write_snapshot_json,
export.zip_snapshot, export.cleanup_snapshot_dir), with module resolution
via export._build_modules (manifest order) filtered by
config.selected_modules. Tests here monkeypatch individual modules'
export() functions (the actual seam _build_modules reads at call time) and
export.py's pipeline functions directly, rather than the old
importlib.import_module()/export.create_snapshot_dir seams the rewritten
worker no longer touches -- create_snapshot_dir is now only reached
*inside* resolve_snapshot_dir, on the unnamed-export branch.

Collision fail-fast, force-overwrite, and snapshot-metadata-builder parity
coverage now live in tests/test_export_worker_adapters.py (added by task
5.3); this file's scope is the archive-creation happy path, per-module
failure isolation, fatal-error cleanup, and the admin warning.

Requirements: 6.1, 7.1, 7.2, 7.5, 11.6
"""

import os
import sys
from pathlib import Path

# Force offscreen rendering for headless CI
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

# Ensure a QApplication exists before any QObject tests
_app = QApplication.instance() or QApplication(sys.argv)

import pytest

import export as export_module
import modules.wallpaper as wallpaper_module
import modules.mouse_display as mouse_display_module
import modules.power as power_module

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
# Test: Archive creation with monkeypatched modules (Requirement 7.1, 7.2)
# ---------------------------------------------------------------------------


class TestExportWorkerArchiveCreation:
    """Test that ExportWorker creates a .winsnap archive when modules succeed."""

    def test_creates_archive_on_success(self, tmp_path, monkeypatch):
        """ExportWorker should create a .winsnap archive when modules succeed.

        Validates: Requirements 7.1, 7.2
        """
        config = _make_config(tmp_path, selected_modules={"wallpaper", "mouse_display"})
        bridge = AppSelectionBridge()
        worker = ExportWorker(config, bridge)
        collected = _collect_signals(worker)

        monkeypatch.setattr(
            wallpaper_module, "export",
            lambda d: {"enabled": True, "filename": "wallpaper.jpg"},
        )
        monkeypatch.setattr(mouse_display_module, "export", lambda d: {"speed": 10})

        worker.run()

        # With name="test_snapshot", resolve_snapshot_dir resolves to
        # tmp_path/test_snapshot and the real (unmocked) zip_snapshot
        # appends ".winsnap" -- this is a genuine end-to-end zip.
        zip_path = tmp_path / "test_snapshot.winsnap"
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

        # Verify both modules matched (ModuleStatus.PASSED was removed;
        # the success status is now ModuleStatus.MATCHED)
        matched_names = [o.name for o in summary.matched()]
        assert "wallpaper" in matched_names
        assert "mouse_display" in matched_names

    def test_module_failure_does_not_stop_operation(self, tmp_path, monkeypatch):
        """A module failure should not prevent other modules from running.

        Validates: Requirement 7.3
        """
        config = _make_config(tmp_path, selected_modules={"wallpaper", "mouse_display"})
        bridge = AppSelectionBridge()
        worker = ExportWorker(config, bridge)
        collected = _collect_signals(worker)

        def failing_export(snapshot_dir):
            raise RuntimeError("wallpaper failed")

        # wallpaper raises, mouse_display succeeds. run_export_modules
        # catches the raise per-module and synthesizes {"error": str(e)},
        # so the failure is classified via the result dict's "error" key,
        # not via an uncaught exception propagating out of the worker.
        monkeypatch.setattr(wallpaper_module, "export", failing_export)
        monkeypatch.setattr(mouse_display_module, "export", lambda d: {"speed": 10})

        worker.run()

        # Verify both modules were attempted
        summary = collected["finished"][0]
        all_names = [o.name for o in summary.outcomes]
        assert "wallpaper" in all_names
        assert "mouse_display" in all_names

        # wallpaper should be FAILED, mouse_display should be MATCHED
        failed_names = [o.name for o in summary.failed()]
        matched_names = [o.name for o in summary.matched()]
        assert "wallpaper" in failed_names
        assert "mouse_display" in matched_names

        # Archive should still be created
        zip_path = tmp_path / "test_snapshot.winsnap"
        assert zip_path.exists()


# ---------------------------------------------------------------------------
# Test: Fatal-error cleanup (Requirement 7.5)
# ---------------------------------------------------------------------------


class TestExportWorkerFatalErrorCleanup:
    """Test that on fatal error, no partial archive is left."""

    def test_no_partial_archive_on_fatal_error(self, tmp_path, monkeypatch):
        """When a fatal error occurs, no partial .winsnap archive should remain.

        Validates: Requirement 7.5
        """
        config = _make_config(tmp_path, selected_modules={"wallpaper"})
        bridge = AppSelectionBridge()
        worker = ExportWorker(config, bridge)
        collected = _collect_signals(worker)

        # Make resolve_snapshot_dir raise to simulate output dir not
        # writable -- this is the worker's actual snapshot-dir-resolution
        # seam now (create_snapshot_dir is only reached from inside it).
        def _raise_permission_error(*args, **kwargs):
            raise PermissionError("Output dir not writable")

        monkeypatch.setattr(export_module, "resolve_snapshot_dir", _raise_permission_error)

        worker.run()

        # Verify no .winsnap files exist in the output directory
        winsnap_files = list(tmp_path.glob("*.winsnap"))
        assert len(winsnap_files) == 0, "No partial archive should remain after fatal error"

        # Verify error log was emitted
        error_logs = [(msg, sev) for msg, sev in collected["logs"] if sev == Severity.ERROR]
        assert len(error_logs) > 0, "Expected an error log on fatal failure"

        # Verify finished signal was still emitted (so UI can re-enable controls)
        assert len(collected["finished"]) == 1

    def test_snapshot_dir_cleaned_on_zip_failure(self, tmp_path, monkeypatch):
        """If zip_snapshot fails, the snapshot directory should be cleaned up.

        Validates: Requirement 7.5
        """
        config = _make_config(tmp_path, selected_modules={"wallpaper"})
        bridge = AppSelectionBridge()
        worker = ExportWorker(config, bridge)
        collected = _collect_signals(worker)

        monkeypatch.setattr(wallpaper_module, "export", lambda d: {"enabled": True})

        def failing_zip(sdir):
            raise OSError("Disk full")

        monkeypatch.setattr(export_module, "zip_snapshot", failing_zip)

        worker.run()

        # zip_snapshot raising leaves zip_path unset (the assignment never
        # completes) but snapshot_dir was already created by the real
        # resolve_snapshot_dir call -- the outer fatal-error handler must
        # rmtree it.
        snapshot_dir = tmp_path / "test_snapshot"
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

    def test_admin_warning_emitted_when_not_admin(self, tmp_path, monkeypatch):
        """When power is selected and process is not admin, a warning log should be emitted.

        Validates: Requirement 6.1
        """
        config = _make_config(tmp_path, selected_modules={"power"})
        bridge = AppSelectionBridge()
        worker = ExportWorker(config, bridge)
        collected = _collect_signals(worker)

        # Power module returns not_admin skip result
        monkeypatch.setattr(
            power_module, "export",
            lambda d: {"enabled": False, "skip_reason": "not_admin"},
        )
        monkeypatch.setattr(worker, "_is_admin", lambda: False)

        worker.run()

        # Verify a warning log was emitted
        warning_logs = [(msg, sev) for msg, sev in collected["logs"] if sev == Severity.WARNING]
        assert len(warning_logs) > 0, "Expected a warning log about admin privileges"

        # Check the warning message content
        admin_warnings = [msg for msg, sev in warning_logs
                          if "administrator" in msg.lower() or "power" in msg.lower()]
        assert len(admin_warnings) > 0, (
            "Expected warning about power plan capture being skipped due to lack of admin"
        )

    def test_no_admin_warning_when_admin(self, tmp_path, monkeypatch):
        """When process IS admin, no admin warning should be emitted for power module.

        Validates: Requirement 6.1 (inverse case)
        """
        config = _make_config(tmp_path, selected_modules={"power"})
        bridge = AppSelectionBridge()
        worker = ExportWorker(config, bridge)
        collected = _collect_signals(worker)

        monkeypatch.setattr(
            power_module, "export",
            lambda d: {"enabled": True, "plan": "balanced"},
        )
        monkeypatch.setattr(worker, "_is_admin", lambda: True)

        worker.run()

        # No admin-related warning should be emitted
        admin_warnings = [msg for msg, sev in collected["logs"]
                          if sev == Severity.WARNING and "administrator" in msg.lower()]
        assert len(admin_warnings) == 0, "No admin warning expected when running as admin"

    def test_no_admin_warning_when_power_not_selected(self, tmp_path, monkeypatch):
        """When power is NOT in the selected modules, no admin warning should be emitted.

        Validates: Requirement 6.1 (power not selected case)
        """
        config = _make_config(tmp_path, selected_modules={"wallpaper"})
        bridge = AppSelectionBridge()
        worker = ExportWorker(config, bridge)
        collected = _collect_signals(worker)

        monkeypatch.setattr(wallpaper_module, "export", lambda d: {"enabled": True})
        monkeypatch.setattr(worker, "_is_admin", lambda: False)

        worker.run()

        # No admin-related warning should be emitted
        admin_warnings = [msg for msg, sev in collected["logs"]
                          if sev == Severity.WARNING and "power" in msg.lower()]
        assert len(admin_warnings) == 0, "No admin warning expected when power not selected"
