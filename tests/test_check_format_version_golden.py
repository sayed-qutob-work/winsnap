"""
test_check_format_version_golden.py — Golden-output table test for
restore._check_format_version.

Feature: gui-backend-alignment, Task 1.2 (Req 7.3, 11.1)

This test captures the exact printed diagnostics and boolean return value of
_check_format_version for a representative table of snapshot metadata
shapes, recorded against the CURRENT (pre-refactor) implementation (the
inline fallback-chain/MAJOR-parsing check) BEFORE it is rewritten into a
thin wrapper over evaluate_snapshot_version. Re-running this unmodified test
after the refactor is what demonstrates Req 11.1's "byte-identical CLI
behavior" for this specific extraction: same printed lines, same return
value, for every case below.

Cases cover: both version keys absent (falls back to "0.1.0"); only
winsnap_version present (falls back past a missing snapshot_format_version);
both present (snapshot_format_version takes precedence); an unparseable
string; a non-string raw value that is also unparseable (exercises the
{raw!r} vs {str(raw)!r} repr-parity edge case called out in design.md);
MAJOR exactly at the supported boundary; MAJOR one above the supported
boundary.
"""

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from restore import _check_format_version

# Each entry: (case_name, snapshot_dict, expected_return, expected_stdout)
# expected_stdout/expected_return captured verbatim from the pre-refactor
# _check_format_version (see task 1.2 baseline capture).
GOLDEN_CASES = [
    (
        "both_absent",
        {},
        True,
        "",
    ),
    (
        "missing_format_falls_back_to_winsnap",
        {"winsnap_version": "0.9.0"},
        True,
        "",
    ),
    (
        "format_version_precedence",
        {"snapshot_format_version": "0.2.0", "winsnap_version": "9.9.9"},
        True,
        "",
    ),
    (
        "unparseable_string",
        {"snapshot_format_version": "not-a-version"},
        True,
        "  WARNING: unrecognized version format 'not-a-version', "
        "attempting restore anyway.\n",
    ),
    (
        "non_string_raw_unparseable",
        {"snapshot_format_version": [1, 2]},
        True,
        "  WARNING: unrecognized version format [1, 2], "
        "attempting restore anyway.\n",
    ),
    (
        "major_within_supported_boundary",
        {"snapshot_format_version": "0.99.99"},
        True,
        "",
    ),
    (
        "major_above_supported",
        {"snapshot_format_version": "1.0.0"},
        False,
        "  ERROR: snapshot format v1.0.0 is newer than this restorer "
        "supports (v0.x). Update WinSnap and try again.\n",
    ),
]


@pytest.mark.parametrize(
    "case_name,snapshot,expected_return,expected_stdout",
    GOLDEN_CASES,
    ids=[c[0] for c in GOLDEN_CASES],
)
def test_check_format_version_golden(case_name, snapshot, expected_return, expected_stdout):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = _check_format_version(snapshot)
    assert result == expected_return, (
        f"{case_name}: return value changed (got {result!r}, "
        f"expected {expected_return!r})"
    )
    assert buf.getvalue() == expected_stdout, (
        f"{case_name}: printed output changed (got {buf.getvalue()!r}, "
        f"expected {expected_stdout!r})"
    )
