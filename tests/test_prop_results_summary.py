"""
test_prop_results_summary.py — Property-based test for results summary partition and counts.

Feature: gui-backend-alignment, Task 3.1: ResultsSummary reshaped for the
four-status report vocabulary (matched/partial/failed/skipped) plus a
separate verify-outcomes track.

Validates: Requirements 1.4, 1.5, 3.1 (gui-backend-alignment)
"""

import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from gui import ModuleOutcome, ModuleStatus, ResultsSummary


# Strategy for module names (non-empty text)
module_names = st.text(min_size=1, max_size=30)

# Strategy for module statuses — the full four-status report vocabulary
module_statuses = st.sampled_from(
    [ModuleStatus.MATCHED, ModuleStatus.PARTIAL, ModuleStatus.FAILED, ModuleStatus.SKIPPED]
)

# Strategy for optional detail strings
details = st.one_of(st.none(), st.text(min_size=0, max_size=100))

# Strategy for a tuple of item dicts (verbatim report "items" passthrough)
items_strategy = st.lists(
    st.fixed_dictionaries(
        {
            "name": st.text(min_size=1, max_size=20),
            "status": st.sampled_from(["matched", "failed", "skipped"]),
            "detail": st.one_of(st.none(), st.text(max_size=50)),
        }
    ),
    max_size=5,
).map(tuple)

# Strategy for a single ModuleOutcome
module_outcomes = st.builds(
    ModuleOutcome,
    name=module_names,
    status=module_statuses,
    detail=details,
    items=items_strategy,
)

# Strategy for lists of ModuleOutcomes
outcome_lists = st.lists(module_outcomes, min_size=0, max_size=50)


@given(outcomes=outcome_lists)
@settings(max_examples=200)
def test_results_summary_partition_and_counts(outcomes):
    """For any collection of ModuleOutcomes, ResultsSummary SHALL place each
    outcome in exactly one group (Matched, Partial, Failed, or Skipped) such
    that the group memberships partition the input, and the reported counts
    SHALL satisfy matched + partial + failed + skipped == total with each
    count equal to the size of its group.

    **Validates: Requirements 1.4, 1.5**
    """
    summary = ResultsSummary()
    for outcome in outcomes:
        summary.add(outcome)

    matched = summary.matched()
    partial = summary.partial()
    failed = summary.failed()
    skipped = summary.skipped()
    counts = summary.counts()

    # The four groups partition the input: their union equals the full list
    all_grouped = matched + partial + failed + skipped
    assert len(all_grouped) == len(outcomes), (
        f"Groups contain {len(all_grouped)} outcomes but input had {len(outcomes)}"
    )

    # Each outcome appears in exactly one group (no duplicates across groups)
    matched_ids = set(id(o) for o in matched)
    partial_ids = set(id(o) for o in partial)
    failed_ids = set(id(o) for o in failed)
    skipped_ids = set(id(o) for o in skipped)
    assert matched_ids.isdisjoint(partial_ids)
    assert matched_ids.isdisjoint(failed_ids)
    assert matched_ids.isdisjoint(skipped_ids)
    assert partial_ids.isdisjoint(failed_ids)
    assert partial_ids.isdisjoint(skipped_ids)
    assert failed_ids.isdisjoint(skipped_ids)

    # Total count across groups equals input size
    assert (
        len(matched_ids) + len(partial_ids) + len(failed_ids) + len(skipped_ids)
        == len(outcomes)
    )

    # Counts tuple matches group sizes: (matched, partial, failed, skipped)
    assert counts == (len(matched), len(partial), len(failed), len(skipped)), (
        f"counts() returned {counts} but group sizes are "
        f"({len(matched)}, {len(partial)}, {len(failed)}, {len(skipped)})"
    )

    # Sum of counts equals total number of outcomes
    assert sum(counts) == len(outcomes), (
        f"Sum of counts {sum(counts)} != total outcomes {len(outcomes)}"
    )

    # Each outcome is in the correct group based on its status
    for o in matched:
        assert o.status == ModuleStatus.MATCHED
    for o in partial:
        assert o.status == ModuleStatus.PARTIAL
    for o in failed:
        assert o.status == ModuleStatus.FAILED
    for o in skipped:
        assert o.status == ModuleStatus.SKIPPED


@given(outcomes=outcome_lists)
@settings(max_examples=100)
def test_module_outcome_items_passthrough(outcomes):
    """ModuleOutcome.items SHALL round-trip verbatim through ResultsSummary
    (no mutation, no coercion) so the results view can render per-item
    detail exactly as the backend report produced it.

    **Validates: Requirement 8.2 (gui-backend-alignment)**
    """
    summary = ResultsSummary()
    for outcome in outcomes:
        summary.add(outcome)

    for original, stored in zip(outcomes, summary.outcomes):
        assert stored.items == original.items


def test_module_outcome_items_defaults_to_empty_tuple():
    """A ModuleOutcome built without an explicit items argument SHALL default
    to an empty tuple (Req 3.1's ModuleOutcome reshape)."""
    outcome = ModuleOutcome(name="wallpaper", status=ModuleStatus.MATCHED, detail=None)
    assert outcome.items == ()


@given(
    restore_outcomes=st.lists(module_outcomes, min_size=0, max_size=10),
    verify_outcomes=st.lists(module_outcomes, min_size=0, max_size=10),
)
@settings(max_examples=100)
def test_verify_outcomes_tracked_separately(restore_outcomes, verify_outcomes):
    """add_verify() SHALL accumulate into a distinct verify_outcomes list
    that never mixes with the restore/export outcomes list, so a module can
    carry both a restore status and a verify status without conflating the
    two (Req 3.1, 8.4).

    **Validates: Requirements 3.4, 8.4 (gui-backend-alignment)**
    """
    summary = ResultsSummary()
    for outcome in restore_outcomes:
        summary.add(outcome)
    for outcome in verify_outcomes:
        summary.add_verify(outcome)

    assert summary.outcomes == restore_outcomes
    assert summary.verify_outcomes == verify_outcomes
    # counts() only reflects the restore/export outcomes track.
    assert sum(summary.counts()) == len(restore_outcomes)


def test_verify_for_returns_matching_outcome_by_name():
    """verify_for(name) SHALL return the verify outcome whose name matches,
    or None when no verify outcome exists for that module."""
    summary = ResultsSummary()
    taskbar_verify = ModuleOutcome(name="taskbar", status=ModuleStatus.MATCHED, detail=None)
    summary.add_verify(taskbar_verify)

    assert summary.verify_for("taskbar") is taskbar_verify
    assert summary.verify_for("wallpaper") is None


def test_verify_for_empty_when_no_verify_ran():
    """When verify did not run, verify_outcomes stays empty and verify_for
    returns None for any module name (Req 3.5's "no placeholder verify
    outcomes")."""
    summary = ResultsSummary()
    summary.add(ModuleOutcome(name="wallpaper", status=ModuleStatus.MATCHED, detail=None))

    assert summary.verify_outcomes == []
    assert summary.verify_for("wallpaper") is None


def test_empty_summary_counts_are_all_zero():
    """An empty ResultsSummary SHALL report all-zero counts across all four
    statuses."""
    summary = ResultsSummary()
    assert summary.counts() == (0, 0, 0, 0)
