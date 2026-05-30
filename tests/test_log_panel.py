"""Unit tests for the LogPanel widget.

Tests run with QT_QPA_PLATFORM=offscreen so no display is required.
"""

import os
import sys

import pytest

# Force offscreen rendering for headless CI
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QTextEdit, QPushButton

# Ensure a QApplication exists before any widget tests
_app = QApplication.instance() or QApplication(sys.argv)

from gui import LogPanel, LogEntry, Severity, format_log_line


class TestLogPanelInitialState:
    """Tests for LogPanel initial configuration."""

    def test_text_edit_is_read_only(self):
        """The QTextEdit should be read-only (Requirement 11)."""
        panel = LogPanel()
        text_edit = panel.findChild(QTextEdit)
        assert text_edit is not None
        assert text_edit.isReadOnly()

    def test_has_clear_button(self):
        """LogPanel should have a Clear button (Requirement 13.1)."""
        panel = LogPanel()
        clear_btn = panel._clear_btn
        assert clear_btn is not None
        assert clear_btn.text() == "Clear"

    def test_has_copy_button(self):
        """LogPanel should have a Copy button (Requirement 13.1)."""
        panel = LogPanel()
        copy_btn = panel._copy_btn
        assert copy_btn is not None
        assert copy_btn.text() == "Copy"

    def test_starts_empty(self):
        """LogPanel should start with no entries."""
        panel = LogPanel()
        assert panel.plain_text() == ""


class TestLogPanelAppend:
    """Tests for LogPanel.append() method."""

    def test_append_single_entry(self):
        """Appending a single entry should be reflected in plain_text."""
        panel = LogPanel()
        entry = LogEntry(timestamp="12:30:45", message="Hello", severity=Severity.SUCCESS)
        panel.append(entry)
        assert panel.plain_text() == "12:30:45  Hello"

    def test_append_multiple_entries(self):
        """Appending multiple entries should accumulate them in order."""
        panel = LogPanel()
        entries = [
            LogEntry(timestamp="12:30:45", message="First", severity=Severity.SUCCESS),
            LogEntry(timestamp="12:30:46", message="Second", severity=Severity.WARNING),
            LogEntry(timestamp="12:30:47", message="Third", severity=Severity.ERROR),
        ]
        for entry in entries:
            panel.append(entry)

        expected = "\n".join(format_log_line(e) for e in entries)
        assert panel.plain_text() == expected

    def test_append_persists_across_operations(self):
        """Entries from prior operations persist until clear (Requirement 11.3)."""
        panel = LogPanel()
        panel.append(LogEntry(timestamp="10:00:00", message="Op1", severity=Severity.SUCCESS))
        panel.append(LogEntry(timestamp="11:00:00", message="Op2", severity=Severity.SUCCESS))
        assert "Op1" in panel.plain_text()
        assert "Op2" in panel.plain_text()


class TestLogPanelColorMapping:
    """Tests for severity color rendering (Requirements 12.1, 12.2, 12.3)."""

    def test_success_renders_green(self):
        """SUCCESS entries should be rendered in green."""
        panel = LogPanel()
        entry = LogEntry(timestamp="12:00:00", message="All good", severity=Severity.SUCCESS)
        panel.append(entry)
        html = panel._text_edit.toHtml()
        # Qt converts "green" to its hex equivalent #008000
        assert "#008000" in html.lower() or "green" in html.lower()

    def test_warning_renders_amber(self):
        """WARNING entries should be rendered in amber (#FFC107)."""
        panel = LogPanel()
        entry = LogEntry(timestamp="12:00:00", message="Watch out", severity=Severity.WARNING)
        panel.append(entry)
        html = panel._text_edit.toHtml()
        # Amber color is #FFC107
        assert "#ffc107" in html.lower() or "ffc107" in html.lower()

    def test_error_renders_red(self):
        """ERROR entries should be rendered in red."""
        panel = LogPanel()
        entry = LogEntry(timestamp="12:00:00", message="Failure", severity=Severity.ERROR)
        panel.append(entry)
        html = panel._text_edit.toHtml()
        # Qt converts "red" to its hex equivalent #ff0000
        assert "#ff0000" in html.lower() or "red" in html.lower()


class TestLogPanelClear:
    """Tests for LogPanel.clear() method (Requirement 13.2)."""

    def test_clear_removes_all_entries(self):
        """Clearing should remove all entries."""
        panel = LogPanel()
        panel.append(LogEntry(timestamp="12:00:00", message="Entry", severity=Severity.SUCCESS))
        panel.clear()
        assert panel.plain_text() == ""

    def test_clear_empties_text_edit(self):
        """Clearing should empty the QTextEdit display."""
        panel = LogPanel()
        panel.append(LogEntry(timestamp="12:00:00", message="Entry", severity=Severity.SUCCESS))
        panel.clear()
        text_edit = panel._text_edit
        assert text_edit.toPlainText().strip() == ""

    def test_append_after_clear(self):
        """Appending after clear should start fresh."""
        panel = LogPanel()
        panel.append(LogEntry(timestamp="12:00:00", message="Before", severity=Severity.SUCCESS))
        panel.clear()
        panel.append(LogEntry(timestamp="13:00:00", message="After", severity=Severity.WARNING))
        assert panel.plain_text() == "13:00:00  After"


class TestLogPanelCopy:
    """Tests for LogPanel.copy() method (Requirements 13.3, 13.4)."""

    def test_copy_places_text_on_clipboard(self):
        """Copy should place plain_text on the system clipboard."""
        panel = LogPanel()
        panel.append(LogEntry(timestamp="12:00:00", message="Copied", severity=Severity.SUCCESS))
        panel.copy()
        clipboard = QApplication.clipboard()
        assert clipboard.text() == "12:00:00  Copied"

    def test_copy_empty_log_places_empty_string(self):
        """Copying an empty log should place an empty string on clipboard (Requirement 13.4)."""
        panel = LogPanel()
        panel.copy()
        clipboard = QApplication.clipboard()
        assert clipboard.text() == ""


class TestLogPanelAutoScroll:
    """Tests for auto-scroll behavior (Requirement 11.4)."""

    def test_scrollbar_at_bottom_after_append(self):
        """After appending, the scrollbar should be at the bottom."""
        panel = LogPanel()
        # Append many entries to ensure scrollbar is needed
        for i in range(50):
            panel.append(
                LogEntry(timestamp=f"12:00:{i:02d}", message=f"Line {i}", severity=Severity.SUCCESS)
            )
        scrollbar = panel._text_edit.verticalScrollBar()
        assert scrollbar.value() == scrollbar.maximum()
