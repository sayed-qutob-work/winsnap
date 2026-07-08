"""
test_prop_evaluate_snapshot_version.py — Property-based tests for
restore.evaluate_snapshot_version / VersionEvaluation.

Feature: gui-backend-alignment, Task 1.1 (Req 7.1, 7.2, 11.3)

Validates the pure, print-free version-acceptance decision restore.py
exposes as the single source of truth for its own _check_format_version
(Task 1.2 refactors that function into a thin wrapper) and for the GUI's
to_version_verdict (Task 3.5): the fallback chain
(snapshot_format_version -> winsnap_version -> "0.1.0"), the
compatible/incompatible/unparseable verdict branches (including the
major > SUPPORTED_MAJOR boundary), and that VersionEvaluation.raw preserves
the original (unstringified) type of the source value.
"""

import contextlib
import io
import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from restore import (
    VersionEvaluation,
    evaluate_snapshot_version,
    _check_format_version,
    SUPPORTED_MAJOR,
)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Well-formed version strings like "X.Y.Z" where X is a non-negative integer.
_well_formed_versions = st.builds(
    lambda major, minor, patch: f"{major}.{minor}.{patch}",
    major=st.integers(min_value=0, max_value=1000),
    minor=st.integers(min_value=0, max_value=100),
    patch=st.integers(min_value=0, max_value=100),
)

# Garbage strings that cannot be parsed as a version (no leading integer).
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

_version_values = st.one_of(_well_formed_versions, _garbage_strings)


def _expected_verdict(raw):
    """Reference reimplementation of the MAJOR-parsing/verdict logic, kept
    independent of the function under test so the property test is not
    circular."""
    try:
        major = int(str(raw).split(".")[0])
    except (ValueError, IndexError):
        return "unparseable", None
    if major > SUPPORTED_MAJOR:
        return "incompatible", major
    return "compatible", major


# ---------------------------------------------------------------------------
# Fallback chain: snapshot_format_version -> winsnap_version -> "0.1.0"
# ---------------------------------------------------------------------------

@given(fmt_version=_version_values, winsnap_version=_version_values)
@settings(max_examples=100)
def test_snapshot_format_version_takes_precedence(fmt_version, winsnap_version):
    """When both keys are present, snapshot_format_version wins."""
    snapshot = {
        "snapshot_format_version": fmt_version,
        "winsnap_version": winsnap_version,
    }
    ev = evaluate_snapshot_version(snapshot)
    assert ev.raw == fmt_version


@given(winsnap_version=_version_values)
@settings(max_examples=100)
def test_falls_back_to_winsnap_version(winsnap_version):
    """When snapshot_format_version is absent, winsnap_version is used."""
    snapshot = {"winsnap_version": winsnap_version}
    ev = evaluate_snapshot_version(snapshot)
    assert ev.raw == winsnap_version


def test_falls_back_to_default_when_both_absent():
    """When neither key is present, the fallback is the literal "0.1.0"."""
    ev = evaluate_snapshot_version({})
    assert ev.raw == "0.1.0"
    assert ev.verdict == "compatible"
    assert ev.major == 0


@given(fmt_version=st.one_of(st.just(""), st.just(None), st.just(0), st.just(False)))
@settings(max_examples=20)
def test_falsy_snapshot_format_version_falls_through(fmt_version):
    """A falsy snapshot_format_version (empty string, None, 0, False) is
    treated the same as absent -- the `or` chain short-circuits to
    winsnap_version, matching the pre-refactor inline check's behavior
    exactly."""
    snapshot = {"snapshot_format_version": fmt_version, "winsnap_version": "0.2.0"}
    ev = evaluate_snapshot_version(snapshot)
    assert ev.raw == "0.2.0"


# ---------------------------------------------------------------------------
# Verdict branches (compatible / incompatible / unparseable)
# ---------------------------------------------------------------------------

@given(
    raw=st.one_of(_version_values, st.none()),
    key=st.sampled_from(["snapshot_format_version", "winsnap_version"]),
)
@settings(max_examples=200)
def test_verdict_matches_reference_logic(raw, key):
    """For any raw version value plumbed through either fallback key,
    evaluate_snapshot_version's verdict/major match a straightforward
    reimplementation of the MAJOR-parsing/comparison logic."""
    snapshot = {key: raw} if raw is not None else {}
    ev = evaluate_snapshot_version(snapshot)

    effective_raw = raw if raw else "0.1.0"
    expected_verdict, expected_major = _expected_verdict(effective_raw)

    assert ev.verdict == expected_verdict
    assert ev.major == expected_major
    assert ev.raw == effective_raw


def test_major_exactly_at_supported_boundary_is_compatible():
    snapshot = {"snapshot_format_version": f"{SUPPORTED_MAJOR}.9.9"}
    ev = evaluate_snapshot_version(snapshot)
    assert ev.verdict == "compatible"
    assert ev.major == SUPPORTED_MAJOR


def test_major_one_above_supported_boundary_is_incompatible():
    snapshot = {"snapshot_format_version": f"{SUPPORTED_MAJOR + 1}.0.0"}
    ev = evaluate_snapshot_version(snapshot)
    assert ev.verdict == "incompatible"
    assert ev.major == SUPPORTED_MAJOR + 1


# ---------------------------------------------------------------------------
# raw-type preservation (repr-parity edge case, see design notes)
# ---------------------------------------------------------------------------

def test_raw_preserves_original_type_when_parseable():
    """VersionEvaluation.raw must keep the ORIGINAL (unstringified) type of
    a non-string value pulled from a malformed snapshot -- MAJOR parsing
    still goes through str(raw) internally, but the stored .raw is not
    eagerly stringified, so a caller can later reproduce {raw!r} byte-parity
    with the pre-refactor CLI warning line (exercised concretely by Task
    1.2's golden-output test)."""
    snapshot = {"snapshot_format_version": 123}
    ev = evaluate_snapshot_version(snapshot)
    assert ev.raw == 123
    assert isinstance(ev.raw, int)
    expected_verdict, expected_major = _expected_verdict(123)
    assert ev.verdict == expected_verdict
    assert ev.major == expected_major


def test_raw_preserves_original_type_when_unparseable():
    """A non-string, non-numeric raw value that cannot be parsed as a MAJOR
    at all is still reported unparseable, and .raw keeps its original type
    (not coerced to str)."""
    snapshot = {"snapshot_format_version": ["not", "a", "version"]}
    ev = evaluate_snapshot_version(snapshot)
    assert ev.raw == ["not", "a", "version"]
    assert isinstance(ev.raw, list)
    assert ev.verdict == "unparseable"
    assert ev.major is None


# ---------------------------------------------------------------------------
# Parity against the pre-existing, independently-implemented
# _check_format_version (Req 7.2: GUI and CLI must reach the same
# accept/refuse decision on the same snapshot metadata). Task 1.1
# deliberately leaves _check_format_version unmodified, so it is a genuine
# second implementation of the same fallback/parsing logic, not a mirror of
# evaluate_snapshot_version's own internals -- using it as the oracle here
# (rather than a hand-written reference function) is what actually
# evidences parity rather than self-consistency with the code under test.
# ---------------------------------------------------------------------------

@given(
    raw=st.one_of(_version_values, st.none()),
    key=st.sampled_from(["snapshot_format_version", "winsnap_version"]),
)
@settings(max_examples=200)
def test_verdict_parity_with_check_format_version(raw, key):
    snapshot = {key: raw} if raw is not None else {}

    ev = evaluate_snapshot_version(snapshot)
    with contextlib.redirect_stdout(io.StringIO()):  # discard CLI diagnostics
        accepted = _check_format_version(snapshot)

    if ev.verdict == "incompatible":
        assert accepted is False
    else:
        # "compatible" and "unparseable" both restore-anyway == True.
        assert accepted is True


# ---------------------------------------------------------------------------
# VersionEvaluation itself
# ---------------------------------------------------------------------------

def test_version_evaluation_is_frozen():
    ev = VersionEvaluation("compatible", "0.1.0", 0)
    try:
        ev.verdict = "incompatible"
    except Exception:
        pass
    else:
        raise AssertionError("VersionEvaluation must be frozen (immutable)")
