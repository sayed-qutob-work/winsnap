"""Unit tests for LogStream file-like object.

Tests verify that LogStream correctly buffers text, splits on newlines,
emits log_line signals with classified severity, and handles flush().

Tests run with QT_QPA_PLATFORM=offscreen so no display is required.

Requirements: 11.2, 12.4, 12.5, 12.6
"""

import os
import sys

# Force offscreen rendering for headless CI
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

# Ensure a QApplication exists before any QObject tests
_app = QApplication.instance() or QApplication(sys.argv)

from gui import LogStream, Severity


class TestLogStreamWrite:
    """Tests for LogStream.write() method."""

    def test_complete_line_emits_signal(self):
        """A complete line (ending with newline) should emit log_line signal."""
        stream = LogStream()
        emitted = []
        stream.log_line.connect(lambda line, sev: emitted.append((line, sev)))

        stream.write("hello world\n")

        assert len(emitted) == 1
        assert emitted[0][0] == "hello world"
        assert emitted[0][1] == Severity.SUCCESS

    def test_multiple_lines_in_one_write(self):
        """Multiple newlines in one write should emit multiple signals."""
        stream = LogStream()
        emitted = []
        stream.log_line.connect(lambda line, sev: emitted.append((line, sev)))

        stream.write("line1\nline2\nline3\n")

        assert len(emitted) == 3
        assert emitted[0][0] == "line1"
        assert emitted[1][0] == "line2"
        assert emitted[2][0] == "line3"

    def test_partial_line_buffered(self):
        """Text without a trailing newline should be buffered, not emitted."""
        stream = LogStream()
        emitted = []
        stream.log_line.connect(lambda line, sev: emitted.append((line, sev)))

        stream.write("partial")

        assert len(emitted) == 0

    def test_partial_then_newline_emits(self):
        """Buffered partial text should emit when newline arrives."""
        stream = LogStream()
        emitted = []
        stream.log_line.connect(lambda line, sev: emitted.append((line, sev)))

        stream.write("hello ")
        stream.write("world\n")

        assert len(emitted) == 1
        assert emitted[0][0] == "hello world"

    def test_returns_len_of_text(self):
        """write() should return len(text) for file-like compatibility."""
        stream = LogStream()
        assert stream.write("hello\n") == 6
        assert stream.write("partial") == 7
        assert stream.write("") == 0

    def test_error_severity_classification(self):
        """Lines with error markers should emit with ERROR severity."""
        stream = LogStream()
        emitted = []
        stream.log_line.connect(lambda line, sev: emitted.append((line, sev)))

        stream.write("Something failed with an error\n")

        assert emitted[0][1] == Severity.ERROR

    def test_warning_severity_classification(self):
        """Lines with warning markers should emit with WARNING severity."""
        stream = LogStream()
        emitted = []
        stream.log_line.connect(lambda line, sev: emitted.append((line, sev)))

        stream.write("This is a warning message\n")

        assert emitted[0][1] == Severity.WARNING

    def test_empty_write(self):
        """Writing an empty string should not emit anything."""
        stream = LogStream()
        emitted = []
        stream.log_line.connect(lambda line, sev: emitted.append((line, sev)))

        stream.write("")

        assert len(emitted) == 0

    def test_empty_line(self):
        """A bare newline should emit an empty string line."""
        stream = LogStream()
        emitted = []
        stream.log_line.connect(lambda line, sev: emitted.append((line, sev)))

        stream.write("\n")

        assert len(emitted) == 1
        assert emitted[0][0] == ""


class TestLogStreamFlush:
    """Tests for LogStream.flush() method."""

    def test_flush_emits_buffered_text(self):
        """flush() should emit any remaining buffered text as a line."""
        stream = LogStream()
        emitted = []
        stream.log_line.connect(lambda line, sev: emitted.append((line, sev)))

        stream.write("no trailing newline")
        stream.flush()

        assert len(emitted) == 1
        assert emitted[0][0] == "no trailing newline"

    def test_flush_clears_buffer(self):
        """After flush(), the buffer should be empty."""
        stream = LogStream()
        emitted = []
        stream.log_line.connect(lambda line, sev: emitted.append((line, sev)))

        stream.write("buffered")
        stream.flush()
        stream.flush()  # second flush should be a no-op

        assert len(emitted) == 1

    def test_flush_empty_buffer_is_noop(self):
        """flush() on an empty buffer should not emit anything."""
        stream = LogStream()
        emitted = []
        stream.log_line.connect(lambda line, sev: emitted.append((line, sev)))

        stream.flush()

        assert len(emitted) == 0

    def test_flush_classifies_severity(self):
        """flush() should classify the buffered text's severity."""
        stream = LogStream()
        emitted = []
        stream.log_line.connect(lambda line, sev: emitted.append((line, sev)))

        stream.write("an error occurred")
        stream.flush()

        assert emitted[0][1] == Severity.ERROR


class TestLogStreamRedirect:
    """Tests for LogStream compatibility with contextlib.redirect_stdout."""

    def test_redirect_stdout_captures_print(self):
        """LogStream should work with contextlib.redirect_stdout to capture print()."""
        import contextlib

        stream = LogStream()
        emitted = []
        stream.log_line.connect(lambda line, sev: emitted.append((line, sev)))

        with contextlib.redirect_stdout(stream):
            print("captured output")

        assert len(emitted) == 1
        assert emitted[0][0] == "captured output"

    def test_redirect_stdout_multiple_prints(self):
        """Multiple print() calls should each emit a line."""
        import contextlib

        stream = LogStream()
        emitted = []
        stream.log_line.connect(lambda line, sev: emitted.append((line, sev)))

        with contextlib.redirect_stdout(stream):
            print("first")
            print("second")
            print("third")

        assert len(emitted) == 3
        assert emitted[0][0] == "first"
        assert emitted[1][0] == "second"
        assert emitted[2][0] == "third"
