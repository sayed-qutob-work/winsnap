"""
tests/test_ordering.py — Cross-module invariant tests for module execution
order (Task 14).

Feature: backend-roundtrip-hardening, Task 14 (Req 2.1, 2.2, 2.5, 7.2, 15.4;
Design D2, D10, Testing Strategy).

Covers:
  - modules.manifest.MODULE_NAMES is the single source of truth that both
    restore.py's ALL_MODULES and export.py's _build_modules derive from, in
    the same order -- the export and restore module *sets* can never drift
    apart (Req 2.1, 2.5).
  - "apps" precedes both "startup" and "taskbar" in MODULE_NAMES, so app
    installs land before the modules that depend on installed
    binaries/shortcuts run (Req 2.1).
  - An orchestrated restore (driven through restore.py's run_modules() /
    run_verify() orchestration functions, using stub modules rather than
    real ones so the test is independent of any module's own
    registry/filesystem behavior) performs exactly one
    winutil.restart_explorer() call, positioned after every module's
    restore() and before any module's verify() call (Req 1.3, 2.2, 7.2).

**Validates: Requirements 2.1, 2.2, 2.5, 7.2, 15.4**
"""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import restore as restore_module
import export as export_module
from modules import manifest
from modules import report as report_module


# ---------------------------------------------------------------------------
# manifest.MODULE_NAMES drives restore.ALL_MODULES and export._build_modules
# (Req 2.1, 2.5)
# ---------------------------------------------------------------------------

def test_restore_all_modules_matches_manifest_order():
    """restore.ALL_MODULES' keys must equal manifest.MODULE_NAMES, in the
    same order -- it is built as a derived list, not a second hand-maintained
    one (design D2)."""
    actual = [key for key, _mod in restore_module.ALL_MODULES]
    assert actual == manifest.MODULE_NAMES


def test_export_build_modules_matches_manifest_order():
    """export._build_modules(args) must iterate manifest.MODULE_NAMES too,
    so the export module *set* can never drift from the restore module set
    (Req 2.5)."""
    args = types.SimpleNamespace(
        show_all=False, apps_selection="interactive", apps_from=None,
    )
    built = export_module._build_modules(args)
    actual = [name for name, _fn in built]
    assert actual == manifest.MODULE_NAMES


# ---------------------------------------------------------------------------
# "apps" precedes "startup" and "taskbar" (Req 2.1)
# ---------------------------------------------------------------------------

def test_apps_precedes_startup_and_taskbar():
    """Apps (winget installs) must run before the modules that depend on
    binaries/shortcuts apps provides, so those consumers don't silently skip
    or break because their target binaries don't exist yet."""
    apps_index = manifest.MODULE_NAMES.index("apps")
    assert apps_index < manifest.MODULE_NAMES.index("startup")
    assert apps_index < manifest.MODULE_NAMES.index("taskbar")


# ---------------------------------------------------------------------------
# Orchestrated restore: exactly one restart, after all restores, before any
# verify (Req 1.3, 2.2, D2, D10)
# ---------------------------------------------------------------------------

def _make_stub_module(name: str, order: list, *, want_restart: bool = False):
    """
    A minimal (restore, verify) module stand-in -- a plain namespace, not a
    real modules/* module -- so this test is independent of any real
    module's own registry/filesystem side effects. Each call appends a
    marker to the shared `order` list so the test can assert call
    sequencing across the whole orchestrated run.
    """
    mod = types.SimpleNamespace()

    def restore(data, snapshot_dir):
        order.append(f"restore:{name}")
        rpt = report_module.Report(name, "restore")
        rpt.add_matched("thing")
        if want_restart:
            rpt.require_explorer_restart()
        return rpt.finalize()
    mod.restore = restore

    def verify(data, snapshot_dir):
        order.append(f"verify:{name}")
        rpt = report_module.Report(name, "verify")
        rpt.add_matched("thing")
        return rpt.finalize()
    mod.verify = verify

    return mod


def test_orchestrated_restore_restarts_explorer_exactly_once_between_phases(
        monkeypatch, tmp_path):
    """Only one of the three stub modules requests an Explorer restart, but
    the restart must still fire exactly once overall (not once per
    requesting module), after every module's restore() has run and before
    any module's verify() call."""
    order: list = []

    def fake_restart_explorer():
        order.append("restart")
        return True

    monkeypatch.setattr(restore_module.winutil, "restart_explorer",
                         fake_restart_explorer)

    stub_a = _make_stub_module("mod_a", order, want_restart=True)
    stub_b = _make_stub_module("mod_b", order, want_restart=False)
    stub_c = _make_stub_module("mod_c", order, want_restart=False)

    modules_to_run = [("mod_a", stub_a), ("mod_b", stub_b), ("mod_c", stub_c)]
    modules_data = {"mod_a": {}, "mod_b": {}, "mod_c": {}}

    restore_reports = restore_module.run_modules(
        modules_to_run, modules_data, tmp_path, dry_run=False)
    verify_reports = restore_module.run_verify(
        modules_to_run, modules_data, tmp_path)

    assert all(r["status"] == "matched" for r in restore_reports.values())
    assert all(r["status"] == "matched" for r in verify_reports.values())

    restart_count = order.count("restart")
    assert restart_count == 1, f"expected exactly one restart, got order: {order}"

    restart_index = order.index("restart")
    restore_indices = [i for i, e in enumerate(order) if e.startswith("restore:")]
    verify_indices = [i for i, e in enumerate(order) if e.startswith("verify:")]

    assert len(restore_indices) == 3, "expected all three restore() calls to be logged"
    assert len(verify_indices) == 3, "expected all three verify() calls to be logged"
    assert max(restore_indices) < restart_index < min(verify_indices), (
        f"restart must land after every module's restore() and before any "
        f"module's verify(): {order}"
    )


def test_no_restart_when_no_module_requires_it(monkeypatch, tmp_path):
    """Sanity companion: when nothing requests a restart, none happens."""
    order: list = []
    monkeypatch.setattr(restore_module.winutil, "restart_explorer",
                         lambda: order.append("restart"))

    stub = _make_stub_module("mod_a", order, want_restart=False)
    modules_to_run = [("mod_a", stub)]
    modules_data = {"mod_a": {}}

    restore_module.run_modules(modules_to_run, modules_data, tmp_path, dry_run=False)

    assert "restart" not in order
