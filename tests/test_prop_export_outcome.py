"""
test_prop_export_outcome.py — Property-based test for export outcome classification.

Feature: winsnap-gui, Property 6: Export outcome classification

Validates: Requirements 6.2, 7.3, 14.4, 14.5
"""

import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from gui import classify_export_outcome, ModuleStatus


# Strategy for module names (non-empty text)
module_names = st.text(min_size=1, max_size=30)


# Strategy for export outcome scenarios
def export_scenario():
    """Generate scenarios covering all classification branches."""
    return st.one_of(
        # Scenario: module raised an exception → FAILED
        st.tuples(
            module_names,
            st.builds(Exception, st.text(min_size=0, max_size=100)),
            st.none(),
        ).map(lambda t: {"name": t[0], "raised": t[1], "result": t[2], "expected_status": ModuleStatus.FAILED}),

        # Scenario: result has skip_reason == "not_admin" → FAILED with admin message
        st.tuples(
            module_names,
            st.none(),
            st.fixed_dictionaries({"skip_reason": st.just("not_admin")}),
        ).map(lambda t: {"name": t[0], "raised": t[1], "result": t[2], "expected_status": ModuleStatus.FAILED, "expected_detail": "Administrator privileges required to capture the active power plan"}),

        # Scenario: result has skip_reason (arbitrary, not "not_admin") → FAILED
        st.tuples(
            module_names,
            st.none(),
            st.fixed_dictionaries({"skip_reason": st.text(min_size=1, max_size=50).filter(lambda s: s != "not_admin")}),
        ).map(lambda t: {"name": t[0], "raised": t[1], "result": t[2], "expected_status": ModuleStatus.FAILED}),

        # Scenario: result has "error" key → FAILED
        st.tuples(
            module_names,
            st.none(),
            st.fixed_dictionaries({"error": st.text(min_size=0, max_size=100)}),
        ).map(lambda t: {"name": t[0], "raised": t[1], "result": t[2], "expected_status": ModuleStatus.FAILED}),

        # Scenario: result is empty dict → PASSED
        st.tuples(
            module_names,
            st.none(),
            st.just({}),
        ).map(lambda t: {"name": t[0], "raised": t[1], "result": t[2], "expected_status": ModuleStatus.PASSED}),

        # Scenario: result has {"enabled": False} (no error/skip) → PASSED
        st.tuples(
            module_names,
            st.none(),
            st.fixed_dictionaries({"enabled": st.just(False)}),
        ).map(lambda t: {"name": t[0], "raised": t[1], "result": t[2], "expected_status": ModuleStatus.PASSED}),

        # Scenario: result is None (no raise) → PASSED
        st.tuples(
            module_names,
            st.none(),
            st.none(),
        ).map(lambda t: {"name": t[0], "raised": t[1], "result": t[2], "expected_status": ModuleStatus.PASSED}),
    )


@given(scenario=export_scenario())
@settings(max_examples=200)
def test_export_outcome_classification(scenario):
    """Property 6: Export outcome classification.

    For any module name and result of running its export,
    classify_export_outcome SHALL classify the module as FAILED when the module
    raised, when the result carries an "error" key, or when the result carries
    a skip_reason (with the "not_admin" case yielding a detail that states
    Administrator privileges are required); SHALL classify it as PASSED when it
    completed without raising and without an error/skip indicator.

    **Validates: Requirements 6.2, 7.3, 14.4, 14.5**
    """
    name = scenario["name"]
    raised = scenario["raised"]
    result = scenario["result"]
    expected_status = scenario["expected_status"]

    outcome = classify_export_outcome(name, raised=raised, result=result)

    # Verify the module name is preserved
    assert outcome.name == name

    # Verify the status classification
    assert outcome.status == expected_status, (
        f"Expected {expected_status} but got {outcome.status} "
        f"for name={name!r}, raised={raised!r}, result={result!r}"
    )

    # Verify specific detail messages
    if raised is not None:
        # When raised, detail should be the exception text
        assert outcome.detail == str(raised)
    elif result is not None and result.get("skip_reason") == "not_admin":
        # Special admin message
        assert outcome.detail == "Administrator privileges required to capture the active power plan"
    elif result is not None and "skip_reason" in result:
        # Generic skip_reason → detail is the reason string
        assert outcome.detail == str(result["skip_reason"])
    elif result is not None and "error" in result:
        # Error key → detail is the error message
        assert outcome.detail == str(result["error"])
    elif expected_status == ModuleStatus.PASSED:
        # PASSED → no detail
        assert outcome.detail is None
