"""
test_prop_restore_outcome.py — Property-based test for restore outcome classification.

Feature: winsnap-gui, Property 7: Restore outcome classification

Validates: Requirements 14.6
"""

import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from gui import classify_restore_outcome, ModuleStatus


# Strategy for module names: non-empty text strings
module_names = st.text(min_size=1, max_size=50)

# Strategy for exception instances
exceptions = st.one_of(
    st.builds(ValueError, st.text(min_size=1, max_size=100)),
    st.builds(RuntimeError, st.text(min_size=1, max_size=100)),
    st.builds(OSError, st.text(min_size=1, max_size=100)),
)

# Strategy for restore context scenarios
# Each scenario is a tuple of (selected, present, export_errored, raised)
restore_scenarios = st.one_of(
    # Deselected → SKIPPED
    st.tuples(st.just(False), st.booleans(), st.booleans(), st.none()),
    # Selected, not present → SKIPPED
    st.tuples(st.just(True), st.just(False), st.booleans(), st.none()),
    # Selected, present, export errored → SKIPPED
    st.tuples(st.just(True), st.just(True), st.just(True), st.none()),
    # Selected, present, no export error, raised → FAILED
    st.tuples(st.just(True), st.just(True), st.just(False), exceptions),
    # Selected, present, no export error, no raise → PASSED
    st.tuples(st.just(True), st.just(True), st.just(False), st.none()),
)


@given(name=module_names, scenario=restore_scenarios)
@settings(max_examples=200)
def test_restore_outcome_classification(name, scenario):
    """Property 7: Restore outcome classification.

    For any module name and restore context, classify_restore_outcome SHALL
    classify the module as SKIPPED when it was deselected, when it is absent
    from the snapshot, or when it was recorded with an export error; as FAILED
    when it ran and raised; and as PASSED when it ran without raising.

    **Validates: Requirements 14.6**
    """
    selected, present, export_errored, raised = scenario

    outcome = classify_restore_outcome(
        name,
        selected=selected,
        present=present,
        export_errored=export_errored,
        raised=raised,
    )

    # Verify the outcome name matches the input
    assert outcome.name == name

    # Classification rules (evaluated in priority order):
    if not selected:
        # Deselected → SKIPPED with reason
        assert outcome.status == ModuleStatus.SKIPPED
        assert outcome.detail == "Deselected by user"
    elif not present:
        # Selected but absent from snapshot → SKIPPED
        assert outcome.status == ModuleStatus.SKIPPED
        assert outcome.detail == "Not present in snapshot"
    elif export_errored:
        # Selected, present, but recorded with export error → SKIPPED
        assert outcome.status == ModuleStatus.SKIPPED
        assert outcome.detail == "Was not captured (export error)"
    elif raised is not None:
        # Ran and raised → FAILED with exception text
        assert outcome.status == ModuleStatus.FAILED
        assert outcome.detail == str(raised)
    else:
        # Ran without raising → PASSED
        assert outcome.status == ModuleStatus.PASSED
        assert outcome.detail is None
