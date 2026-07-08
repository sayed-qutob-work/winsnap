"""
test_prop_to_version_verdict.py — Unit and property tests for
gui.to_version_verdict.

Feature: gui-backend-alignment, Task 3.5: to_version_verdict is a pure,
3-way mapping from a restore.VersionEvaluation's "verdict" field
("compatible"/"incompatible"/"unparseable") to the GUI's presentation-only
VersionVerdict enum. It accepts any object exposing a ".verdict" attribute
so gui.py does not need a module-level import of restore.py.

Also covers the removal of gui.evaluate_version (Req 7.3): it must no
longer be importable from gui, while VersionVerdict itself is retained.

Validates: Requirements 7.1, 7.2, 7.3, 11.3 (gui-backend-alignment)
"""

import sys
from pathlib import Path
from types import SimpleNamespace

from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from gui import VersionVerdict, to_version_verdict
from restore import VersionEvaluation, evaluate_snapshot_version, SUPPORTED_MAJOR


# --- Unit tests: the 3-way mapping, from real VersionEvaluation values ----


def test_compatible_maps_to_compatible():
    evaluation = VersionEvaluation("compatible", "1.0.0", 1)
    assert to_version_verdict(evaluation) == VersionVerdict.COMPATIBLE


def test_incompatible_maps_to_incompatible():
    evaluation = VersionEvaluation("incompatible", "99.0.0", 99)
    assert to_version_verdict(evaluation) == VersionVerdict.INCOMPATIBLE


def test_unparseable_maps_to_unparseable():
    evaluation = VersionEvaluation("unparseable", "not-a-version", None)
    assert to_version_verdict(evaluation) == VersionVerdict.UNPARSEABLE


def test_accepts_any_object_with_verdict_attribute():
    """to_version_verdict SHALL NOT require a restore.VersionEvaluation
    instance specifically -- only a ".verdict" attribute -- so gui.py is
    not forced into a module-level import of restore.py."""
    evaluation = SimpleNamespace(verdict="compatible", raw="1.0.0", major=1)
    assert to_version_verdict(evaluation) == VersionVerdict.COMPATIBLE


# --- Integration with restore.evaluate_snapshot_version (Req 7.1, 7.2) ----


def test_composes_with_evaluate_snapshot_version_compatible():
    snapshot = {"snapshot_format_version": f"{SUPPORTED_MAJOR}.0.0"}
    evaluation = evaluate_snapshot_version(snapshot)
    assert to_version_verdict(evaluation) == VersionVerdict.COMPATIBLE


def test_composes_with_evaluate_snapshot_version_incompatible():
    snapshot = {"snapshot_format_version": f"{SUPPORTED_MAJOR + 1}.0.0"}
    evaluation = evaluate_snapshot_version(snapshot)
    assert to_version_verdict(evaluation) == VersionVerdict.INCOMPATIBLE


def test_composes_with_evaluate_snapshot_version_unparseable():
    snapshot = {"snapshot_format_version": "not-a-version"}
    evaluation = evaluate_snapshot_version(snapshot)
    assert to_version_verdict(evaluation) == VersionVerdict.UNPARSEABLE


def test_composes_with_evaluate_snapshot_version_fallback_chain():
    """A snapshot carrying only winsnap_version (no snapshot_format_version)
    SHALL still evaluate and map correctly, exercising restore.py's
    fallback chain end to end (Req 7.1, 7.2)."""
    snapshot = {"winsnap_version": f"{SUPPORTED_MAJOR}.0.0"}
    evaluation = evaluate_snapshot_version(snapshot)
    assert to_version_verdict(evaluation) == VersionVerdict.COMPATIBLE


# --- Property test: every verdict string evaluate_snapshot_version can
# --- produce always maps to a valid VersionVerdict, never raises ----------


_verdict_strategy = st.sampled_from(["compatible", "incompatible", "unparseable"])
_evaluation_strategy = st.builds(
    VersionEvaluation,
    verdict=_verdict_strategy,
    raw=st.one_of(st.text(max_size=20), st.integers(), st.none()),
    major=st.one_of(st.none(), st.integers(min_value=0, max_value=999)),
)


@given(evaluation=_evaluation_strategy)
@settings(max_examples=100)
def test_to_version_verdict_never_raises_and_round_trips_verdict(evaluation):
    """For any VersionEvaluation carrying a known verdict string,
    to_version_verdict SHALL never raise, and the resulting VersionVerdict's
    value SHALL equal the input verdict string.

    **Validates: Requirements 7.1, 7.2, 7.3**
    """
    result = to_version_verdict(evaluation)

    assert isinstance(result, VersionVerdict)
    assert result.value == evaluation.verdict


# --- Removal of gui.evaluate_version (Req 7.3) -----------------------------


def test_evaluate_version_no_longer_exists():
    """Req 7.3: the old (raw, supported_major)-based evaluate_version SHALL
    be removed entirely -- version-acceptance decisions now flow exclusively
    through restore.evaluate_snapshot_version + gui.to_version_verdict."""
    import gui

    assert not hasattr(gui, "evaluate_version")


def test_version_verdict_enum_is_retained():
    """VersionVerdict itself SHALL be kept as the GUI-side presentation
    type, populated by to_version_verdict instead of the removed
    evaluate_version."""
    import gui

    assert hasattr(gui, "VersionVerdict")
    assert {member.value for member in gui.VersionVerdict} == {
        "compatible", "incompatible", "unparseable",
    }
