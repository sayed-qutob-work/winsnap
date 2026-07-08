"""
test_prop_version_verdict.py — Property-based test for snapshot version evaluation.

Feature: gui-backend-alignment, Task 7.3: exercises the full two-step
version-acceptance composition -- a snapshot dict flows through
restore.evaluate_snapshot_version (Req 7.1, 7.3: single importable backend
function, shared fallback chain) and then through gui.to_version_verdict
(Req 7.2: GUI and CLI reach the same accept/warn/refuse outcome) -- replacing
the old, removed gui.evaluate_version(raw, supported_major).

Validates: Requirements 7.1, 7.2, 7.3, 11.6
"""

import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent))

import restore
from gui import VersionVerdict, to_version_verdict
from restore import evaluate_snapshot_version


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Well-formed version strings like "X.Y.Z" where X is a non-negative integer.
# Always non-empty/truthy, so placing one under "snapshot_format_version"
# never triggers the fallback chain.
_well_formed_versions = st.builds(
    lambda major, minor, patch: f"{major}.{minor}.{patch}",
    major=st.integers(min_value=0, max_value=1000),
    minor=st.integers(min_value=0, max_value=100),
    patch=st.integers(min_value=0, max_value=100),
)

# Supported major version range (monkeypatched onto restore.SUPPORTED_MAJOR
# for the duration of each example -- evaluate_snapshot_version reads the
# module-global each call, so this is picked up correctly).
_supported_major = st.integers(min_value=0, max_value=100)

# Garbage strings that cannot be parsed as a version (no leading integer).
# Deliberately non-empty/truthy for the same reason as above: an empty or
# None "raw" is a fallback-chain concern, covered separately below.
_garbage_strings = st.one_of(
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


def _verdict_for(evaluation) -> VersionVerdict:
    """Run the full composition under test: evaluate_snapshot_version's
    output feeds directly into to_version_verdict, mirroring how gui.py's
    restore worker uses them together (Req 7.1, 7.2)."""
    return to_version_verdict(evaluation)


@given(
    raw=st.one_of(_well_formed_versions, _garbage_strings),
    supported_major=_supported_major,
)
@settings(max_examples=200)
def test_snapshot_version_verdict(raw, supported_major):
    """Property 8: Snapshot version verdict (full composition).

    For any truthy "snapshot_format_version" string and supported MAJOR,
    restore.evaluate_snapshot_version + gui.to_version_verdict together
    SHALL return INCOMPATIBLE when the parsed MAJOR exceeds the supported
    MAJOR, COMPATIBLE when the parsed MAJOR is less than or equal to the
    supported MAJOR, and UNPARSEABLE when no MAJOR can be parsed -- the
    same outcome the CLI's evaluate_snapshot_version-based
    _check_format_version reaches (Req 7.2).

    **Validates: Requirements 7.1, 7.2, 7.3, 11.6**
    """
    original_supported_major = restore.SUPPORTED_MAJOR
    try:
        restore.SUPPORTED_MAJOR = supported_major
        snapshot = {"snapshot_format_version": raw}
        evaluation = evaluate_snapshot_version(snapshot)
        verdict = _verdict_for(evaluation)
    finally:
        restore.SUPPORTED_MAJOR = original_supported_major

    # Determine expected behavior based on input, mirroring
    # evaluate_snapshot_version's own parsing rule.
    try:
        expected_major = int(str(raw).split(".")[0])
    except (ValueError, IndexError):
        # Cannot parse -> UNPARSEABLE
        assert verdict == VersionVerdict.UNPARSEABLE, (
            f"Expected UNPARSEABLE for unparseable raw={raw!r}, got {verdict}"
        )
        assert evaluation.major is None
    else:
        # Parseable -> check INCOMPATIBLE vs COMPATIBLE
        assert evaluation.major == expected_major, (
            f"Expected major={expected_major}, got {evaluation.major}"
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


# ---------------------------------------------------------------------------
# Fallback chain: snapshot_format_version, then winsnap_version, then
# "0.1.0" (Req 7.1) -- exercised as part of the full snapshot-dict-based
# composition, unlike the old raw-argument-based evaluate_version which
# had no fallback chain of its own.
# ---------------------------------------------------------------------------

_optional_version = st.one_of(st.none(), _well_formed_versions, _garbage_strings)


@given(
    format_version=_optional_version,
    winsnap_version=_optional_version,
    supported_major=_supported_major,
)
@settings(max_examples=200)
def test_snapshot_version_verdict_fallback_chain(
    format_version, winsnap_version, supported_major
):
    """Property 8 (fallback chain): restore.evaluate_snapshot_version SHALL
    prefer "snapshot_format_version", fall back to "winsnap_version" when
    absent/falsy, and finally default to "0.1.0" when neither is present --
    identical to the pre-refactor _check_format_version fallback chain --
    and gui.to_version_verdict SHALL reach the matching verdict for
    whichever value wins the fallback.

    **Validates: Requirements 7.1, 7.2, 7.3, 11.6**
    """
    snapshot = {}
    if format_version is not None:
        snapshot["snapshot_format_version"] = format_version
    if winsnap_version is not None:
        snapshot["winsnap_version"] = winsnap_version

    # Replicate the fallback chain exactly as evaluate_snapshot_version does:
    # `snapshot.get(...) or snapshot.get(...) or "0.1.0"` -- falsy values
    # (None, "") fall through to the next link.
    expected_raw = (
        snapshot.get("snapshot_format_version")
        or snapshot.get("winsnap_version")
        or "0.1.0"
    )

    original_supported_major = restore.SUPPORTED_MAJOR
    try:
        restore.SUPPORTED_MAJOR = supported_major
        evaluation = evaluate_snapshot_version(snapshot)
        verdict = _verdict_for(evaluation)
    finally:
        restore.SUPPORTED_MAJOR = original_supported_major

    assert evaluation.raw == expected_raw, (
        f"Expected fallback-resolved raw={expected_raw!r}, got {evaluation.raw!r}"
    )

    try:
        expected_major = int(str(expected_raw).split(".")[0])
    except (ValueError, IndexError):
        assert verdict == VersionVerdict.UNPARSEABLE
        assert evaluation.major is None
    else:
        assert evaluation.major == expected_major
        if expected_major > supported_major:
            assert verdict == VersionVerdict.INCOMPATIBLE
        else:
            assert verdict == VersionVerdict.COMPATIBLE
