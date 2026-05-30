"""
test_prop_version_info_msg.py — Property-based test for version-info message placeholders.

Feature: winsnap-gui, Property 9: Version-info message placeholders

Validates: Requirements 10.3
"""

import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from gui import format_version_info_message


# Strategy: generate snapshot dicts with optional "exported_at" and "snapshot_format_version" keys
snapshot_strategy = st.fixed_dictionaries(
    {},
    optional={
        "exported_at": st.one_of(st.none(), st.text(min_size=1)),
        "snapshot_format_version": st.one_of(st.none(), st.text(min_size=1)),
    },
)


@given(snapshot=snapshot_strategy)
@settings(max_examples=100)
def test_version_info_message_placeholders(snapshot: dict):
    """Property 9: Version-info message placeholders.

    For any snapshot dictionary, the version-info log message produced at the
    start of a restore SHALL contain the snapshot's export date and format
    version when present, and SHALL contain the literal placeholder "unknown"
    in place of either value that is absent.

    **Validates: Requirements 10.3**
    """
    message = format_version_info_message(snapshot)

    exported_at = snapshot.get("exported_at")
    format_version = snapshot.get("snapshot_format_version")

    # When exported_at is present (not None), the message must contain its value
    if exported_at is not None:
        assert exported_at in message, (
            f"Expected export date '{exported_at}' to appear in message: '{message}'"
        )
    else:
        # When absent, the message must contain "unknown" as placeholder
        assert "unknown" in message, (
            f"Expected 'unknown' placeholder in message when exported_at is absent: '{message}'"
        )

    # When snapshot_format_version is present (not None), the message must contain its value
    if format_version is not None:
        assert format_version in message, (
            f"Expected format version '{format_version}' to appear in message: '{message}'"
        )
    else:
        # When absent, the message must contain "unknown" as placeholder
        assert "unknown" in message, (
            f"Expected 'unknown' placeholder in message when format_version is absent: '{message}'"
        )
