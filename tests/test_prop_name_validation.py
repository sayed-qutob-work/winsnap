"""
test_prop_name_validation.py — Property-based test for snapshot name validation.

Feature: winsnap-gui, Property 2: Snapshot name validation

Validates: Requirements 2.7, 2.3
"""

import re
import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from gui import validate_snapshot_name

# ---------------------------------------------------------------------------
# Reference validation logic (independent oracle)
# ---------------------------------------------------------------------------

# Characters forbidden in Windows file names
_FORBIDDEN_CHARS = set('<>:"/\\|?*') | {chr(c) for c in range(0x00, 0x20)}

# Reserved device names (case-insensitive), with or without extension
_RESERVED_PATTERN = re.compile(
    r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..+)?$",
    re.IGNORECASE,
)


def _should_reject(name: str) -> bool:
    """Independent oracle: returns True if the name should be rejected."""
    # Condition 1: empty after trimming
    if not name or not name.strip():
        return True

    # Condition 2: exceeds 255 characters
    if len(name) > 255:
        return True

    # Condition 3: contains a Windows-forbidden character
    if any(c in _FORBIDDEN_CHARS for c in name):
        return True

    # Condition 4: is a reserved device name
    if _RESERVED_PATTERN.match(name):
        return True

    # Condition 5: ends with a space or dot
    if name.endswith(" ") or name.endswith("."):
        return True

    return False


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


@given(name=st.text())
@settings(max_examples=200)
def test_snapshot_name_validation(name: str):
    """Property 2: Snapshot name validation.

    For any string, validate_snapshot_name SHALL reject it (return an error
    message) if and only if it is empty after trimming, exceeds 255 characters,
    contains a Windows-forbidden character (< > : " / \\ | ? * or a control
    character), is a reserved device name (e.g. CON, PRN, AUX, NUL, COM1-COM9,
    LPT1-LPT9), or ends with a space or dot; otherwise it SHALL accept it
    (return None).

    **Validates: Requirements 2.7, 2.3**
    """
    result = validate_snapshot_name(name)
    expected_reject = _should_reject(name)

    if expected_reject:
        assert result is not None, (
            f"Expected rejection for name {name!r} but got None (accepted)"
        )
    else:
        assert result is None, (
            f"Expected acceptance for name {name!r} but got rejection: {result!r}"
        )
