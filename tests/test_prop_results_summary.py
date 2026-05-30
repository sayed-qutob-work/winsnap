"""
test_prop_results_summary.py — Property-based test for results summary partition and counts.

Feature: winsnap-gui, Property 13: Results summary partition and counts

Validates: Requirements 14.1, 14.7
"""

import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from gui import ResultsSummary, ModuleOutcome, ModuleStatus


# Strategy for module names (non-empty text)
module_names = st.text(min_size=1, max_size=30)

# Strategy for module statuses
module_statuses = st.sampled_from([ModuleStatus.PASSED, ModuleStatus.FAILED, ModuleStatus.SKIPPED])

# Strategy for optional detail strings
details = st.one_of(st.none(), st.text(min_size=0, max_size=100))

# Strategy for a single ModuleOutcome
module_outcomes = st.builds(
    ModuleOutcome,
    name=module_names,
    status=module_statuses,
    detail=details,
)

# Strategy for lists of ModuleOutcomes
outcome_lists = st.lists(module_outcomes, min_size=0, max_size=50)


@given(outcomes=outcome_lists)
@settings(max_examples=200)
def test_results_summary_partition_and_counts(outcomes):
    """Property 13: Results summary partition and counts.

    For any collection of ModuleOutcomes, ResultsSummary SHALL place each
    outcome in exactly one group (Passed, Failed, or Skipped) such that the
    group memberships partition the input, and the reported counts SHALL satisfy
    passed + failed + skipped == total with each count equal to the size of its
    group.

    **Validates: Requirements 14.1, 14.7**
    """
    summary = ResultsSummary()
    for outcome in outcomes:
        summary.add(outcome)

    passed = summary.passed()
    failed = summary.failed()
    skipped = summary.skipped()
    counts = summary.counts()

    # The three groups partition the input: their union equals the full list
    all_grouped = passed + failed + skipped
    assert len(all_grouped) == len(outcomes), (
        f"Groups contain {len(all_grouped)} outcomes but input had {len(outcomes)}"
    )

    # Each outcome appears in exactly one group (no duplicates across groups)
    passed_ids = set(id(o) for o in passed)
    failed_ids = set(id(o) for o in failed)
    skipped_ids = set(id(o) for o in skipped)
    assert passed_ids.isdisjoint(failed_ids), "An outcome appears in both Passed and Failed"
    assert passed_ids.isdisjoint(skipped_ids), "An outcome appears in both Passed and Skipped"
    assert failed_ids.isdisjoint(skipped_ids), "An outcome appears in both Failed and Skipped"

    # Total count across groups equals input size
    assert len(passed_ids) + len(failed_ids) + len(skipped_ids) == len(outcomes)

    # Counts tuple matches group sizes
    assert counts == (len(passed), len(failed), len(skipped)), (
        f"counts() returned {counts} but group sizes are "
        f"({len(passed)}, {len(failed)}, {len(skipped)})"
    )

    # Sum of counts equals total number of outcomes
    assert counts[0] + counts[1] + counts[2] == len(outcomes), (
        f"Sum of counts {sum(counts)} != total outcomes {len(outcomes)}"
    )

    # Each outcome is in the correct group based on its status
    for o in passed:
        assert o.status == ModuleStatus.PASSED
    for o in failed:
        assert o.status == ModuleStatus.FAILED
    for o in skipped:
        assert o.status == ModuleStatus.SKIPPED
