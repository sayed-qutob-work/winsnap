"""Unit tests for the RunningIndicator widget.

Tests run with QT_QPA_PLATFORM=offscreen so no display is required.
"""

import os
import sys

import pytest

# Force offscreen rendering for headless CI
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QProgressBar

# Ensure a QApplication exists before any widget tests
_app = QApplication.instance() or QApplication(sys.argv)

from gui import RunningIndicator


class TestRunningIndicator:
    """Tests for RunningIndicator widget (Requirement 15.5)."""

    def test_hidden_by_default(self):
        """RunningIndicator should not be visible when first created."""
        indicator = RunningIndicator()
        assert not indicator.isVisible()

    def test_start_shows_indicator(self):
        """Calling start() should make the indicator visible."""
        indicator = RunningIndicator()
        indicator.start()
        assert indicator.isVisible()

    def test_stop_hides_indicator(self):
        """Calling stop() should hide the indicator."""
        indicator = RunningIndicator()
        indicator.start()
        indicator.stop()
        assert not indicator.isVisible()

    def test_progress_bar_is_indeterminate(self):
        """The internal QProgressBar should have min=0, max=0 (indeterminate)."""
        indicator = RunningIndicator()
        progress_bar = indicator.findChild(QProgressBar)
        assert progress_bar is not None
        assert progress_bar.minimum() == 0
        assert progress_bar.maximum() == 0

    def test_start_stop_cycle(self):
        """Multiple start/stop cycles should work correctly."""
        indicator = RunningIndicator()
        for _ in range(3):
            indicator.start()
            assert indicator.isVisible()
            indicator.stop()
            assert not indicator.isVisible()
