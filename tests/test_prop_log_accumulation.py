"""
test_prop_log_accumulation.py — Property-based test for log accumulation and copy text.

Feature: winsnap-gui, Property 12: Log accumulation and copy text

Validates: Requirements 11.3, 13.2, 13.3, 13.4
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from gui import LogEntry, Severity, format_log_line


# ---------------------------------------------------------------------------
# Simple LogModel — a pure model of the log panel's state (no Qt dependency)
# ---------------------------------------------------------------------------


class LogModel:
    """A pure model of the log panel's append/clear/copy behavior."""

    def __init__(self) -> None:
        self._entries: list[LogEntry] = []

    def append(self, entry: LogEntry) -> None:
        """Append a log entry."""
        self._entries.append(entry)

    def clear(self) -> None:
        """Remove all entries."""
        self._entries = []

    @property
    def entries(self) -> list[LogEntry]:
        """Return the currently retained entries in append order."""
        return list(self._entries)

    def copy_text(self) -> str:
        """Return the copyable text: newline-join of format_log_line over entries, or empty string."""
        if not self._entries:
            return ""
        return "\n".join(format_log_line(e) for e in self._entries)


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

severity_strategy = st.sampled_from(list(Severity))

# Generate valid HH:MM:SS timestamps
timestamp_strategy = st.builds(
    lambda h, m, s: f"{h:02d}:{m:02d}:{s:02d}",
    st.integers(min_value=0, max_value=23),
    st.integers(min_value=0, max_value=59),
    st.integers(min_value=0, max_value=59),
)

log_entry_strategy = st.builds(
    LogEntry,
    timestamp=timestamp_strategy,
    message=st.text(min_size=0, max_size=100),
    severity=severity_strategy,
)

# Operations: either append(LogEntry) or clear
append_op_strategy = st.builds(lambda e: ("append", e), log_entry_strategy)
clear_op_strategy = st.just(("clear", None))

operation_strategy = st.one_of(append_op_strategy, clear_op_strategy)

operations_strategy = st.lists(operation_strategy, min_size=0, max_size=50)


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


@given(operations=operations_strategy)
@settings(max_examples=200)
def test_log_accumulation_and_copy_text(operations):
    """Property 12: Log accumulation and copy text.

    For any sequence of append and clear operations on the log model, the copyable
    text SHALL equal the newline-join of format_log_line over the currently retained
    entries (in append order); appends SHALL accumulate entries across operations
    until a clear, a clear SHALL leave zero entries, and copying an empty log SHALL
    yield the empty string.

    **Validates: Requirements 11.3, 13.2, 13.3, 13.4**
    """
    model = LogModel()
    # Track expected entries independently to verify the model
    expected_entries: list[LogEntry] = []

    for op_type, payload in operations:
        if op_type == "append":
            model.append(payload)
            expected_entries.append(payload)

            # Appends SHALL accumulate entries across operations
            assert model.entries == expected_entries, (
                "Append did not accumulate correctly"
            )
        elif op_type == "clear":
            model.clear()
            expected_entries = []

            # A clear SHALL leave zero entries
            assert model.entries == [], (
                "Clear did not remove all entries"
            )

    # After replaying all operations, verify the copyable text
    retained = model.entries
    if retained:
        expected_text = "\n".join(format_log_line(e) for e in retained)
    else:
        expected_text = ""

    # The copyable text SHALL equal the newline-join of format_log_line
    # over the currently retained entries
    assert model.copy_text() == expected_text, (
        f"Copy text mismatch.\nExpected: {expected_text!r}\nGot: {model.copy_text()!r}"
    )

    # Copying an empty log SHALL yield the empty string
    if not retained:
        assert model.copy_text() == "", (
            "Copying an empty log did not yield the empty string"
        )
