"""Smoke tests for MainWindow construction.

Verifies that the MainWindow constructs without errors and that all
key child widgets are present, accessible, and in their expected
initial states.

Tests run with QT_QPA_PLATFORM=offscreen so no display is required.

Requirements: 1.1, 1.3, 15.3
"""

import os
import sys

# Force offscreen rendering for headless CI
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

# Ensure a QApplication exists before any widget tests
_app = QApplication.instance() or QApplication(sys.argv)

from gui import (
    ExportView,
    LogPanel,
    MainWindow,
    RestoreView,
    ResultsView,
    RunningIndicator,
)


@pytest.fixture
def window():
    """Create a MainWindow instance for testing."""
    w = MainWindow()
    yield w
    w.close()


class TestMainWindowConstruction:
    """MainWindow constructs without errors (Requirement 1.1)."""

    def test_constructs_without_errors(self, window):
        """MainWindow should instantiate without raising any exceptions."""
        assert window is not None

    def test_window_title_is_winsnap(self, window):
        """Window title should be 'WinSnap'."""
        assert window.windowTitle() == "WinSnap"


class TestMainWindowViews:
    """ExportView and RestoreView are accessible via the stacked widget."""

    def test_export_view_at_index_0(self, window):
        """ExportView should be at stacked widget index 0 (Requirement 1.1)."""
        widget = window._stacked_widget.widget(0)
        assert isinstance(widget, ExportView)

    def test_restore_view_at_index_1(self, window):
        """RestoreView should be at stacked widget index 1 (Requirement 1.1)."""
        widget = window._stacked_widget.widget(1)
        assert isinstance(widget, RestoreView)


class TestMainWindowStartButtons:
    """Both start buttons are visible and enabled (Requirements 1.3, 15.3)."""

    def test_start_export_button_exists_and_enabled(self, window):
        """'Start Export' button should exist and be enabled."""
        btn = window._start_export_btn
        assert btn.text() == "Start Export"
        assert btn.isEnabled()

    def test_start_restore_button_exists_and_enabled(self, window):
        """'Start Restore' button should exist and be enabled."""
        btn = window._start_restore_btn
        assert btn.text() == "Start Restore"
        assert btn.isEnabled()


class TestMainWindowSharedWidgets:
    """LogPanel, ResultsView, and RunningIndicator are present."""

    def test_log_panel_is_present(self, window):
        """LogPanel should be present in the MainWindow."""
        assert isinstance(window._log_panel, LogPanel)

    def test_results_view_is_present(self, window):
        """ResultsView should be present in the MainWindow."""
        assert isinstance(window._results_view, ResultsView)

    def test_running_indicator_is_present(self, window):
        """RunningIndicator should be present in the MainWindow."""
        assert isinstance(window._running_indicator, RunningIndicator)

    def test_running_indicator_hidden_by_default(self, window):
        """RunningIndicator should be hidden by default (no operation running)."""
        assert not window._running_indicator.isVisible()


class TestMainWindowViewSwitcher:
    """View switcher toggles between Export and Restore views."""

    def test_default_view_is_export(self, window):
        """The default active view should be Export (index 0)."""
        assert window._stacked_widget.currentIndex() == 0

    def test_switch_to_restore_view(self, window):
        """Switching to index 1 should show the RestoreView."""
        window._view_switcher.setCurrentIndex(1)
        assert window._stacked_widget.currentIndex() == 1
        assert isinstance(window._stacked_widget.currentWidget(), RestoreView)

    def test_switch_back_to_export_view(self, window):
        """Switching back to index 0 should show the ExportView."""
        window._view_switcher.setCurrentIndex(1)
        window._view_switcher.setCurrentIndex(0)
        assert window._stacked_widget.currentIndex() == 0
        assert isinstance(window._stacked_widget.currentWidget(), ExportView)
