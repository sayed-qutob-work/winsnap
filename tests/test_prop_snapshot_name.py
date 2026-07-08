"""
test_prop_snapshot_name.py — Property-based test for snapshot directory
default naming (export.create_snapshot_dir).

Feature: winsnap-gui, Property 1: Default snapshot name format.

gui.py's default_snapshot_name(start: datetime) -> str was removed by
gui-backend-alignment Task 3.6: its sole responsibility -- computing the
"winsnap_<timestamp>" default folder name for an unnamed export -- now
belongs exclusively to export.create_snapshot_dir, which
export.resolve_snapshot_dir delegates to whenever no --name is given.
Repointed by Task 7.5 to exercise create_snapshot_dir directly instead of
importing the now-deleted gui.default_snapshot_name.

tmp_path/monkeypatch are function-scoped fixtures that hypothesis's health
checks correctly flag as unsafe to share across generated examples, so this
uses the session-scoped tmp_path_factory plus a plain unittest.mock context
manager (entered/exited fresh on every example) instead -- the same pattern
already used by tests/test_prop_partition_modules.py for a similar concern.

Validates: Requirements 2.4 (winsnap-gui);
gui-backend-alignment Requirements 9.4, 11.6
"""

import re
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent))

import export


def _fixed_datetime(dt: datetime):
    """Build a datetime subclass whose .now() always returns `dt`, so
    export.create_snapshot_dir's timestamp can be pinned to an arbitrary
    hypothesis-generated datetime."""

    class _Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return dt

    return _Fixed


@given(dt=st.datetimes())
@settings(max_examples=100)
def test_create_snapshot_dir_name_format(dt: datetime, tmp_path_factory):
    """Property 1: Default snapshot name format.

    For any datetime, when no --name is given, export.create_snapshot_dir
    (reached via export.resolve_snapshot_dir(output, None, force) for an
    unnamed export) SHALL produce a directory named
    "winsnap_" + dt.strftime("%Y%m%d_%H%M%S"), and that name SHALL match the
    pattern ^winsnap_\\d{8}_\\d{6}$.

    **Validates: Requirements 2.4 (winsnap-gui); 9.4, 11.6 (gui-backend-alignment)**
    """
    base_output = tmp_path_factory.mktemp("snapshot_naming_prop")

    with patch.object(export, "datetime", _fixed_datetime(dt)):
        result = export.create_snapshot_dir(base_output)

    # Assert the folder name equals the expected formatted string.
    expected = "winsnap_" + dt.strftime("%Y%m%d_%H%M%S")
    assert result.name == expected, (
        f"Expected '{expected}' but got '{result.name}' for datetime {dt}"
    )

    # Assert the folder name matches the required regex pattern.
    assert re.match(r"^winsnap_\d{8}_\d{6}$", result.name), (
        f"Result '{result.name}' does not match pattern ^winsnap_\\d{{8}}_\\d{{6}}$"
    )
