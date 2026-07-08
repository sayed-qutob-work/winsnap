"""
test_prop_restore_outcome.py — Property-based tests for restore/verify outcome
classification.

Feature: gui-backend-alignment, Requirement 1: Report-based restore outcome
classification.

Validates: Requirements 1.1, 1.2, 1.3, 11.6

The pre-hardening GUI classified a module's restore outcome from whether its
restore() call raised an exception (``classify_restore_outcome``). That
function has been removed: the hardened backend never raises out of
restore()/verify() -- it always returns a structured report dict
(``{"status": "matched"|"partial"|"failed"|"skipped", "reason", "items", ...}``,
see modules/report.py), and ``restore.run_modules`` itself converts any stray
exception into a synthesized ``{"status": "failed"}`` report before the GUI
ever sees it (Req 1.6). The GUI's job is now just to map that report dict --
or a skip reason code, for modules that never ran at all -- to a
``ModuleOutcome`` for display. That mapping is done by two pure functions:

- ``report_to_outcome(name, report)`` -- maps a report dict's ``status`` field
  directly to a ``ModuleStatus`` value (matched/partial/failed/skipped are
  literally the same strings), carrying the report's ``reason`` and ``items``
  through unchanged.
- ``skip_outcome(name, reason_code)`` -- maps a skip reason code (module
  deselected by the user, or one of restore.partition_modules's codes for a
  module that was never run at all) to a SKIPPED ``ModuleOutcome`` with
  CLI-matching wording.
"""

import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from gui import report_to_outcome, skip_outcome, ModuleStatus


# Strategy for module names: non-empty text strings
module_names = st.text(min_size=1, max_size=50)

# Strategy for the four report statuses modules/report.py's aggregate_status
# can produce -- explicitly enumerated (rather than left to chance) so every
# hypothesis run exercises all four (Req 11.6).
report_statuses = st.sampled_from(["matched", "partial", "failed", "skipped"])

# Strategy for a report's optional reason text
reasons = st.one_of(st.none(), st.text(min_size=1, max_size=100))

# Strategy for a single report item, per modules/report.py's item shape:
# {"name", "status", "detail", "expected", "actual"}
report_items = st.fixed_dictionaries({
    "name": st.text(min_size=1, max_size=50),
    "status": st.sampled_from(["matched", "failed", "skipped"]),
    "detail": st.one_of(st.none(), st.text(min_size=1, max_size=100)),
    "expected": st.one_of(st.none(), st.text(max_size=20), st.integers(), st.booleans()),
    "actual": st.one_of(st.none(), st.text(max_size=20), st.integers(), st.booleans()),
})

# Strategy for a full report dict, per modules/report.py's locked contract.
# explorer_restart_required is included (as it would be for a restore-phase
# report) but is not part of what report_to_outcome consumes.
report_dicts = st.fixed_dictionaries({
    "status": report_statuses,
    "reason": reasons,
    "items": st.lists(report_items, max_size=5),
    "explorer_restart_required": st.booleans(),
})

# Strategy for skip_outcome's reason codes: "deselected" is a GUI-only
# concept; the other two are restore.partition_modules's skip reason codes.
skip_reason_codes = st.sampled_from(
    ["deselected", "not_found_in_snapshot", "export_error"]
)


@given(name=module_names, report=report_dicts)
@settings(max_examples=200)
def test_report_to_outcome_classification(name, report):
    """Requirement 1: report_to_outcome maps a report dict's status field
    directly to the corresponding ModuleStatus value, and carries the
    report's reason and items through unchanged, for all four possible
    report statuses (matched, partial, failed, skipped).

    **Validates: Requirements 1.1, 1.2, 1.3, 11.6**
    """
    outcome = report_to_outcome(name, report)

    # The outcome name matches the input.
    assert outcome.name == name

    # Status is classified from the report's status field, not from whether
    # any call raised (Req 1.1, 1.2) -- a direct value mapping since
    # ModuleStatus's values are literally the report status strings.
    assert outcome.status == ModuleStatus(report["status"])

    # A non-empty reason is always surfaced as the outcome's detail,
    # regardless of status (Req 1.3).
    assert outcome.detail == report.get("reason")

    # Per-item detail is carried through verbatim (as a tuple).
    assert outcome.items == tuple(report["items"])


@given(name=module_names, report=report_dicts)
@settings(max_examples=200)
def test_report_to_outcome_reason_surfaced_for_failed_or_skipped(name, report):
    """Requirement 1.3: when a report has status 'failed' or 'skipped' and a
    non-empty reason, that reason SHALL be surfaced in the module's outcome
    detail.

    **Validates: Requirements 1.3**
    """
    outcome = report_to_outcome(name, report)

    if report["status"] in ("failed", "skipped") and report.get("reason"):
        assert outcome.detail == report["reason"]


@given(name=module_names, reason_code=skip_reason_codes)
@settings(max_examples=200)
def test_skip_outcome_classification(name, reason_code):
    """Requirement 1: skip_outcome classifies a module that never ran (was
    deselected by the user, absent from the snapshot, or recorded with an
    export error) as SKIPPED, with CLI-matching wording for each reason
    code.

    **Validates: Requirements 1.1, 1.2, 11.6**
    """
    outcome = skip_outcome(name, reason_code)

    # skip_outcome always produces a SKIPPED outcome for the given name.
    assert outcome.name == name
    assert outcome.status == ModuleStatus.SKIPPED

    # Wording matches the CLI's corresponding skip messages.
    expected_detail = {
        "deselected": "Deselected by user",
        "not_found_in_snapshot": "Not found in snapshot",
        "export_error": "Was not captured (export error)",
    }[reason_code]
    assert outcome.detail == expected_detail


def test_report_to_outcome_covers_all_four_statuses():
    """Requirement 1.5, 11.6: matched, partial, failed, and skipped each map
    to a distinct ModuleStatus value via report_to_outcome.

    **Validates: Requirements 1.1, 11.6**
    """
    statuses_seen = set()
    for status in ("matched", "partial", "failed", "skipped"):
        report = {"status": status, "reason": None, "items": [],
                   "explorer_restart_required": False}
        outcome = report_to_outcome("some_module", report)
        statuses_seen.add(outcome.status)

    assert statuses_seen == {
        ModuleStatus.MATCHED,
        ModuleStatus.PARTIAL,
        ModuleStatus.FAILED,
        ModuleStatus.SKIPPED,
    }
