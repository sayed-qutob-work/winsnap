"""
test_prop_snapshot_name.py — Property-based test for default snapshot name generation.

Feature: winsnap-gui, Property 1: Default snapshot name format

Validates: Requirements 2.4
"""

import re
import sys
from datetime import datetime
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from gui import default_snapshot_name


@given(dt=st.datetimes())
@settings(max_examples=100)
def test_default_snapshot_name_format(dt: datetime):
    """Property 1: Default snapshot name format.

    For any datetime, when the snapshot name is left empty,
    default_snapshot_name SHALL produce "winsnap_" + dt.strftime("%Y%m%d_%H%M%S"),
    and the result SHALL match the pattern ^winsnap_\\d{8}_\\d{6}$.

    **Validates: Requirements 2.4**
    """
    result = default_snapshot_name(dt)

    # Assert the result equals the expected formatted string
    expected = "winsnap_" + dt.strftime("%Y%m%d_%H%M%S")
    assert result == expected, (
        f"Expected '{expected}' but got '{result}' for datetime {dt}"
    )

    # Assert the result matches the required regex pattern
    assert re.match(r"^winsnap_\d{8}_\d{6}$", result), (
        f"Result '{result}' does not match pattern ^winsnap_\\d{{8}}_\\d{{6}}$"
    )
