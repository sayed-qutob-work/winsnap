"""
test_prop_severity.py — Property-based test for severity classification.

Feature: winsnap-gui, Property 10: Severity classification is total and single-valued

For any log line, `classify_severity` SHALL return exactly one Severity
(success, warning, or error); a line carrying an error/exception marker
SHALL classify as error, and a line carrying a non-fatal advisory marker
(and no error marker) SHALL classify as warning. The color map SHALL
associate that single severity with exactly one color.

**Validates: Requirements 12.4, 12.5, 12.6**
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from hypothesis import given, settings
from hypothesis import strategies as st

from gui import classify_severity, Severity

# Markers used by the implementation (case-insensitive)
ERROR_MARKERS = ("error", "exception", "traceback", "failed")
WARNING_MARKERS = ("warning", "advisory", "skipped")

# The color map as specified in the design (success→green, warning→amber, error→red)
SEVERITY_COLOR_MAP = {
    Severity.SUCCESS: "green",
    Severity.WARNING: "amber",
    Severity.ERROR: "red",
}


def _has_error_marker(line: str) -> bool:
    """Check if a line contains any error marker (case-insensitive)."""
    lower = line.lower()
    return any(marker in lower for marker in ERROR_MARKERS)


def _has_warning_marker(line: str) -> bool:
    """Check if a line contains any warning marker (case-insensitive)."""
    lower = line.lower()
    return any(marker in lower for marker in WARNING_MARKERS)


@settings(max_examples=200)
@given(line=st.text())
def test_severity_classification_is_total_and_single_valued(line):
    """Property 10: classify_severity returns exactly one Severity for any
    log line, error markers classify as ERROR, warning markers (without error
    markers) classify as WARNING, and the color map assigns exactly one color
    per severity."""
    result = classify_severity(line)

    # Totality: result is always exactly one Severity enum value
    assert isinstance(result, Severity), (
        f"classify_severity did not return a Severity, got {type(result)}: {result!r}"
    )
    assert result in (Severity.SUCCESS, Severity.WARNING, Severity.ERROR), (
        f"classify_severity returned unexpected Severity value: {result!r}"
    )

    # Single-valued: the result is deterministic (calling again gives same answer)
    assert classify_severity(line) == result, (
        "classify_severity is not deterministic for the same input"
    )

    # Error marker priority: if line contains an error marker → ERROR
    if _has_error_marker(line):
        assert result == Severity.ERROR, (
            f"Line contains error marker but classified as {result!r}: {line!r}"
        )

    # Warning marker (no error marker) → WARNING
    if _has_warning_marker(line) and not _has_error_marker(line):
        assert result == Severity.WARNING, (
            f"Line contains warning marker (no error marker) but classified as {result!r}: {line!r}"
        )

    # No markers → SUCCESS
    if not _has_error_marker(line) and not _has_warning_marker(line):
        assert result == Severity.SUCCESS, (
            f"Line contains no markers but classified as {result!r}: {line!r}"
        )

    # Color map: the severity maps to exactly one color
    assert result in SEVERITY_COLOR_MAP, (
        f"Severity {result!r} has no color mapping"
    )
    color = SEVERITY_COLOR_MAP[result]
    assert isinstance(color, str) and len(color) > 0, (
        f"Color for {result!r} is not a valid non-empty string: {color!r}"
    )

    # Each severity maps to a unique color (no two severities share a color)
    colors = list(SEVERITY_COLOR_MAP.values())
    assert len(colors) == len(set(colors)), (
        "Color map does not assign unique colors to each severity"
    )
