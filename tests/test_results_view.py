"""Unit tests for the ResultsView widget.

Tests run with QT_QPA_PLATFORM=offscreen so no display is required.
"""

import os
import sys

import pytest

# Force offscreen rendering for headless CI
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QGroupBox, QLabel

# Ensure a QApplication exists before any widget tests
_app = QApplication.instance() or QApplication(sys.argv)

from gui import ModuleOutcome, ModuleStatus, ResultsSummary, ResultsView


class TestResultsView:
    """Tests for ResultsView widget (Requirements 14.1, 14.2, 14.3, 14.7)."""

    def test_hidden_by_default(self):
        """ResultsView should not be visible when first created."""
        view = ResultsView()
        assert not view.isVisible()

    def test_show_summary_makes_visible(self):
        """Calling show_summary() should make the widget visible."""
        view = ResultsView()
        summary = ResultsSummary()
        summary.add(ModuleOutcome(name="wallpaper", status=ModuleStatus.PASSED, detail=None))
        view.show_summary(summary)
        assert view.isVisible()

    def test_counts_header_displays_correctly(self):
        """The counts header should show 'Passed: X | Failed: Y | Skipped: Z'."""
        view = ResultsView()
        summary = ResultsSummary()
        summary.add(ModuleOutcome(name="wallpaper", status=ModuleStatus.PASSED, detail=None))
        summary.add(ModuleOutcome(name="apps", status=ModuleStatus.PASSED, detail=None))
        summary.add(ModuleOutcome(name="power", status=ModuleStatus.FAILED, detail="No admin"))
        summary.add(ModuleOutcome(name="cursors", status=ModuleStatus.SKIPPED, detail="Deselected by user"))
        view.show_summary(summary)
        assert view._counts_label.text() == "Passed: 2 | Failed: 1 | Skipped: 1"

    def test_has_three_group_boxes(self):
        """ResultsView should contain three QGroupBox sections."""
        view = ResultsView()
        groups = view.findChildren(QGroupBox)
        assert len(groups) == 3
        titles = {g.title() for g in groups}
        assert titles == {"Passed", "Failed", "Skipped"}

    def test_passed_rows_show_module_name(self):
        """Passed rows should display just the module name."""
        view = ResultsView()
        summary = ResultsSummary()
        summary.add(ModuleOutcome(name="wallpaper", status=ModuleStatus.PASSED, detail=None))
        summary.add(ModuleOutcome(name="fonts", status=ModuleStatus.PASSED, detail=None))
        view.show_summary(summary)

        labels = [
            view._passed_layout.itemAt(i).widget()
            for i in range(view._passed_layout.count())
        ]
        texts = [lbl.text() for lbl in labels if isinstance(lbl, QLabel)]
        assert texts == ["wallpaper", "fonts"]

    def test_failed_rows_show_error_message(self):
        """Failed rows should display 'module_name — error message' (Req 14.2)."""
        view = ResultsView()
        summary = ResultsSummary()
        summary.add(ModuleOutcome(name="power", status=ModuleStatus.FAILED, detail="No admin rights"))
        view.show_summary(summary)

        labels = [
            view._failed_layout.itemAt(i).widget()
            for i in range(view._failed_layout.count())
        ]
        texts = [lbl.text() for lbl in labels if isinstance(lbl, QLabel)]
        assert texts == ["power \u2014 No admin rights"]

    def test_skipped_rows_show_reason(self):
        """Skipped rows should display 'module_name — reason' (Req 14.3)."""
        view = ResultsView()
        summary = ResultsSummary()
        summary.add(ModuleOutcome(name="apps", status=ModuleStatus.SKIPPED, detail="Deselected by user"))
        view.show_summary(summary)

        labels = [
            view._skipped_layout.itemAt(i).widget()
            for i in range(view._skipped_layout.count())
        ]
        texts = [lbl.text() for lbl in labels if isinstance(lbl, QLabel)]
        assert texts == ["apps \u2014 Deselected by user"]

    def test_show_summary_clears_previous_content(self):
        """Calling show_summary() again should replace previous content."""
        view = ResultsView()

        # First summary
        summary1 = ResultsSummary()
        summary1.add(ModuleOutcome(name="wallpaper", status=ModuleStatus.PASSED, detail=None))
        summary1.add(ModuleOutcome(name="apps", status=ModuleStatus.PASSED, detail=None))
        view.show_summary(summary1)
        assert view._counts_label.text() == "Passed: 2 | Failed: 0 | Skipped: 0"

        # Second summary (different content)
        summary2 = ResultsSummary()
        summary2.add(ModuleOutcome(name="power", status=ModuleStatus.FAILED, detail="Error"))
        view.show_summary(summary2)
        assert view._counts_label.text() == "Passed: 0 | Failed: 1 | Skipped: 0"
        assert view._passed_layout.count() == 0

    def test_empty_summary(self):
        """An empty summary should show all zeros and no rows."""
        view = ResultsView()
        summary = ResultsSummary()
        view.show_summary(summary)
        assert view._counts_label.text() == "Passed: 0 | Failed: 0 | Skipped: 0"
        assert view._passed_layout.count() == 0
        assert view._failed_layout.count() == 0
        assert view._skipped_layout.count() == 0
        assert view.isVisible()
