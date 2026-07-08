"""
test_prop_module_order.py — Unit test for manifest-derived module ordering.

Feature: gui-backend-alignment, Task 3.6: gui.py's hardcoded
MODULES_EXPORT_ORDER/MODULES_RESTORE_ORDER constants are replaced by a
single canonical order, modules.manifest.MODULE_NAMES, imported at gui.py
module scope. Every resolve_run_modules caller now passes
manifest.MODULE_NAMES as the order argument, so there is exactly one
ordering for both export and restore (Req 5.1-5.4, 9.4).

This is a regression guard for the ordering rationale documented in
modules/manifest.py: "apps" (winget install) must run before "startup"
and "taskbar", which depend on binaries/shortcuts that installing apps
provides (Req 2.1 of the backend-roundtrip-hardening feature, carried
forward here as Req 5.2).

Validates: Requirements 5.1, 5.2, 5.3, 5.4, 9.4 (gui-backend-alignment)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from modules import manifest

from gui import resolve_run_modules


def test_apps_precedes_startup_and_taskbar_in_manifest_order():
    """apps SHALL be ordered before both startup and taskbar in the
    canonical manifest order (the single source of truth gui.py now
    uses for both export and restore)."""
    apps_index = manifest.MODULE_NAMES.index("apps")
    startup_index = manifest.MODULE_NAMES.index("startup")
    taskbar_index = manifest.MODULE_NAMES.index("taskbar")

    assert apps_index < startup_index
    assert apps_index < taskbar_index


def test_apps_precedes_startup_and_taskbar_after_resolve_run_modules_filtering():
    """The apps-before-startup/taskbar ordering SHALL be preserved after
    resolve_run_modules filters the manifest order down to a selected
    subset -- i.e. filtering never reorders, only removes."""
    selected = {"apps", "startup", "taskbar"}
    result = resolve_run_modules(selected, manifest.MODULE_NAMES)

    assert result == ["apps", "startup", "taskbar"]
    assert result.index("apps") < result.index("startup")
    assert result.index("apps") < result.index("taskbar")


def test_gui_module_ordering_constants_are_removed():
    """Req 5.4: the hardcoded MODULES_EXPORT_ORDER/MODULES_RESTORE_ORDER
    constants and the dead default_snapshot_name() SHALL be removed
    entirely -- gui.py now sources ordering solely from
    modules.manifest.MODULE_NAMES."""
    import gui

    assert not hasattr(gui, "MODULES_EXPORT_ORDER")
    assert not hasattr(gui, "MODULES_RESTORE_ORDER")
    assert not hasattr(gui, "default_snapshot_name")


def test_gui_imports_manifest_at_module_scope():
    """Req 5.1: gui.py imports modules.manifest at module scope (not
    lazily inside a worker method, unlike gui.py's restore/export
    imports), so manifest.MODULE_NAMES is available wherever gui.py's
    module-level code needs the canonical order."""
    import gui

    assert gui.manifest is manifest
    assert gui.manifest.MODULE_NAMES == manifest.MODULE_NAMES
