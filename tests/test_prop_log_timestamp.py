"""
test_prop_log_timestamp.py — Property-based test for log line timestamp prefix.

Feature: winsnap-gui, Property 11: Log line timestamp prefix

For any LogEntry, `format_log_line` SHALL produce a string that begins with
the entry's timestamp formatted as HH:MM:SS.

**Validates: Requirements 11.1**
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from hypothesis import given, settings
from hypothesis import strategies as st

from gui import format_log_line, LogEntry, Severity

# Strategy: generate valid HH:MM:SS timestamps
hours = st.integers(min_value=0, max_value=23)
minutes = st.integers(min_value=0, max_value=59)
seconds = st.integers(min_value=0, max_value=59)

timestamps = st.builds(
    lambda h, m, s: f"{h:02d}:{m:02d}:{s:02d}",
    hours, minutes, seconds,
)

# Strategy: arbitrary message text
messages = st.text(min_size=0, max_size=200)

# Strategy: arbitrary Severity
severities = st.sampled_from(list(Severity))

# Strategy: generate LogEntry instances
log_entries = st.builds(LogEntry, timestamp=timestamps, message=messages, severity=severities)


@settings(max_examples=200)
@given(entry=log_entries)
def test_format_log_line_starts_with_timestamp(entry):
    """Property 11: format_log_line produces a string that begins with the
    entry's timestamp formatted as HH:MM:SS."""
    result = format_log_line(entry)

    # The result must start with the entry's timestamp
    assert result.startswith(entry.timestamp), (
        f"Expected result to start with {entry.timestamp!r}, "
        f"got {result!r}"
    )

    # The timestamp prefix must match the HH:MM:SS pattern
    assert re.match(r"\d{2}:\d{2}:\d{2}", result), (
        f"Expected result to start with a \\d{{2}}:\\d{{2}}:\\d{{2}} pattern, "
        f"got {result!r}"
    )
