"""Unit tests for MainWindow pre-start validation guards.

Tests run with QT_QPA_PLATFORM=offscreen so no display is required.
Tests for try_start_export() and try_start_restore() methods.
"""

import os
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Force offscreen rendering for headless CI
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

# Ensure a QApplication exists before any widget tests
_app = QApplication.instance() or QApplication(sys.argv)

from gui import LogEntry, MainWindow, Severity


class TestTryStartExportGuards:
    """Tests for MainWindow.try_start_export() validation guards."""

    def test_rejects_when_operation_in_progress(self):
        """Should return False and emit warning when an operation is already running (Req 1.4)."""
        window = MainWindow()
        window._operation_in_progress = True
        result = window.try_start_export()
        assert result is False
        assert len(window._log_panel._entries) == 1
        entry = window._log_panel._entries[0]
        assert entry.severity == Severity.WARNING
        assert "already in progress" in entry.message

    @patch("gui.sys")
    def test_rejects_on_non_windows(self, mock_sys):
        """Should return False and emit error on non-Windows host (Req 1.5)."""
        mock_sys.platform = "linux"
        window = MainWindow()
        # Need to patch at module level since sys is imported
        with patch("gui.sys.platform", "linux"):
            result = window.try_start_export()
        assert result is False
        assert len(window._log_panel._entries) == 1
        entry = window._log_panel._entries[0]
        assert entry.severity == Severity.ERROR
        assert "Windows only" in entry.message

    def test_rejects_invalid_snapshot_name(self):
        """Should return False and emit error for invalid snapshot name (Req 2.7)."""
        window = MainWindow()
        # Set a name with forbidden characters
        window._export_view._name_edit.setText("my<snapshot")
        with patch("gui.sys.platform", "win32"):
            result = window.try_start_export()
        assert result is False
        assert len(window._log_panel._entries) == 1
        entry = window._log_panel._entries[0]
        assert entry.severity == Severity.ERROR
        assert "forbidden character" in entry.message

    def test_rejects_zero_modules_selected(self):
        """Should return False and emit error when no modules selected (Req 3.8)."""
        window = MainWindow()
        window._export_view._module_selector.set_all(False)
        with patch("gui.sys.platform", "win32"):
            result = window.try_start_export()
        assert result is False
        assert len(window._log_panel._entries) == 1
        entry = window._log_panel._entries[0]
        assert entry.severity == Severity.ERROR
        assert "at least one module" in entry.message.lower()

    def test_passes_all_guards_with_valid_config(self):
        """Should return True when all guards pass."""
        window = MainWindow()
        # Default config: all modules selected, no name (auto-generated)
        with patch("gui.sys.platform", "win32"):
            result = window.try_start_export()
        assert result is True
        assert len(window._log_panel._entries) == 0

    def test_passes_with_valid_custom_name(self):
        """Should return True when a valid custom name is provided."""
        window = MainWindow()
        window._export_view._name_edit.setText("my_valid_snapshot")
        with patch("gui.sys.platform", "win32"):
            result = window.try_start_export()
        assert result is True
        assert len(window._log_panel._entries) == 0

    def test_operation_in_progress_checked_first(self):
        """Operation-in-progress guard should be checked before other guards."""
        window = MainWindow()
        window._operation_in_progress = True
        # Also set invalid state that would trigger other guards
        window._export_view._module_selector.set_all(False)
        result = window.try_start_export()
        assert result is False
        # Only the "already in progress" message should appear
        assert len(window._log_panel._entries) == 1
        assert "already in progress" in window._log_panel._entries[0].message


class TestTryStartRestoreGuards:
    """Tests for MainWindow.try_start_restore() validation guards."""

    def test_rejects_when_operation_in_progress(self):
        """Should return False and emit warning when an operation is already running (Req 1.4)."""
        window = MainWindow()
        window._operation_in_progress = True
        result = window.try_start_restore()
        assert result is False
        assert len(window._log_panel._entries) == 1
        entry = window._log_panel._entries[0]
        assert entry.severity == Severity.WARNING
        assert "already in progress" in entry.message

    def test_rejects_on_non_windows(self):
        """Should return False and emit error on non-Windows host (Req 1.5)."""
        window = MainWindow()
        with patch("gui.sys.platform", "linux"):
            result = window.try_start_restore()
        assert result is False
        assert len(window._log_panel._entries) == 1
        entry = window._log_panel._entries[0]
        assert entry.severity == Severity.ERROR
        assert "Windows only" in entry.message

    def test_rejects_no_file_selected(self):
        """Should return False and emit error when no snapshot file selected (Req 8.2)."""
        window = MainWindow()
        # Default state: no file selected (snapshot_path is Path(""))
        with patch("gui.sys.platform", "win32"):
            result = window.try_start_restore()
        assert result is False
        assert len(window._log_panel._entries) == 1
        entry = window._log_panel._entries[0]
        assert entry.severity == Severity.ERROR
        assert "snapshot file must be selected" in entry.message.lower()

    def test_rejects_nonexistent_file(self):
        """Should return False and emit error when file does not exist (Req 8.3)."""
        window = MainWindow()
        window._restore_view._snapshot_path = Path("C:/nonexistent/file.winsnap")
        with patch("gui.sys.platform", "win32"):
            result = window.try_start_restore()
        assert result is False
        assert len(window._log_panel._entries) == 1
        entry = window._log_panel._entries[0]
        assert entry.severity == Severity.ERROR
        assert "File not found" in entry.message

    def test_rejects_invalid_archive(self):
        """Should return False and emit error when file is not a valid zip (Req 8.4)."""
        window = MainWindow()
        # Create a temp file that is not a valid zip
        with tempfile.NamedTemporaryFile(suffix=".winsnap", delete=False) as f:
            f.write(b"this is not a zip file")
            temp_path = Path(f.name)
        try:
            window._restore_view._snapshot_path = temp_path
            with patch("gui.sys.platform", "win32"):
                result = window.try_start_restore()
            assert result is False
            assert len(window._log_panel._entries) == 1
            entry = window._log_panel._entries[0]
            assert entry.severity == Severity.ERROR
            assert "Not a valid snapshot" in entry.message
        finally:
            temp_path.unlink(missing_ok=True)

    def test_rejects_zero_modules_selected(self):
        """Should return False and emit error when no modules selected (Req 3.8)."""
        window = MainWindow()
        # Create a valid zip file
        with tempfile.NamedTemporaryFile(suffix=".winsnap", delete=False) as f:
            temp_path = Path(f.name)
        try:
            with zipfile.ZipFile(temp_path, "w") as zf:
                zf.writestr("test.txt", "test content")
            window._restore_view._snapshot_path = temp_path
            window._restore_view._module_selector.set_all(False)
            with patch("gui.sys.platform", "win32"):
                result = window.try_start_restore()
            assert result is False
            assert len(window._log_panel._entries) == 1
            entry = window._log_panel._entries[0]
            assert entry.severity == Severity.ERROR
            assert "at least one module" in entry.message.lower()
        finally:
            temp_path.unlink(missing_ok=True)

    def test_passes_all_guards_with_valid_config(self):
        """Should return True when all guards pass."""
        window = MainWindow()
        # Create a valid zip file
        with tempfile.NamedTemporaryFile(suffix=".winsnap", delete=False) as f:
            temp_path = Path(f.name)
        try:
            with zipfile.ZipFile(temp_path, "w") as zf:
                zf.writestr("test.txt", "test content")
            window._restore_view._snapshot_path = temp_path
            with patch("gui.sys.platform", "win32"):
                result = window.try_start_restore()
            assert result is True
            assert len(window._log_panel._entries) == 0
        finally:
            temp_path.unlink(missing_ok=True)

    def test_operation_in_progress_checked_first(self):
        """Operation-in-progress guard should be checked before other guards."""
        window = MainWindow()
        window._operation_in_progress = True
        # Also set invalid state that would trigger other guards
        window._restore_view._module_selector.set_all(False)
        result = window.try_start_restore()
        assert result is False
        # Only the "already in progress" message should appear
        assert len(window._log_panel._entries) == 1
        assert "already in progress" in window._log_panel._entries[0].message

    def test_log_entries_have_timestamp(self):
        """All emitted log entries should have a valid HH:MM:SS timestamp."""
        window = MainWindow()
        window._operation_in_progress = True
        window.try_start_restore()
        entry = window._log_panel._entries[0]
        # Timestamp should match HH:MM:SS format
        import re
        assert re.match(r"\d{2}:\d{2}:\d{2}", entry.timestamp)
