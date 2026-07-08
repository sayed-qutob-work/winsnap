"""
test_restore_worker_adapters.py — Integration tests for the rewritten
RestoreWorker (gui.py Task 5.2), which is a thin adapter over restore.py's
importable orchestration functions (safe_extract, find_snapshot_dir,
evaluate_snapshot_version, partition_modules, run_dry_run, run_modules,
run_verify).

These tests exercise RestoreWorker end to end (worker.run() called directly,
not via QThread — mirrors the existing test_restore_worker.py pattern) with
stub modules installed as restore.ALL_MODULES / modules.manifest.MODULE_NAMES
(mirroring tests/test_restore_hygiene.py's approach for restore.py's own
CLI-level tests), so no real registry/filesystem module logic is exercised —
only the worker's wiring to the backend.

This is new coverage added by gui-backend-alignment Task 5.3 (distinct from
tasks 7.6/7.7, which will rewrite the *old*, pre-rewrite
tests/test_restore_worker.py's monkeypatch targets separately).

Requirements: 1.6, 2.3, 3.2, 3.5, 4.2, 4.4, 4.5, 6.2, 6.4, 7.2, 9.3, 9.4,
10.2, 10.4, 11.6
"""

import json
import os
import sys
import types
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

# Ensure a QApplication exists before any QObject/signal tests.
_app = QApplication.instance() or QApplication(sys.argv)

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.conftest import make_winsnap_zip, stage_snapshot_json

import restore as restore_module
from modules import report as report_module
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
# Helpers
# ---------------------------------------------------------------------------


def _make_stub_module(restore_status: str, verify_status: str | None = None,
                       explorer_restart: bool = False, name: str = "stub"):
    """
    Build a minimal (restore[, verify]) module object -- a plain namespace,
    not a real modules/* module -- so worker tests don't depend on real
    registry access. Mirrors tests/test_restore_hygiene.py's helper of the
    same shape, kept local here so this file has no cross-file test
    dependency.
    """
    mod = types.SimpleNamespace()

    def _build(phase, status):
        rpt = report_module.Report(name, phase)
        if status == "matched":
            rpt.add_matched("thing")
        elif status == "failed":
            rpt.add_failed("thing", "boom")
        elif status == "partial":
            rpt.add_matched("a")
            rpt.add_failed("b", "boom")
        elif status == "skipped":
            return rpt.skip_all("nothing to do")
        if phase == "restore" and explorer_restart:
            rpt.require_explorer_restart()
        return rpt.finalize()

    def restore(data, snapshot_dir):
        return _build("restore", restore_status)
    mod.restore = restore

    if verify_status is not None:
        def verify(data, snapshot_dir):
            return _build("verify", verify_status)
        mod.verify = verify

    return mod


def _make_none_returning_module():
    """A module whose restore() violates the contract by returning None."""
    mod = types.SimpleNamespace()
    mod.restore = lambda data, snapshot_dir: None
    return mod


def _install_stub_modules(monkeypatch, stub_map: dict) -> None:
    """
    Install `stub_map` ({key: module}) as both restore.ALL_MODULES and
    modules.manifest.MODULE_NAMES (same keys, same order), so RestoreWorker's
    manifest-driven "deselected" loop and its ALL_MODULES-derived run set
    agree on the same module universe -- isolating these tests from the
    real 13-module manifest.
    """
    items = list(stub_map.items())
    monkeypatch.setattr(restore_module, "ALL_MODULES", items)
    monkeypatch.setattr(manifest_module, "MODULE_NAMES", [k for k, _ in items])


def _collect_signals(worker: RestoreWorker) -> dict:
    collected = {"logs": [], "module_completed": [], "finished": [], "running_changed": []}
    worker.log.connect(lambda msg, sev: collected["logs"].append((msg, sev)))
    worker.module_completed.connect(lambda o: collected["module_completed"].append(o))
    worker.finished.connect(lambda s: collected["finished"].append(s))
    worker.running_changed.connect(lambda b: collected["running_changed"].append(b))
    return collected


def _make_flat_zip(tmp_path: Path, modules: dict | None = None,
                    version: str = "0.3.0", zip_name: str = "flat.winsnap") -> Path:
    """Build a .winsnap archive with snapshot.json at the archive ROOT (no
    wrapper folder) -- the "flat archive" layout Req 4.4 requires the GUI to
    restore successfully via restore.find_snapshot_dir."""
    build_dir = tmp_path / f"_flat_build_{zip_name}"
    build_dir.mkdir(exist_ok=True)
    stage_snapshot_json(build_dir, version=version, modules=modules or {})

    zip_path = tmp_path / zip_name
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in build_dir.rglob("*"):
            zf.write(file, file.relative_to(build_dir))
    return zip_path


def _make_zip_with_raw_snapshot(tmp_path: Path, snapshot: dict,
                                 folder_name: str = "winsnap_test",
                                 zip_name: str = "raw.winsnap") -> Path:
    """Build a .winsnap archive (nested layout) from a caller-supplied
    snapshot dict, verbatim -- for tests that need control over exactly
    which top-level keys are present (e.g. version-fallback parity, where
    snapshot_format_version must be ABSENT)."""
    build_dir = tmp_path / f"_raw_build_{zip_name}"
    snap_folder = build_dir / folder_name
    snap_folder.mkdir(parents=True)
    (snap_folder / "snapshot.json").write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    zip_path = tmp_path / zip_name
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in snap_folder.rglob("*"):
            zf.write(file, file.relative_to(build_dir))
    return zip_path


# ---------------------------------------------------------------------------
# zip-slip rejection (Req 4.2)
# ---------------------------------------------------------------------------


class TestZipSlipRejection:
    def test_zip_slip_refused_and_run_modules_never_called(self, tmp_path, monkeypatch):
        zip_path = make_winsnap_zip(tmp_path, member_names=["../evil.txt"])

        run_modules_mock = MagicMock()
        monkeypatch.setattr(restore_module, "run_modules", run_modules_mock)

        config = RestoreConfig(
            snapshot_path=zip_path, dry_run=False, selected_modules=set(),
        )
        worker = RestoreWorker(config)
        collected = _collect_signals(worker)
        worker.run()

        run_modules_mock.assert_not_called()
        assert any(sev == Severity.ERROR for _, sev in collected["logs"])
        assert any("unsafe" in msg.lower() for msg, _ in collected["logs"])
        # finished must still be emitted (so the UI can re-enable controls)
        # even though the archive was refused wholesale.
        assert len(collected["finished"]) == 1
        assert collected["finished"][0].outcomes == []


# ---------------------------------------------------------------------------
# Flat and nested archive layouts (Req 4.4)
# ---------------------------------------------------------------------------


class TestArchiveLayouts:
    def test_flat_archive_restores_successfully(self, tmp_path, monkeypatch):
        stub = _make_stub_module(restore_status="matched", name="stub_mod")
        _install_stub_modules(monkeypatch, {"stub_mod": stub})

        zip_path = _make_flat_zip(tmp_path, modules={"stub_mod": {"anything": True}})

        config = RestoreConfig(
            snapshot_path=zip_path, dry_run=False, selected_modules={"stub_mod"},
        )
        worker = RestoreWorker(config)
        collected = _collect_signals(worker)
        worker.run()

        assert not any(sev == Severity.ERROR for _, sev in collected["logs"]), collected["logs"]
        outcomes = collected["finished"][0].outcomes
        stub_outcome = next(o for o in outcomes if o.name == "stub_mod")
        assert stub_outcome.status == ModuleStatus.MATCHED

    def test_nested_archive_restores_successfully(self, tmp_path, monkeypatch):
        stub = _make_stub_module(restore_status="matched", name="stub_mod")
        _install_stub_modules(monkeypatch, {"stub_mod": stub})

        zip_path = make_winsnap_zip(
            tmp_path, folder_name="winsnap_nested",
            modules={"stub_mod": {"anything": True}},
        )

        config = RestoreConfig(
            snapshot_path=zip_path, dry_run=False, selected_modules={"stub_mod"},
        )
        worker = RestoreWorker(config)
        collected = _collect_signals(worker)
        worker.run()

        assert not any(sev == Severity.ERROR for _, sev in collected["logs"]), collected["logs"]
        outcomes = collected["finished"][0].outcomes
        stub_outcome = next(o for o in outcomes if o.name == "stub_mod")
        assert stub_outcome.status == ModuleStatus.MATCHED


# ---------------------------------------------------------------------------
# SnapshotLayoutError handling (Req 4.5)
# ---------------------------------------------------------------------------


class TestSnapshotLayoutErrorHandling:
    def test_no_snapshot_json_anywhere_reports_clean_error(self, tmp_path, monkeypatch):
        run_modules_mock = MagicMock()
        monkeypatch.setattr(restore_module, "run_modules", run_modules_mock)

        zip_path = tmp_path / "no_snapshot.winsnap"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("some_dir/unrelated_file.txt", "hello")

        config = RestoreConfig(
            snapshot_path=zip_path, dry_run=False, selected_modules=set(),
        )
        worker = RestoreWorker(config)
        collected = _collect_signals(worker)
        worker.run()

        run_modules_mock.assert_not_called()
        assert any(sev == Severity.ERROR for _, sev in collected["logs"])
        assert any("not a recognizable snapshot" in msg.lower() for msg, _ in collected["logs"])
        assert len(collected["finished"]) == 1
        assert collected["finished"][0].outcomes == []


# ---------------------------------------------------------------------------
# Verify on/off flows (Req 3.2, 3.5)
# ---------------------------------------------------------------------------


class TestVerifyFlows:
    def test_verify_on_runs_run_verify_and_populates_verify_outcomes(self, tmp_path, monkeypatch):
        stub = _make_stub_module(restore_status="matched", verify_status="matched", name="stub_mod")
        _install_stub_modules(monkeypatch, {"stub_mod": stub})

        run_verify_mock = MagicMock(wraps=restore_module.run_verify)
        monkeypatch.setattr(restore_module, "run_verify", run_verify_mock)

        zip_path = make_winsnap_zip(tmp_path, modules={"stub_mod": {"anything": True}})
        config = RestoreConfig(
            snapshot_path=zip_path, dry_run=False, selected_modules={"stub_mod"}, verify=True,
        )
        worker = RestoreWorker(config)
        collected = _collect_signals(worker)
        worker.run()

        run_verify_mock.assert_called_once()
        summary = collected["finished"][0]
        assert len(summary.verify_outcomes) == 1
        assert summary.verify_outcomes[0].name == "stub_mod"
        assert summary.verify_outcomes[0].status == ModuleStatus.MATCHED

    def test_verify_off_never_runs_verify_and_no_verify_outcomes(self, tmp_path, monkeypatch):
        stub = _make_stub_module(restore_status="matched", verify_status="matched", name="stub_mod")
        _install_stub_modules(monkeypatch, {"stub_mod": stub})

        run_verify_mock = MagicMock()
        monkeypatch.setattr(restore_module, "run_verify", run_verify_mock)

        zip_path = make_winsnap_zip(tmp_path, modules={"stub_mod": {"anything": True}})
        config = RestoreConfig(
            snapshot_path=zip_path, dry_run=False, selected_modules={"stub_mod"}, verify=False,
        )
        worker = RestoreWorker(config)
        collected = _collect_signals(worker)
        worker.run()

        run_verify_mock.assert_not_called()
        summary = collected["finished"][0]
        assert summary.verify_outcomes == []

    def test_verify_never_runs_when_dry_run_true(self, tmp_path, monkeypatch):
        stub = _make_stub_module(restore_status="matched", verify_status="matched", name="stub_mod")
        _install_stub_modules(monkeypatch, {"stub_mod": stub})

        run_verify_mock = MagicMock()
        run_modules_mock = MagicMock()
        monkeypatch.setattr(restore_module, "run_verify", run_verify_mock)
        monkeypatch.setattr(restore_module, "run_modules", run_modules_mock)

        zip_path = make_winsnap_zip(tmp_path, modules={"stub_mod": {"anything": True}})
        # verify=True AND dry_run=True: dry-run must bypass verify entirely,
        # matching the CLI's --dry-run semantics (Req 3.5, 8.5, 8.8).
        config = RestoreConfig(
            snapshot_path=zip_path, dry_run=True, selected_modules={"stub_mod"}, verify=True,
        )
        worker = RestoreWorker(config)
        collected = _collect_signals(worker)
        worker.run()

        run_verify_mock.assert_not_called()
        run_modules_mock.assert_not_called()
        summary = collected["finished"][0]
        assert summary.verify_outcomes == []


# ---------------------------------------------------------------------------
# Failed report -> ModuleStatus.FAILED (regression: "no exception => shown
# as passed") (Req 1.6)
# ---------------------------------------------------------------------------


class TestFailedReportRegression:
    def test_failed_report_without_exception_surfaces_as_failed(self, tmp_path, monkeypatch):
        """A module whose restore() returns {"status": "failed", ...} WITHOUT
        raising must be classified FAILED -- this is the direct regression
        guard for the old bug where "no exception" was treated as success."""
        stub = _make_stub_module(restore_status="failed", name="stub_mod")
        _install_stub_modules(monkeypatch, {"stub_mod": stub})

        zip_path = make_winsnap_zip(tmp_path, modules={"stub_mod": {"anything": True}})
        config = RestoreConfig(
            snapshot_path=zip_path, dry_run=False, selected_modules={"stub_mod"},
        )
        worker = RestoreWorker(config)
        collected = _collect_signals(worker)
        worker.run()

        outcomes = collected["finished"][0].outcomes
        stub_outcome = next(o for o in outcomes if o.name == "stub_mod")
        assert stub_outcome.status == ModuleStatus.FAILED
        # A "failed" (not "skipped") status carries no top-level `reason`
        # per modules/report.py's aggregation rule -- the failure detail
        # lives on the per-item entry instead.
        assert stub_outcome.detail is None
        assert stub_outcome.items[0]["status"] == "failed"
        assert stub_outcome.items[0]["detail"] == "boom"

    def test_none_returning_restore_surfaces_as_skipped_not_matched(self, tmp_path, monkeypatch):
        """A module whose restore() returns None (contract violation) is
        recorded skipped with the "module returned no report" semantics
        run_modules synthesizes -- never silently matched (Req 2.3)."""
        stub = _make_none_returning_module()
        _install_stub_modules(monkeypatch, {"stub_mod": stub})

        zip_path = make_winsnap_zip(tmp_path, modules={"stub_mod": {"anything": True}})
        config = RestoreConfig(
            snapshot_path=zip_path, dry_run=False, selected_modules={"stub_mod"},
        )
        worker = RestoreWorker(config)
        collected = _collect_signals(worker)
        worker.run()

        outcomes = collected["finished"][0].outcomes
        stub_outcome = next(o for o in outcomes if o.name == "stub_mod")
        assert stub_outcome.status == ModuleStatus.SKIPPED
        assert stub_outcome.detail == "module returned no report"


# ---------------------------------------------------------------------------
# Partial report -> distinct row with item detail (Req 1.6)
# ---------------------------------------------------------------------------


class TestPartialReportItemDetail:
    def test_partial_report_renders_distinct_status_with_items(self, tmp_path, monkeypatch):
        stub = _make_stub_module(restore_status="partial", name="stub_mod")
        _install_stub_modules(monkeypatch, {"stub_mod": stub})

        zip_path = make_winsnap_zip(tmp_path, modules={"stub_mod": {"anything": True}})
        config = RestoreConfig(
            snapshot_path=zip_path, dry_run=False, selected_modules={"stub_mod"},
        )
        worker = RestoreWorker(config)
        collected = _collect_signals(worker)
        worker.run()

        outcomes = collected["finished"][0].outcomes
        stub_outcome = next(o for o in outcomes if o.name == "stub_mod")
        assert stub_outcome.status == ModuleStatus.PARTIAL
        assert stub_outcome.status != ModuleStatus.MATCHED
        assert stub_outcome.status != ModuleStatus.FAILED

        item_names = {item["name"]: item for item in stub_outcome.items}
        assert item_names["a"]["status"] == "matched"
        assert item_names["b"]["status"] == "failed"
        assert item_names["b"]["detail"] == "boom"


# ---------------------------------------------------------------------------
# Exactly one winutil.restart_explorer() call (Req 6.2, 6.4)
# ---------------------------------------------------------------------------


class TestExplorerRestartPolicy:
    def test_single_restart_when_explorer_and_desktop_icons_without_taskbar(
            self, tmp_path, monkeypatch
    ):
        """Regression guard: a restore that includes explorer/desktop_icons
        but NOT taskbar must still trigger exactly one Explorer restart when
        either module's report requests it (the current silent-no-restart
        bug this feature fixes)."""
        restart_mock = MagicMock()
        monkeypatch.setattr(restore_module.winutil, "restart_explorer", restart_mock)

        explorer_stub = _make_stub_module(
            restore_status="matched", explorer_restart=True, name="explorer")
        desktop_icons_stub = _make_stub_module(
            restore_status="matched", explorer_restart=True, name="desktop_icons")
        _install_stub_modules(monkeypatch, {
            "explorer": explorer_stub, "desktop_icons": desktop_icons_stub,
        })

        zip_path = make_winsnap_zip(tmp_path, modules={
            "explorer": {"anything": True},
            "desktop_icons": {"anything": True},
        })
        config = RestoreConfig(
            snapshot_path=zip_path, dry_run=False,
            selected_modules={"explorer", "desktop_icons"},
        )
        worker = RestoreWorker(config)
        collected = _collect_signals(worker)
        worker.run()

        assert restart_mock.call_count == 1
        # sanity: taskbar was never part of this run at all.
        outcome_names = {o.name for o in collected["finished"][0].outcomes}
        assert outcome_names == {"explorer", "desktop_icons"}

    def test_no_restart_when_no_report_requires_it(self, tmp_path, monkeypatch):
        restart_mock = MagicMock()
        monkeypatch.setattr(restore_module.winutil, "restart_explorer", restart_mock)

        stub = _make_stub_module(restore_status="matched", explorer_restart=False, name="stub_mod")
        _install_stub_modules(monkeypatch, {"stub_mod": stub})

        zip_path = make_winsnap_zip(tmp_path, modules={"stub_mod": {"anything": True}})
        config = RestoreConfig(
            snapshot_path=zip_path, dry_run=False, selected_modules={"stub_mod"},
        )
        worker = RestoreWorker(config)
        _collect_signals(worker)
        worker.run()

        restart_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Version-fallback parity: snapshot carrying only winsnap_version (Req 7.2)
# ---------------------------------------------------------------------------


class TestVersionFallbackParity:
    def test_snapshot_with_only_winsnap_version_still_restores(self, tmp_path, monkeypatch):
        stub = _make_stub_module(restore_status="matched", name="stub_mod")
        _install_stub_modules(monkeypatch, {"stub_mod": stub})

        snapshot = {
            # snapshot_format_version is DELIBERATELY absent; the accept/
            # refuse decision must fall back to winsnap_version, exactly
            # like restore._check_format_version / evaluate_snapshot_version.
            "winsnap_version": "0.3.0",
            "exported_at": "2024-01-01T00:00:00",
            "exported_on": {"user": "t", "machine": "T"},
            "modules_attempted": ["stub_mod"],
            "modules": {"stub_mod": {"anything": True}},
        }
        zip_path = _make_zip_with_raw_snapshot(tmp_path, snapshot)

        config = RestoreConfig(
            snapshot_path=zip_path, dry_run=False, selected_modules={"stub_mod"},
        )
        worker = RestoreWorker(config)
        collected = _collect_signals(worker)
        worker.run()

        # Must NOT be treated as incompatible/halted.
        assert not any(
            "newer than this restorer supports" in msg for msg, _ in collected["logs"]
        )
        outcomes = collected["finished"][0].outcomes
        stub_outcome = next(o for o in outcomes if o.name == "stub_mod")
        assert stub_outcome.status == ModuleStatus.MATCHED
