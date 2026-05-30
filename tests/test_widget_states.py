"""Unit tests for widget initial states and configuration.

Covers ModuleSelector, RestoreView, and AppSelectorDialog — the widgets
not fully tested by the existing test_export_view.py, test_log_panel.py,
test_running_indicator.py, and test_results_view.py files.

Tests run with QT_QPA_PLATFORM=offscreen so no display is required.
"""

import os
import sys
from pathlib import Path

import pytest

# Force offscreen rendering for headless CI
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QCheckBox, QDialog

# Ensure a QApplication exists before any widget tests
_app = QApplication.instance() or QApplication(sys.argv)

from gui import (
    AppSelectorDialog,
    ModuleSelector,
    MODULES_EXPORT_ORDER,
    RestoreConfig,
    RestoreView,
)


# ---------------------------------------------------------------------------
# ModuleSelector tests
# ---------------------------------------------------------------------------


class TestModuleSelectorDefaults:
    """Tests for ModuleSelector initial state (Requirement 3.4)."""

    def test_has_13_checkboxes(self):
        """ModuleSelector should have exactly 13 checkboxes."""
        selector = ModuleSelector()
        assert len(selector._checkboxes) == 13

    def test_all_13_checked_by_default(self):
        """All 13 module checkboxes should be checked on creation (Req 3.4, 9.2)."""
        selector = ModuleSelector()
        assert selector.selected() == set(MODULES_EXPORT_ORDER)

    def test_checkbox_labels_match_module_names(self):
        """Each checkbox label should match its module name."""
        selector = ModuleSelector()
        for name, cb in selector._checkboxes.items():
            assert cb.text() == name


class TestModuleSelectorSelectAll:
    """Tests for ModuleSelector select-all / deselect-all behavior."""

    def test_set_all_true_selects_all(self):
        """set_all(True) should check all 13 modules (Req 3.6)."""
        selector = ModuleSelector()
        # First deselect some
        selector._checkboxes["wallpaper"].setChecked(False)
        selector._checkboxes["apps"].setChecked(False)
        # Now select all
        selector.set_all(True)
        assert selector.selected() == set(MODULES_EXPORT_ORDER)

    def test_set_all_false_deselects_all(self):
        """set_all(False) should uncheck all 13 modules (Req 3.7)."""
        selector = ModuleSelector()
        selector.set_all(False)
        assert selector.selected() == set()

    def test_select_all_after_deselect_all(self):
        """Deselect all then select all should restore full selection."""
        selector = ModuleSelector()
        selector.set_all(False)
        assert selector.selected() == set()
        selector.set_all(True)
        assert selector.selected() == set(MODULES_EXPORT_ORDER)

    def test_partial_selection(self):
        """Manually toggling checkboxes should reflect in selected()."""
        selector = ModuleSelector()
        selector.set_all(False)
        selector._checkboxes["wallpaper"].setChecked(True)
        selector._checkboxes["fonts"].setChecked(True)
        assert selector.selected() == {"wallpaper", "fonts"}


# ---------------------------------------------------------------------------
# RestoreView tests
# ---------------------------------------------------------------------------


class TestRestoreViewDefaults:
    """Tests for RestoreView initial state (Requirements 8.6, 9.2, 9.5)."""

    def test_no_file_selected_label(self):
        """RestoreView should show 'No snapshot file selected' initially (Req 8.6)."""
        view = RestoreView()
        assert view._path_label.text() == "No snapshot file selected"

    def test_snapshot_path_is_none(self):
        """No snapshot path should be set initially."""
        view = RestoreView()
        assert view._snapshot_path is None

    def test_dry_run_unchecked_by_default(self):
        """Dry_Run checkbox should be unchecked by default (Req 9.5)."""
        view = RestoreView()
        assert not view._dry_run_cb.isChecked()

    def test_all_modules_selected_by_default(self):
        """All 13 modules should be selected by default (Req 9.2)."""
        view = RestoreView()
        assert view._module_selector.selected() == set(MODULES_EXPORT_ORDER)

    def test_has_module_selector(self):
        """RestoreView should contain a ModuleSelector instance."""
        view = RestoreView()
        assert isinstance(view._module_selector, ModuleSelector)

    def test_build_config_default_state(self):
        """build_config() with defaults should have empty path, dry_run=False, all modules."""
        view = RestoreView()
        config = view.build_config()
        assert isinstance(config, RestoreConfig)
        assert config.snapshot_path == Path("")
        assert config.dry_run is False
        assert config.selected_modules == set(MODULES_EXPORT_ORDER)


# ---------------------------------------------------------------------------
# AppSelectorDialog tests
# ---------------------------------------------------------------------------


class TestAppSelectorDialogPreselection:
    """Tests for AppSelectorDialog preselection (Requirement 5.2)."""

    def test_all_winget_apps_preselected(self):
        """All winget app checkboxes should be checked on open (Req 5.2)."""
        winget = [{"name": "App1"}, {"name": "App2"}, {"name": "App3"}]
        manual = [{"name": "ManualApp"}]
        dialog = AppSelectorDialog(winget, manual)
        for cb in dialog._winget_checkboxes:
            assert cb.isChecked()

    def test_all_manual_apps_preselected(self):
        """All manual app checkboxes should be checked on open (Req 5.2)."""
        winget = [{"name": "App1"}]
        manual = [{"name": "M1"}, {"name": "M2"}, {"name": "M3"}]
        dialog = AppSelectorDialog(winget, manual)
        for cb in dialog._manual_checkboxes:
            assert cb.isChecked()

    def test_checkbox_labels_match_app_names(self):
        """Checkbox labels should match the app 'name' field."""
        winget = [{"name": "Firefox"}, {"name": "Chrome"}]
        manual = [{"name": "CustomTool"}]
        dialog = AppSelectorDialog(winget, manual)
        assert dialog._winget_checkboxes[0].text() == "Firefox"
        assert dialog._winget_checkboxes[1].text() == "Chrome"
        assert dialog._manual_checkboxes[0].text() == "CustomTool"


class TestAppSelectorDialogEmptyGroups:
    """Tests for AppSelectorDialog with empty groups (Requirement 5.7)."""

    def test_empty_winget_group(self):
        """Dialog should work with an empty winget group."""
        winget: list[dict] = []
        manual = [{"name": "ManualApp"}]
        dialog = AppSelectorDialog(winget, manual)
        assert len(dialog._winget_checkboxes) == 0
        assert len(dialog._manual_checkboxes) == 1

    def test_empty_manual_group(self):
        """Dialog should work with an empty manual group."""
        winget = [{"name": "App1"}]
        manual: list[dict] = []
        dialog = AppSelectorDialog(winget, manual)
        assert len(dialog._winget_checkboxes) == 1
        assert len(dialog._manual_checkboxes) == 0

    def test_both_groups_empty(self):
        """Dialog should work with both groups empty (still confirmable)."""
        winget: list[dict] = []
        manual: list[dict] = []
        dialog = AppSelectorDialog(winget, manual)
        assert len(dialog._winget_checkboxes) == 0
        assert len(dialog._manual_checkboxes) == 0

    def test_empty_group_dialog_has_button_box(self):
        """Even with empty groups, the dialog should have OK/Cancel buttons."""
        dialog = AppSelectorDialog([], [])
        # The dialog should be constructable and have a button box
        from PyQt6.QtWidgets import QDialogButtonBox
        button_box = dialog.findChild(QDialogButtonBox)
        assert button_box is not None


class TestAppSelectorDialogGroupControls:
    """Tests for per-group select-all / deselect-all in AppSelectorDialog."""

    def test_deselect_all_winget(self):
        """Per-group deselect-all should uncheck all winget entries."""
        winget = [{"name": "A"}, {"name": "B"}, {"name": "C"}]
        dialog = AppSelectorDialog(winget, [])
        dialog._set_group(dialog._winget_checkboxes, False)
        for cb in dialog._winget_checkboxes:
            assert not cb.isChecked()

    def test_select_all_winget_after_deselect(self):
        """Per-group select-all should re-check all winget entries."""
        winget = [{"name": "A"}, {"name": "B"}]
        dialog = AppSelectorDialog(winget, [])
        dialog._set_group(dialog._winget_checkboxes, False)
        dialog._set_group(dialog._winget_checkboxes, True)
        for cb in dialog._winget_checkboxes:
            assert cb.isChecked()

    def test_deselect_all_manual(self):
        """Per-group deselect-all should uncheck all manual entries."""
        manual = [{"name": "X"}, {"name": "Y"}]
        dialog = AppSelectorDialog([], manual)
        dialog._set_group(dialog._manual_checkboxes, False)
        for cb in dialog._manual_checkboxes:
            assert not cb.isChecked()
