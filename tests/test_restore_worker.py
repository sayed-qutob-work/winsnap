"""
test_restore_worker.py — Integration tests for RestoreWorker (gui.py).

Tests the RestoreWorker end-to-end behavior with real .winsnap archives
(zip files with snapshot.json) in temp directories, verifying:
- Dry-run applies no changes (no module.restore called)
- Incompatible version halts before modules
- Module skip conditions (absent, export-errored, deselected)
- Successful restore calls module restore functions

Rewritten for gui-backend-alignment Task 7.6: ``RestoreWorker._do_restore()``
is now a thin adapter over restore.py's importable orchestration functions
(``safe_extract``, ``find_snapshot_dir``, ``evaluate_snapshot_version``,
``partition_modules``, ``run_dry_run``, ``run_modules``, ``run_verify``)
instead of a hand-rolled per-module loop. The OLD approach here swapped the
entire ``restore`` module for a bare ``MagicMock()`` via
``patch.dict("sys.modules", {"restore": MagicMock()})`` and then set a
handful of attributes (``SUPPORTED_MAJOR``, ``ALL_MODULES``, ``_summarize``)
on it. That silently replaced every orchestration function above with an
auto-generated MagicMock too (since they were never explicitly re-attached),
so ``safe_extract``/``find_snapshot_dir``/``run_dry_run``/``run_modules``
stopped doing any real extraction, snapshot-dir discovery, or module
dispatch -- which is why these tests broke when Task 5.2 rewired
``_do_restore()`` onto the new call surface.

The fix: import the REAL ``restore`` module and only monkeypatch
``restore.ALL_MODULES`` (plus ``modules.manifest.MODULE_NAMES``, so the
worker's manifest-derived "deselected" loop agrees with the same stub
module universe) to install stub modules -- exactly the pattern
tests/test_restore_worker_adapters.py (Task 5.3) already uses. Every real
orchestration function (safe_extract, find_snapshot_dir,
evaluate_snapshot_version, partition_modules, run_dry_run, run_modules,
run_verify) then runs for real against the stub modules and a real
on-disk .winsnap archive, so these tests exercise the worker's actual wiring
to the backend rather than a fully-mocked stand-in for it.

Validates: Requirements 2.1, 2.2, 2.3, 4.1, 4.3, 11.6 (dry-run/skip/failure
scenarios below also exercise 1.1, 1.6, 5.1, 5.2, 7.1, 7.2).
"""

import json
import os
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

import restore as restore_module
from modules import manifest as manifest_module

from gui import (
    ModuleOutcome,
    ModuleStatus,
    RestoreConfig,
    RestoreWorker,
    ResultsSummary,
    Severity,
)


# ---------------------------------------------------------------------------
# QApplication fixture (needed for signals)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    """Provide a QApplication instance for the test module."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_winsnap_archive(tmp_path: Path, snapshot_data: dict) -> Path:
    """Create a .winsnap zip archive with a snapshot.json inside a subfolder.

    The archive structure mirrors what export.py produces:
        winsnap_test/
            snapshot.json
    """
    snap_folder_name = "winsnap_test"
    content_dir = tmp_path / "archive_content" / snap_folder_name
    content_dir.mkdir(parents=True)

    json_path = content_dir / "snapshot.json"
    json_path.write_text(json.dumps(snapshot_data, indent=2), encoding="utf-8")

    archive_path = tmp_path / "test.winsnap"
    with zipfile.ZipFile(archive_path, "w") as zf:
        for file in content_dir.rglob("*"):
            if file.is_file():
                arcname = str(file.relative_to(tmp_path / "archive_content"))
                zf.write(file, arcname)

    return archive_path


def _install_stub_modules(monkeypatch, stub_map: dict) -> None:
    """Install `stub_map` ({key: module}) as both restore.ALL_MODULES and
    modules.manifest.MODULE_NAMES (same keys, same order) on the REAL
    restore module.

    This is the new monkeypatch target for module substitution (Req 2.1,
    5.1, 5.2), replacing the old whole-module
    ``patch.dict(sys.modules, {"restore": MagicMock()})`` swap, which also
    clobbered safe_extract/find_snapshot_dir/evaluate_snapshot_version/
    partition_modules/run_dry_run/run_modules/run_verify. Mirrors
    tests/test_restore_worker_adapters.py's helper of the same name.
    """
    items = list(stub_map.items())
    monkeypatch.setattr(restore_module, "ALL_MODULES", items)
    monkeypatch.setattr(manifest_module, "MODULE_NAMES", [k for k, _ in items])


class SignalCollector:
    """Collects signals emitted by RestoreWorker for assertion."""

    def __init__(self, worker: RestoreWorker) -> None:
        self.logs: list[tuple[str, Severity]] = []
        self.outcomes: list[ModuleOutcome] = []
        self.summaries: list[ResultsSummary] = []
        self.running_states: list[bool] = []

        worker.log.connect(self._on_log)
        worker.module_completed.connect(self._on_module_completed)
        worker.finished.connect(self._on_finished)
        worker.running_changed.connect(self._on_running_changed)

    def _on_log(self, message: str, severity: Severity) -> None:
        self.logs.append((message, severity))

    def _on_module_completed(self, outcome: ModuleOutcome) -> None:
        self.outcomes.append(outcome)

    def _on_finished(self, summary: ResultsSummary) -> None:
        self.summaries.append(summary)

    def _on_running_changed(self, running: bool) -> None:
        self.running_states.append(running)

    @property
    def log_messages(self) -> list[str]:
        return [msg for msg, _ in self.logs]

    @property
    def error_logs(self) -> list[str]:
        return [msg for msg, sev in self.logs if sev == Severity.ERROR]

    @property
    def warning_logs(self) -> list[str]:
        return [msg for msg, sev in self.logs if sev == Severity.WARNING]


# ---------------------------------------------------------------------------
# Test 1: Dry-run applies no changes
# ---------------------------------------------------------------------------

class TestDryRunAppliesNoChanges:
    """Dry-run mode should emit log messages but never call module.restore.

    The worker's dry-run path now delegates to ``restore.run_dry_run``
    (Req 8.8), which in turn uses the real ``restore._summarize`` -- these
    tests no longer monkeypatch ``_summarize`` and instead assert against
    its real (un-mocked) output text.
    """

    def test_dry_run_does_not_call_module_restore(self, tmp_path, qapp, monkeypatch):
        """RestoreWorker with dry_run=True emits logs but applies no changes."""
        snapshot_data = {
            "exported_at": "2024-01-01T12:00:00",
            "winsnap_version": "0.2.0",
            "snapshot_format_version": "0.2.0",
            "modules": {
                "wallpaper": {"enabled": True, "filename": "wallpaper.jpg"},
                "env_vars": {"PATH": "C:\\bin"},
            },
        }

        archive_path = _create_winsnap_archive(tmp_path, snapshot_data)

        config = RestoreConfig(
            snapshot_path=archive_path,
            dry_run=True,
            selected_modules={"wallpaper", "env_vars"},
        )

        worker = RestoreWorker(config)
        collector = SignalCollector(worker)

        # Mock ALL_MODULES to track calls; monkeypatch target moved from the
        # whole `restore` module to just ALL_MODULES/MODULE_NAMES.
        mock_wallpaper = MagicMock()
        mock_env_vars = MagicMock()
        _install_stub_modules(monkeypatch, {
            "env_vars": mock_env_vars,
            "wallpaper": mock_wallpaper,
        })

        worker.run()

        # Module restore functions should NOT have been called
        mock_wallpaper.restore.assert_not_called()
        mock_env_vars.restore.assert_not_called()

        # Should have emitted log messages about what would be restored
        # (real restore._summarize output, e.g. "would restore N field(s)"
        # for wallpaper and "would restore N environment variable(s)" for
        # env_vars).
        assert any("would restore" in msg for msg in collector.log_messages), \
            "Dry-run should emit summary log messages"

        # All processed modules should be MATCHED (dry-run counts as success;
        # ModuleStatus.PASSED was renamed to ModuleStatus.MATCHED as part of
        # the report-based status vocabulary, Req 1.5).
        matched_outcomes = [o for o in collector.outcomes if o.status == ModuleStatus.MATCHED]
        assert len(matched_outcomes) == 2, \
            "Both selected modules should be MATCHED in dry-run"

        # finished signal should have been emitted
        assert len(collector.summaries) == 1

    def test_dry_run_emits_per_module_summary_text(self, tmp_path, qapp, monkeypatch):
        """Dry-run emits real _summarize text for each selected module."""
        snapshot_data = {
            "exported_at": "2024-01-01T12:00:00",
            "winsnap_version": "0.2.0",
            "snapshot_format_version": "0.2.0",
            "modules": {
                "cursors": {"scheme": "Windows Default"},
            },
        }

        archive_path = _create_winsnap_archive(tmp_path, snapshot_data)

        config = RestoreConfig(
            snapshot_path=archive_path,
            dry_run=True,
            selected_modules={"cursors"},
        )

        worker = RestoreWorker(config)
        collector = SignalCollector(worker)

        mock_cursors = MagicMock()
        _install_stub_modules(monkeypatch, {"cursors": mock_cursors})

        worker.run()

        # restore._summarize special-cases "cursors" as
        # f"would set cursor scheme {data.get('scheme')!r}" -- assert
        # against that real text rather than a monkeypatched stand-in.
        assert any("cursor scheme" in msg for msg in collector.log_messages), \
            "Dry-run should emit _summarize text for cursors module"

        mock_cursors.restore.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: Incompatible version halts before modules
# ---------------------------------------------------------------------------

class TestIncompatibleVersionHalts:
    """Snapshot with incompatible format_version should halt before any
    module runs.

    Version acceptance is now decided by the real
    ``restore.evaluate_snapshot_version`` (Req 7.1, 7.2); since
    ``restore.SUPPORTED_MAJOR`` is already 0 by default, these tests no
    longer need to monkeypatch it.
    """

    def test_incompatible_version_emits_error_and_halts(self, tmp_path, qapp, monkeypatch):
        """Snapshot with format_version '99.0.0' halts before any module runs."""
        snapshot_data = {
            "exported_at": "2024-06-15T10:30:00",
            "winsnap_version": "0.2.0",
            "snapshot_format_version": "99.0.0",
            "modules": {
                "wallpaper": {"enabled": True, "filename": "wallpaper.jpg"},
                "env_vars": {"PATH": "C:\\bin"},
            },
        }

        archive_path = _create_winsnap_archive(tmp_path, snapshot_data)

        config = RestoreConfig(
            snapshot_path=archive_path,
            dry_run=False,
            selected_modules={"wallpaper", "env_vars"},
        )

        worker = RestoreWorker(config)
        collector = SignalCollector(worker)

        mock_wallpaper = MagicMock()
        mock_env_vars = MagicMock()
        _install_stub_modules(monkeypatch, {
            "env_vars": mock_env_vars,
            "wallpaper": mock_wallpaper,
        })

        worker.run()

        # No module restore should have been called
        mock_wallpaper.restore.assert_not_called()
        mock_env_vars.restore.assert_not_called()

        # Should have emitted an error log about incompatible version
        assert len(collector.error_logs) > 0, \
            "Should emit at least one error log for incompatible version"
        assert any("99.0.0" in msg or "newer" in msg for msg in collector.error_logs), \
            "Error log should mention the incompatible version"

        # No module outcomes should have been emitted (halted before
        # resolving the run set / manifest deselected-loop)
        assert len(collector.outcomes) == 0, \
            "No module outcomes should be emitted when halted due to version"

        # finished signal should still be emitted
        assert len(collector.summaries) == 1

    def test_incompatible_version_applies_no_changes(self, tmp_path, qapp, monkeypatch):
        """Incompatible version means zero modules run — no system changes."""
        snapshot_data = {
            "exported_at": "2024-01-01T12:00:00",
            "winsnap_version": "0.2.0",
            "snapshot_format_version": "5.0.0",
            "modules": {
                "power": {"enabled": True, "plan": "balanced"},
            },
        }

        archive_path = _create_winsnap_archive(tmp_path, snapshot_data)

        config = RestoreConfig(
            snapshot_path=archive_path,
            dry_run=False,
            selected_modules={"power"},
        )

        worker = RestoreWorker(config)
        collector = SignalCollector(worker)

        mock_power = MagicMock()
        _install_stub_modules(monkeypatch, {"power": mock_power})

        worker.run()

        mock_power.restore.assert_not_called()

        # Summary should have zero outcomes
        assert len(collector.summaries) == 1
        summary = collector.summaries[0]
        assert len(summary.outcomes) == 0


# ---------------------------------------------------------------------------
# Test 3: Module skip conditions
# ---------------------------------------------------------------------------

class TestModuleSkipConditions:
    """Modules should be skipped with correct reasons for various conditions.

    Skip reasons and wording now come from ``restore.partition_modules`` +
    ``gui.skip_outcome`` (Req 2.2), not an inline loop -- assertions below
    use the current wording those functions actually produce.
    """

    def test_deselected_module_is_skipped(self, tmp_path, qapp, monkeypatch):
        """Module deselected by user → SKIPPED 'Deselected by user'."""
        snapshot_data = {
            "exported_at": "2024-01-01T12:00:00",
            "winsnap_version": "0.2.0",
            "snapshot_format_version": "0.2.0",
            "modules": {
                "wallpaper": {"enabled": True, "filename": "wallpaper.jpg"},
                "env_vars": {"PATH": "C:\\bin"},
            },
        }

        archive_path = _create_winsnap_archive(tmp_path, snapshot_data)

        # Only select env_vars, not wallpaper
        config = RestoreConfig(
            snapshot_path=archive_path,
            dry_run=False,
            selected_modules={"env_vars"},
        )

        worker = RestoreWorker(config)
        collector = SignalCollector(worker)

        mock_wallpaper = MagicMock()
        mock_env_vars = MagicMock()
        # env_vars is selected+present, so run_modules will really call its
        # restore(); give it a valid report dict so report_to_outcome has a
        # real status to classify (dry_run=False → the real restore path).
        mock_env_vars.restore.return_value = {"status": "matched", "items": []}
        _install_stub_modules(monkeypatch, {
            "env_vars": mock_env_vars,
            "wallpaper": mock_wallpaper,
        })

        worker.run()

        # wallpaper should be SKIPPED with "Deselected by user"
        wallpaper_outcomes = [o for o in collector.outcomes if o.name == "wallpaper"]
        assert len(wallpaper_outcomes) == 1
        assert wallpaper_outcomes[0].status == ModuleStatus.SKIPPED
        assert wallpaper_outcomes[0].detail == "Deselected by user"

        # wallpaper.restore should NOT have been called
        mock_wallpaper.restore.assert_not_called()

    def test_absent_module_is_skipped(self, tmp_path, qapp, monkeypatch):
        """Module absent from snapshot → SKIPPED 'Not found in snapshot'."""
        # Snapshot only has wallpaper, not cursors
        snapshot_data = {
            "exported_at": "2024-01-01T12:00:00",
            "winsnap_version": "0.2.0",
            "snapshot_format_version": "0.2.0",
            "modules": {
                "wallpaper": {"enabled": True, "filename": "wallpaper.jpg"},
            },
        }

        archive_path = _create_winsnap_archive(tmp_path, snapshot_data)

        # Select both wallpaper and cursors (cursors is absent from snapshot)
        config = RestoreConfig(
            snapshot_path=archive_path,
            dry_run=False,
            selected_modules={"wallpaper", "cursors"},
        )

        worker = RestoreWorker(config)
        collector = SignalCollector(worker)

        mock_wallpaper = MagicMock()
        # wallpaper is selected+present, so it really runs; give it a valid
        # report dict.
        mock_wallpaper.restore.return_value = {"status": "matched", "items": []}
        mock_cursors = MagicMock()
        _install_stub_modules(monkeypatch, {
            "wallpaper": mock_wallpaper,
            "cursors": mock_cursors,
        })

        worker.run()

        # cursors should be SKIPPED with "Not found in snapshot" (the
        # wording restore.partition_modules'/gui.skip_outcome's
        # "not_found_in_snapshot" reason code maps to)
        cursors_outcomes = [o for o in collector.outcomes if o.name == "cursors"]
        assert len(cursors_outcomes) == 1
        assert cursors_outcomes[0].status == ModuleStatus.SKIPPED
        assert cursors_outcomes[0].detail == "Not found in snapshot"

        # cursors.restore should NOT have been called
        mock_cursors.restore.assert_not_called()

    def test_export_errored_module_is_skipped(self, tmp_path, qapp, monkeypatch):
        """Module with export error in snapshot → SKIPPED 'Was not captured (export error)'."""
        snapshot_data = {
            "exported_at": "2024-01-01T12:00:00",
            "winsnap_version": "0.2.0",
            "snapshot_format_version": "0.2.0",
            "modules": {
                "power": {"error": "Administrator privileges required"},
                "wallpaper": {"enabled": True, "filename": "wallpaper.jpg"},
            },
        }

        archive_path = _create_winsnap_archive(tmp_path, snapshot_data)

        config = RestoreConfig(
            snapshot_path=archive_path,
            dry_run=False,
            selected_modules={"power", "wallpaper"},
        )

        worker = RestoreWorker(config)
        collector = SignalCollector(worker)

        mock_power = MagicMock()
        mock_wallpaper = MagicMock()
        # wallpaper is selected+present (no export error), so it really
        # runs; give it a valid report dict.
        mock_wallpaper.restore.return_value = {"status": "matched", "items": []}
        _install_stub_modules(monkeypatch, {
            "wallpaper": mock_wallpaper,
            "power": mock_power,
        })

        worker.run()

        # power should be SKIPPED with "Was not captured (export error)"
        power_outcomes = [o for o in collector.outcomes if o.name == "power"]
        assert len(power_outcomes) == 1
        assert power_outcomes[0].status == ModuleStatus.SKIPPED
        assert power_outcomes[0].detail == "Was not captured (export error)"

        # power.restore should NOT have been called
        mock_power.restore.assert_not_called()


# ---------------------------------------------------------------------------
# Test 4: Successful restore calls module restore functions
# ---------------------------------------------------------------------------

class TestSuccessfulRestore:
    """Modules that are present and selected should have their restore
    called, with the outcome classified from the returned report dict's
    ``status`` field (Req 1.1) via ``restore.run_modules`` (Req 2.1) -- not
    from whether the call raised.
    """

    def test_selected_present_module_restore_is_called(self, tmp_path, qapp, monkeypatch):
        """A module that is selected, present, and has no export error gets restored."""
        snapshot_data = {
            "exported_at": "2024-01-01T12:00:00",
            "winsnap_version": "0.2.0",
            "snapshot_format_version": "0.2.0",
            "modules": {
                "wallpaper": {"enabled": True, "filename": "wallpaper.jpg"},
            },
        }

        archive_path = _create_winsnap_archive(tmp_path, snapshot_data)

        config = RestoreConfig(
            snapshot_path=archive_path,
            dry_run=False,
            selected_modules={"wallpaper"},
        )

        worker = RestoreWorker(config)
        collector = SignalCollector(worker)

        mock_wallpaper = MagicMock()
        # restore.run_modules expects a report dict back from restore();
        # a bare MagicMock() return value is not a valid report shape.
        mock_wallpaper.restore.return_value = {"status": "matched", "items": []}
        _install_stub_modules(monkeypatch, {"wallpaper": mock_wallpaper})

        worker.run()

        # wallpaper.restore should have been called
        mock_wallpaper.restore.assert_called_once()

        # The outcome should be MATCHED (renamed from PASSED, Req 1.5)
        wallpaper_outcomes = [o for o in collector.outcomes if o.name == "wallpaper"]
        assert len(wallpaper_outcomes) == 1
        assert wallpaper_outcomes[0].status == ModuleStatus.MATCHED

    def test_module_raising_exception_is_failed(self, tmp_path, qapp, monkeypatch):
        """A module that raises during restore is classified as FAILED.

        restore.run_modules catches the exception and synthesizes a
        ``{"status": "failed", "reason": str(e)}`` report rather than
        letting it propagate (Req 1.6), and the run continues.
        """
        snapshot_data = {
            "exported_at": "2024-01-01T12:00:00",
            "winsnap_version": "0.2.0",
            "snapshot_format_version": "0.2.0",
            "modules": {
                "wallpaper": {"enabled": True, "filename": "wallpaper.jpg"},
            },
        }

        archive_path = _create_winsnap_archive(tmp_path, snapshot_data)

        config = RestoreConfig(
            snapshot_path=archive_path,
            dry_run=False,
            selected_modules={"wallpaper"},
        )

        worker = RestoreWorker(config)
        collector = SignalCollector(worker)

        mock_wallpaper = MagicMock()
        mock_wallpaper.restore.side_effect = RuntimeError("Simulated failure")
        _install_stub_modules(monkeypatch, {"wallpaper": mock_wallpaper})

        worker.run()

        # wallpaper.restore was called but raised
        mock_wallpaper.restore.assert_called_once()

        # The outcome should be FAILED with the exception text as the
        # synthesized report's "reason"
        wallpaper_outcomes = [o for o in collector.outcomes if o.name == "wallpaper"]
        assert len(wallpaper_outcomes) == 1
        assert wallpaper_outcomes[0].status == ModuleStatus.FAILED
        assert "Simulated failure" in wallpaper_outcomes[0].detail
