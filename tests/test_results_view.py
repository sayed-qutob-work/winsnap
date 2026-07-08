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


def _labels(layout):
    """Return the text of every QLabel directly in ``layout``, in order."""
    widgets = [layout.itemAt(i).widget() for i in range(layout.count())]
    return [w.text() for w in widgets if isinstance(w, QLabel)]


class TestResultsView:
    """Tests for ResultsView widget (Requirements 3.4, 8.1, 8.2, 8.4, 11.6)."""

    def test_hidden_by_default(self):
        """ResultsView should not be visible when first created."""
        view = ResultsView()
        assert not view.isVisible()

    def test_show_summary_makes_visible(self):
        """Calling show_summary() should make the widget visible."""
        view = ResultsView()
        summary = ResultsSummary()
        summary.add(ModuleOutcome(name="wallpaper", status=ModuleStatus.MATCHED, detail=None))
        view.show_summary(summary)
        assert view.isVisible()

    def test_counts_header_displays_correctly(self):
        """The counts header should show
        'Matched: X | Partial: Y | Failed: Z | Skipped: W' (Req 8.1)."""
        view = ResultsView()
        summary = ResultsSummary()
        summary.add(ModuleOutcome(name="wallpaper", status=ModuleStatus.MATCHED, detail=None))
        summary.add(ModuleOutcome(name="apps", status=ModuleStatus.MATCHED, detail=None))
        summary.add(ModuleOutcome(name="taskbar", status=ModuleStatus.PARTIAL, detail="Some pins missing"))
        summary.add(ModuleOutcome(name="power", status=ModuleStatus.FAILED, detail="No admin"))
        summary.add(ModuleOutcome(name="cursors", status=ModuleStatus.SKIPPED, detail="Deselected by user"))
        view.show_summary(summary)
        assert view._counts_label.text() == "Matched: 2 | Partial: 1 | Failed: 1 | Skipped: 1"

    def test_has_four_group_boxes(self):
        """ResultsView should contain four QGroupBox sections, one per
        report status (Req 8.1)."""
        view = ResultsView()
        groups = view.findChildren(QGroupBox)
        assert len(groups) == 4
        titles = {g.title() for g in groups}
        assert titles == {"Matched", "Partial", "Failed", "Skipped"}

    def test_matched_rows_show_module_name(self):
        """Matched rows with no detail should display just the module name."""
        view = ResultsView()
        summary = ResultsSummary()
        summary.add(ModuleOutcome(name="wallpaper", status=ModuleStatus.MATCHED, detail=None))
        summary.add(ModuleOutcome(name="fonts", status=ModuleStatus.MATCHED, detail=None))
        view.show_summary(summary)

        assert _labels(view._matched_layout) == ["wallpaper", "fonts"]

    def test_matched_rows_show_reason_when_present(self):
        """A non-empty ``reason``/``detail`` is displayed for matched rows
        too, not only failed/skipped ones (Req 8.3)."""
        view = ResultsView()
        summary = ResultsSummary()
        summary.add(
            ModuleOutcome(name="fonts", status=ModuleStatus.MATCHED, detail="All fonts already present")
        )
        view.show_summary(summary)

        assert _labels(view._matched_layout) == ["fonts \u2014 All fonts already present"]

    def test_partial_rows_show_reason(self):
        """Partial rows should display 'module_name — reason'."""
        view = ResultsView()
        summary = ResultsSummary()
        summary.add(
            ModuleOutcome(name="taskbar", status=ModuleStatus.PARTIAL, detail="2 of 5 pins failed")
        )
        view.show_summary(summary)

        assert _labels(view._partial_layout) == ["taskbar \u2014 2 of 5 pins failed"]

    def test_failed_rows_show_error_message(self):
        """Failed rows should display 'module_name — error message' (Req 8.3)."""
        view = ResultsView()
        summary = ResultsSummary()
        summary.add(ModuleOutcome(name="power", status=ModuleStatus.FAILED, detail="No admin rights"))
        view.show_summary(summary)

        assert _labels(view._failed_layout) == ["power \u2014 No admin rights"]

    def test_skipped_rows_show_reason(self):
        """Skipped rows should display 'module_name — reason'."""
        view = ResultsView()
        summary = ResultsSummary()
        summary.add(ModuleOutcome(name="apps", status=ModuleStatus.SKIPPED, detail="Deselected by user"))
        view.show_summary(summary)

        assert _labels(view._skipped_layout) == ["apps \u2014 Deselected by user"]

    def test_partial_row_renders_per_item_detail(self):
        """A partial restore outcome's items render as indented per-item
        lines below the row, formatted with name/status/detail and
        expected/actual when present (Req 8.2)."""
        view = ResultsView()
        summary = ResultsSummary()
        summary.add(
            ModuleOutcome(
                name="taskbar",
                status=ModuleStatus.PARTIAL,
                detail="1 of 2 pins failed",
                items=(
                    {"name": "pin1", "status": "matched", "detail": None, "expected": None, "actual": None},
                    {
                        "name": "pin2",
                        "status": "failed",
                        "detail": "path not found",
                        "expected": "C:\\App2.lnk",
                        "actual": None,
                    },
                ),
            )
        )
        view.show_summary(summary)

        texts = _labels(view._partial_layout)
        assert texts[0] == "taskbar \u2014 1 of 2 pins failed"
        assert texts[1] == "pin1: matched"
        assert texts[2] == "pin2: failed \u2014 path not found (expected='C:\\\\App2.lnk')"

    def test_failed_row_renders_per_item_detail(self):
        """A failed restore outcome's items render as indented per-item
        lines, including both expected and actual when both are present
        (Req 8.2)."""
        view = ResultsView()
        summary = ResultsSummary()
        summary.add(
            ModuleOutcome(
                name="env_vars",
                status=ModuleStatus.FAILED,
                detail="mismatch after apply",
                items=(
                    {
                        "name": "PATH",
                        "status": "failed",
                        "detail": "value differs",
                        "expected": "C:\\bin",
                        "actual": "C:\\other",
                    },
                ),
            )
        )
        view.show_summary(summary)

        texts = _labels(view._failed_layout)
        assert texts[0] == "env_vars \u2014 mismatch after apply"
        assert texts[1] == "PATH: failed \u2014 value differs (expected='C:\\\\bin', actual='C:\\\\other')"

    def test_matched_and_skipped_rows_do_not_render_item_detail(self):
        """Per-item detail is only rendered for partial/failed rows, even
        if items happen to be present on a matched/skipped outcome."""
        view = ResultsView()
        summary = ResultsSummary()
        summary.add(
            ModuleOutcome(
                name="fonts",
                status=ModuleStatus.MATCHED,
                detail=None,
                items=({"name": "Arial", "status": "matched", "detail": None, "expected": None, "actual": None},),
            )
        )
        summary.add(
            ModuleOutcome(
                name="apps",
                status=ModuleStatus.SKIPPED,
                detail="Deselected by user",
                items=({"name": "app1", "status": "skipped", "detail": None, "expected": None, "actual": None},),
            )
        )
        view.show_summary(summary)

        assert _labels(view._matched_layout) == ["fonts"]
        assert _labels(view._skipped_layout) == ["apps \u2014 Deselected by user"]

    def test_verify_outcome_appends_suffix_to_matching_row(self):
        """When verify_outcomes is non-empty, a row whose module has a
        matching verify outcome appends
        ' | verify: <status> (<reason>)' (Req 3.4, 8.4)."""
        view = ResultsView()
        summary = ResultsSummary()
        summary.add(ModuleOutcome(name="wallpaper", status=ModuleStatus.MATCHED, detail=None))
        summary.add_verify(
            ModuleOutcome(name="wallpaper", status=ModuleStatus.FAILED, detail="wallpaper reverted")
        )
        view.show_summary(summary)

        assert _labels(view._matched_layout) == [
            "wallpaper | verify: failed (wallpaper reverted)"
        ]

    def test_verify_outcome_without_detail_omits_parens(self):
        """A verify outcome with no detail appends just the status, no
        empty parens."""
        view = ResultsView()
        summary = ResultsSummary()
        summary.add(ModuleOutcome(name="wallpaper", status=ModuleStatus.MATCHED, detail=None))
        summary.add_verify(ModuleOutcome(name="wallpaper", status=ModuleStatus.MATCHED, detail=None))
        view.show_summary(summary)

        assert _labels(view._matched_layout) == ["wallpaper | verify: matched"]

    def test_verify_outcome_items_render_with_verify_prefix(self):
        """When the verify outcome itself is partial/failed, its items
        render too, prefixed '[verify]' to disambiguate from the restore
        item block (Req 3.4, 8.2, 8.4)."""
        view = ResultsView()
        summary = ResultsSummary()
        summary.add(ModuleOutcome(name="taskbar", status=ModuleStatus.MATCHED, detail=None))
        summary.add_verify(
            ModuleOutcome(
                name="taskbar",
                status=ModuleStatus.PARTIAL,
                detail="drifted after restart",
                items=(
                    {
                        "name": "pin1",
                        "status": "failed",
                        "detail": "missing",
                        "expected": None,
                        "actual": None,
                    },
                ),
            )
        )
        view.show_summary(summary)

        texts = _labels(view._matched_layout)
        assert texts[0] == "taskbar | verify: partial (drifted after restart)"
        assert texts[1] == "[verify] pin1: failed \u2014 missing"

    def test_empty_verify_outcomes_renders_no_verify_text(self):
        """When summary.verify_outcomes is globally empty, zero verify
        text renders anywhere, even for modules that would otherwise
        match a verify outcome by name (Req 3.5)."""
        view = ResultsView()
        summary = ResultsSummary()
        summary.add(ModuleOutcome(name="wallpaper", status=ModuleStatus.MATCHED, detail=None))
        summary.add(ModuleOutcome(name="power", status=ModuleStatus.FAILED, detail="No admin"))
        view.show_summary(summary)

        assert summary.verify_outcomes == []
        all_texts = (
            _labels(view._matched_layout)
            + _labels(view._partial_layout)
            + _labels(view._failed_layout)
            + _labels(view._skipped_layout)
        )
        assert not any("verify" in text for text in all_texts)

    def test_show_summary_clears_previous_content(self):
        """Calling show_summary() again should replace previous content."""
        view = ResultsView()

        # First summary
        summary1 = ResultsSummary()
        summary1.add(ModuleOutcome(name="wallpaper", status=ModuleStatus.MATCHED, detail=None))
        summary1.add(ModuleOutcome(name="apps", status=ModuleStatus.MATCHED, detail=None))
        view.show_summary(summary1)
        assert view._counts_label.text() == "Matched: 2 | Partial: 0 | Failed: 0 | Skipped: 0"

        # Second summary (different content)
        summary2 = ResultsSummary()
        summary2.add(ModuleOutcome(name="power", status=ModuleStatus.FAILED, detail="Error"))
        view.show_summary(summary2)
        assert view._counts_label.text() == "Matched: 0 | Partial: 0 | Failed: 1 | Skipped: 0"
        assert view._matched_layout.count() == 0

    def test_empty_summary(self):
        """An empty summary should show all zeros and no rows."""
        view = ResultsView()
        summary = ResultsSummary()
        view.show_summary(summary)
        assert view._counts_label.text() == "Matched: 0 | Partial: 0 | Failed: 0 | Skipped: 0"
        assert view._matched_layout.count() == 0
        assert view._partial_layout.count() == 0
        assert view._failed_layout.count() == 0
        assert view._skipped_layout.count() == 0
        assert view.isVisible()
