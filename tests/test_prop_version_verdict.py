"""
test_prop_version_verdict.py — Property-based test for snapshot version evaluation.

Feature: winsnap-gui, Property 8: Snapshot version verdict

Validates: Requirements 10.2, 10.4, 10.5
"""

import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from gui import VersionVerdict, evaluate_version


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Well-formed version strings like "X.Y.Z" where X is a non-negative integer
_well_formed_versions = st.builds(
    lambda major, minor, patch: f"{major}.{minor}.{patch}",
    major=st.integers(min_value=0, max_value=1000),
    minor=st.integers(min_value=0, max_value=100),
    patch=st.integers(min_value=0, max_value=100),
)

# Supported major version range
_supported_major = st.integers(min_value=0, max_value=100)

# Garbage strings that cannot be parsed as a version (no leading integer)
_garbage_strings = st.one_of(
    st.just(""),
    st.text(
        alphabet=st.characters(blacklist_categories=("Nd",)),
        min_size=1,
        max_size=50,
    ),
    st.just("abc.def.ghi"),
    st.just("not-a-version"),
    st.just(".1.2"),
    st.just("v.1.2"),
)


@given(
    raw=st.one_of(
        _well_formed_versions,
        _garbage_strings,
        st.none(),
    ),
    supported_major=_supported_major,
)
@settings(max_examples=200)
def test_snapshot_version_verdict(raw, supported_major):
    """Property 8: Snapshot version verdict.

    For any version string and supported MAJOR, evaluate_version SHALL return
    INCOMPATIBLE when the parsed MAJOR exceeds the supported MAJOR, COMPATIBLE
    when the parsed MAJOR is less than or equal to the supported MAJOR, and
    UNPARSEABLE when no MAJOR can be parsed.

    **Validates: Requirements 10.2, 10.4, 10.5**
    """
    verdict, parsed_major = evaluate_version(raw, supported_major)

    # Determine expected behavior based on input
    if raw is None or raw == "":
        # None or empty → UNPARSEABLE
        assert verdict == VersionVerdict.UNPARSEABLE, (
            f"Expected UNPARSEABLE for raw={raw!r}, got {verdict}"
        )
        assert parsed_major is None
    else:
        # Try to parse the major version the same way the function does
        try:
            expected_major = int(str(raw).split(".")[0])
        except (ValueError, IndexError):
            # Cannot parse → UNPARSEABLE
            assert verdict == VersionVerdict.UNPARSEABLE, (
                f"Expected UNPARSEABLE for unparseable raw={raw!r}, got {verdict}"
            )
            assert parsed_major is None
        else:
            # Parseable → check INCOMPATIBLE vs COMPATIBLE
            assert parsed_major == expected_major, (
                f"Expected parsed_major={expected_major}, got {parsed_major}"
            )
            if expected_major > supported_major:
                assert verdict == VersionVerdict.INCOMPATIBLE, (
                    f"Expected INCOMPATIBLE for major={expected_major} > "
                    f"supported={supported_major}, got {verdict}"
                )
            else:
                assert verdict == VersionVerdict.COMPATIBLE, (
                    f"Expected COMPATIBLE for major={expected_major} <= "
                    f"supported={supported_major}, got {verdict}"
                )
