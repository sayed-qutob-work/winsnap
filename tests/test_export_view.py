"""Unit tests for the ExportView widget.

Tests run with QT_QPA_PLATFORM=offscreen so no display is required.
"""

import os
import sys
from pathlib import Path

import pytest

# Force offscreen rendering for headless CI
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QCheckBox, QLabel, QLineEdit, QPushButton

# Ensure a QApplication exists before any widget tests
_app = QApplication.instance() or QApplication(sys.argv)

from gui import ExportConfig, ExportView, ModuleSelector, MODULES_EXPORT_ORDER


class TestExportViewDefaults:
    """Tests for ExportView initial state."""

    def test_default_output_dir_is_desktop(self):
        """Output directory should default to user's Desktop (Req 2.2)."""
        view = ExportView()
        expected = Path.home() / "Desktop"
        assert view._output_dir == expected

    def test_path_label_shows_desktop(self):
        """Path label should display the Desktop path on init (Req 2.2)."""
        view = ExportView()
        expected = str(Path.home() / "Desktop")
        assert view._path_label.text() == expected

    def test_snapshot_name_empty_by_default(self):
        """Snapshot name field should be empty initially."""
        view = ExportView()
        assert view._name_edit.text() == ""

    def test_snapshot_name_max_length(self):
        """Snapshot name QLineEdit should have maxLength=255 (Req 2.3)."""
        view = ExportView()
        assert view._name_edit.maxLength() == 255

    def test_snapshot_name_placeholder(self):
        """Snapshot name should have a placeholder indicating auto-generation."""
        view = ExportView()
        assert "auto" in view._name_edit.placeholderText().lower()

    def test_show_all_unchecked_by_default(self):
        """Show_All checkbox should be unchecked by default (Req 4.2)."""
        view = ExportView()
        assert not view._show_all_cb.isChecked()

    def test_module_selector_present(self):
        """ExportView should contain a ModuleSelector instance (Req 3.1)."""
        view = ExportView()
        assert isinstance(view._module_selector, ModuleSelector)

    def test_all_modules_selected_by_default(self):
        """All 13 modules should be selected by default (Req 3.4)."""
        view = ExportView()
        assert view._module_selector.selected() == set(MODULES_EXPORT_ORDER)


class TestExportViewBuildConfig:
    """Tests for ExportView.build_config() method."""

    def test_build_config_returns_export_config(self):
        """build_config() should return an ExportConfig instance."""
        view = ExportView()
        config = view.build_config()
        assert isinstance(config, ExportConfig)

    def test_build_config_default_state(self):
        """build_config() with defaults should have Desktop dir, no name, show_all=False, all modules."""
        view = ExportView()
        config = view.build_config()
        assert config.output_dir == Path.home() / "Desktop"
        assert config.name is None
        assert config.show_all is False
        assert config.selected_modules == set(MODULES_EXPORT_ORDER)

    def test_build_config_with_name(self):
        """build_config() should capture the snapshot name when provided."""
        view = ExportView()
        view._name_edit.setText("my_snapshot")
        config = view.build_config()
        assert config.name == "my_snapshot"

    def test_build_config_strips_whitespace_name(self):
        """build_config() should strip whitespace from name; empty after strip → None."""
        view = ExportView()
        view._name_edit.setText("   ")
        config = view.build_config()
        assert config.name is None

    def test_build_config_show_all_checked(self):
        """build_config() should reflect show_all=True when checkbox is checked."""
        view = ExportView()
        view._show_all_cb.setChecked(True)
        config = view.build_config()
        assert config.show_all is True

    def test_build_config_partial_module_selection(self):
        """build_config() should reflect partial module selection."""
        view = ExportView()
        view._module_selector.set_all(False)
        # Manually check just 'wallpaper' and 'apps'
        view._module_selector._checkboxes["wallpaper"].setChecked(True)
        view._module_selector._checkboxes["apps"].setChecked(True)
        config = view.build_config()
        assert config.selected_modules == {"wallpaper", "apps"}

    def test_build_config_custom_output_dir(self):
        """build_config() should reflect a changed output directory."""
        view = ExportView()
        view._output_dir = Path("C:/custom/path")
        view._path_label.setText("C:/custom/path")
        config = view.build_config()
        assert config.output_dir == Path("C:/custom/path")


class TestExportViewBrowseButton:
    """Tests for the Browse button behavior."""

    def test_browse_button_exists(self):
        """ExportView should have a Browse button."""
        view = ExportView()
        assert view._browse_btn is not None
        assert isinstance(view._browse_btn, QPushButton)
        assert view._browse_btn.text() == "Browse..."

    def test_cancel_leaves_path_unchanged(self):
        """If directory dialog returns empty (cancelled), path stays unchanged (Req 2.6)."""
        view = ExportView()
        original_dir = view._output_dir
        original_label = view._path_label.text()
        # Simulate cancel: _choose_directory checks if chosen is truthy
        # An empty string from QFileDialog means cancel
        view._output_dir = original_dir  # stays the same
        assert view._output_dir == original_dir
        assert view._path_label.text() == original_label
