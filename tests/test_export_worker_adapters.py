"""
test_export_worker_adapters.py — Integration tests for the rewritten
ExportWorker (gui.py Task 5.1), which is a thin adapter over export.py's
importable pipeline functions (resolve_snapshot_dir, run_export_modules,
build_snapshot_metadata, write_snapshot_json, zip_snapshot,
cleanup_snapshot_dir).

worker.run() is called directly (not via QThread), mirroring the existing
test_export_worker.py pattern. This is new coverage added by
gui-backend-alignment Task 5.3 (distinct from task 7.7, which will rewrite
the *old*, pre-rewrite tests/test_export_worker.py's API usage separately).

Requirements: 1.6, 2.3, 3.2, 3.5, 4.2, 4.4, 4.5, 6.2, 6.4, 7.2, 9.3, 9.4,
10.2, 10.4, 11.6
"""

import json
import os
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(sys.argv)

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import export as export_module
import modules.wallpaper as wallpaper_module
import modules.apps as apps_module
import modules.checklist as checklist_module

from gui import (
    AppSelectionBridge,
    ExportConfig,
    ExportWorker,
    ModuleStatus,
    Severity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(tmp_path: Path, *, name: str | None, force: bool = False,
                  selected_modules=None) -> ExportConfig:
    return ExportConfig(
        output_dir=tmp_path,
        name=name,
        show_all=False,
        selected_modules=selected_modules or {"wallpaper"},
        force=force,
    )


def _collect_signals(worker: ExportWorker) -> dict:
    collected = {"logs": [], "module_completed": [], "finished": [], "running_changed": []}
    worker.log.connect(lambda msg, sev: collected["logs"].append((msg, sev)))
    worker.module_completed.connect(lambda o: collected["module_completed"].append(o))
    worker.finished.connect(lambda s: collected["finished"].append(s))
    worker.running_changed.connect(lambda b: collected["running_changed"].append(b))
    return collected


class _FixedDatetime(datetime):
    """A datetime subclass with a frozen .now(), so a worker-produced
    snapshot's exported_at can be compared field-for-field against a
    directly-called build_snapshot_metadata()'s exported_at instead of
    merely both being "close in time"."""

    @classmethod
    def now(cls, tz=None):
        return cls(2024, 6, 15, 9, 30, 0)


def _read_snapshot_json_from_zip(zip_path: Path) -> dict:
    with zipfile.ZipFile(zip_path) as zf:
        json_name = next(n for n in zf.namelist() if n.endswith("snapshot.json"))
        return json.loads(zf.read(json_name))


# ---------------------------------------------------------------------------
# Collision fail-fast: no module executes (Req 9.3)
# ---------------------------------------------------------------------------


class TestCollisionFailFast:
    def test_no_module_executes_when_preflight_check_fails_and_user_declines(
            self, tmp_path, monkeypatch
    ):
        """When resolve_snapshot_dir raises FileExistsError (force=False --
        the "user declined to overwrite" case), the worker must fail fast
        before running any module. This exercises the worker's own
        belt-and-suspenders resolve_snapshot_dir call directly (Task 6's
        MainWindow-level pre-check is a separate, not-yet-built layer)."""
        run_export_modules_mock = MagicMock()
        monkeypatch.setattr(export_module, "run_export_modules", run_export_modules_mock)
        monkeypatch.setattr(
            export_module, "resolve_snapshot_dir",
            MagicMock(side_effect=FileExistsError(
                "Snapshot destination already exists: mysnap. "
                "Use --force to overwrite, or pick a different --name."
            )),
        )

        config = _make_config(tmp_path, name="mysnap", force=False)
        bridge = AppSelectionBridge()
        worker = ExportWorker(config, bridge)
        collected = _collect_signals(worker)
        worker.run()

        run_export_modules_mock.assert_not_called()
        assert any(sev == Severity.ERROR for _, sev in collected["logs"])
        assert any("already exists" in msg for msg, _ in collected["logs"])
        assert len(collected["finished"]) == 1
        assert collected["finished"][0].outcomes == []
        # No archive should have been produced.
        assert list(tmp_path.glob("*.winsnap")) == []


# ---------------------------------------------------------------------------
# Force-overwrite path re-invokes with force=True and succeeds (Req 9.3, 9.4)
# ---------------------------------------------------------------------------


class TestForceOverwritePath:
    def test_force_overwrite_deletes_collision_and_succeeds(self, tmp_path, monkeypatch):
        # Pre-existing collision at the resolved destination: a leftover
        # unzipped snapshot folder from a previous export.
        existing_dir = tmp_path / "mysnap"
        existing_dir.mkdir()
        (existing_dir / "leftover.txt").write_text("stale", encoding="utf-8")

        resolve_spy = MagicMock(wraps=export_module.resolve_snapshot_dir)
        monkeypatch.setattr(export_module, "resolve_snapshot_dir", resolve_spy)
        monkeypatch.setattr(wallpaper_module, "export", lambda d: {"enabled": True})

        config = _make_config(tmp_path, name="mysnap", force=True,
                               selected_modules={"wallpaper"})
        bridge = AppSelectionBridge()
        worker = ExportWorker(config, bridge)
        collected = _collect_signals(worker)
        worker.run()

        # resolve_snapshot_dir was re-invoked with force=True.
        resolve_spy.assert_called_once_with(tmp_path, "mysnap", True)

        zip_path = tmp_path / "mysnap.winsnap"
        assert zip_path.exists(), "expected the export to succeed and produce an archive"

        error_logs = [msg for msg, sev in collected["logs"] if sev == Severity.ERROR]
        assert error_logs == [], f"expected no errors, got: {error_logs}"

        # The stale leftover content must be gone -- overwritten, not merged.
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        assert not any("leftover.txt" in n for n in names)

        outcomes = collected["finished"][0].outcomes
        wallpaper_outcome = next(o for o in outcomes if o.name == "wallpaper")
        assert wallpaper_outcome.status == ModuleStatus.MATCHED


# ---------------------------------------------------------------------------
# Worker-produced snapshot.json metadata is field-for-field identical to
# calling build_snapshot_metadata directly (Req 10.2)
# ---------------------------------------------------------------------------


class TestSnapshotMetadataParity:
    def test_metadata_matches_direct_build_snapshot_metadata_call(self, tmp_path, monkeypatch):
        monkeypatch.setattr(export_module, "datetime", _FixedDatetime)
        monkeypatch.setattr(wallpaper_module, "export", lambda d: {"enabled": True})

        config = _make_config(tmp_path, name=None, force=False,
                               selected_modules={"wallpaper"})
        bridge = AppSelectionBridge()
        worker = ExportWorker(config, bridge)
        collected = _collect_signals(worker)
        worker.run()

        zip_files = list(tmp_path.glob("*.winsnap"))
        assert len(zip_files) == 1
        written = _read_snapshot_json_from_zip(zip_files[0])

        direct = export_module.build_snapshot_metadata(modules_attempted=["wallpaper"])

        # "modules" is populated by the worker AFTER build_snapshot_metadata
        # returns (Req 10.1's documented pattern: modules starts empty and
        # callers fill it in), so it is excluded from the identity check --
        # every OTHER field must match field-for-field, including
        # exported_at (both calls run under the same frozen clock).
        written_metadata = {k: v for k, v in written.items() if k != "modules"}
        direct_metadata = {k: v for k, v in direct.items() if k != "modules"}
        assert written_metadata == direct_metadata

    def test_metadata_key_set_matches_named_export_too(self, tmp_path, monkeypatch):
        """Same parity check, but for a named (not timestamp-default) export,
        since resolve_snapshot_dir's two branches (create_snapshot_dir vs.
        resolve_output_path) must not affect metadata construction."""
        monkeypatch.setattr(export_module, "datetime", _FixedDatetime)
        monkeypatch.setattr(wallpaper_module, "export", lambda d: {"enabled": True})

        config = _make_config(tmp_path, name="named_snap", force=False,
                               selected_modules={"wallpaper"})
        bridge = AppSelectionBridge()
        worker = ExportWorker(config, bridge)
        _collect_signals(worker)
        worker.run()

        zip_path = tmp_path / "named_snap.winsnap"
        assert zip_path.exists()
        written = _read_snapshot_json_from_zip(zip_path)

        direct = export_module.build_snapshot_metadata(modules_attempted=["wallpaper"])

        written_metadata = {k: v for k, v in written.items() if k != "modules"}
        direct_metadata = {k: v for k, v in direct.items() if k != "modules"}
        assert written_metadata == direct_metadata


# ---------------------------------------------------------------------------
# apps / checklist.run monkeypatch + AppSelectionBridge None-on-cancel
# (Req 10.4)
# ---------------------------------------------------------------------------


class TestAppsChecklistBridgeCancel:
    def test_checklist_monkeypatch_and_none_on_cancel_produce_empty_matched_result(
            self, tmp_path, monkeypatch
    ):
        """The worker replaces modules.checklist.run with
        bridge.request_app_selection for the duration of the export
        (restoring it in a finally), and apps.export's None-on-cancel
        handling (checklist.run returning None -> empty winget/manual
        lists, no error) must continue to work unchanged through that
        monkeypatch."""
        monkeypatch.setattr(apps_module, "_export_winget", lambda snapshot_dir: ([], None))
        monkeypatch.setattr(apps_module, "_scan_registry_apps", lambda show_all=False: [])

        original_checklist_run = checklist_module.run

        config = _make_config(tmp_path, name=None, force=False, selected_modules={"apps"})
        bridge = AppSelectionBridge()
        # Simulate the UI thread immediately cancelling the app-selector
        # dialog: connecting to app_selection_requested and calling
        # provide_result(None) resolves synchronously (direct connection,
        # same thread), unblocking request_app_selection's event.wait().
        bridge.app_selection_requested.connect(lambda winget, manual: bridge.provide_result(None))

        worker = ExportWorker(config, bridge)
        collected = _collect_signals(worker)
        worker.run()

        # checklist.run is restored to the original after the worker
        # finishes, even on the cancel path.
        assert checklist_module.run is original_checklist_run

        error_logs = [msg for msg, sev in collected["logs"] if sev == Severity.ERROR]
        assert error_logs == [], f"expected no errors, got: {error_logs}"

        outcomes = collected["finished"][0].outcomes
        apps_outcome = next(o for o in outcomes if o.name == "apps")
        # A cancelled selection is not an export failure.
        assert apps_outcome.status == ModuleStatus.MATCHED

        zip_files = list(tmp_path.glob("*.winsnap"))
        assert len(zip_files) == 1
        written = _read_snapshot_json_from_zip(zip_files[0])
        assert written["modules"]["apps"] == {
            "winget": [], "manual": [], "winget_export_error": None,
        }
