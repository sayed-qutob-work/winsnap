"""
test_prop_report_to_outcome.py — Unit and property tests for gui.report_to_outcome.

Feature: gui-backend-alignment, Task 3.2: report_to_outcome maps a
restore/verify report dict (modules/report.py's locked
{status, reason, items, ...} contract) to a ModuleOutcome via a direct
ModuleStatus(report["status"]) lookup, with detail=report.get("reason")
and items=tuple(report.get("items", [])).

Validates: Requirements 1.1, 1.3, 1.4, 1.7, 3.3 (gui-backend-alignment)
"""

import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from gui import ModuleOutcome, ModuleStatus, report_to_outcome
from modules.report import aggregate_status


# --- Unit tests: all four statuses, with/without reason -------------------


def test_matched_status_maps_to_matched():
    report = {"status": "matched", "reason": None, "items": []}
    outcome = report_to_outcome("wallpaper", report)
    assert outcome == ModuleOutcome(
        name="wallpaper", status=ModuleStatus.MATCHED, detail=None, items=()
    )


def test_partial_status_maps_to_partial():
    report = {"status": "partial", "reason": None, "items": []}
    outcome = report_to_outcome("taskbar", report)
    assert outcome.status == ModuleStatus.PARTIAL
    assert outcome.name == "taskbar"


def test_failed_status_maps_to_failed_with_reason():
    report = {"status": "failed", "reason": "powercfg error", "items": []}
    outcome = report_to_outcome("power", report)
    assert outcome.status == ModuleStatus.FAILED
    assert outcome.detail == "powercfg error"


def test_skipped_status_maps_to_skipped_with_reason():
    report = {"status": "skipped", "reason": "nothing to restore", "items": []}
    outcome = report_to_outcome("startup", report)
    assert outcome.status == ModuleStatus.SKIPPED
    assert outcome.detail == "nothing to restore"


def test_missing_reason_key_defaults_detail_to_none():
    """report.get("reason") SHALL be used, so a report dict that omits the
    key entirely (rather than setting it to None) still yields detail=None
    instead of raising KeyError."""
    report = {"status": "matched", "items": []}
    outcome = report_to_outcome("fonts", report)
    assert outcome.detail is None


def test_present_reason_is_surfaced_as_detail():
    report = {"status": "failed", "reason": "write access denied", "items": []}
    outcome = report_to_outcome("cursors", report)
    assert outcome.detail == "write access denied"


# --- Unit tests: items passthrough -----------------------------------------


def test_missing_items_key_defaults_to_empty_tuple():
    """report.get("items", []) SHALL be used, so a report dict that omits
    "items" entirely still yields an empty tuple instead of raising."""
    report = {"status": "skipped", "reason": "not found in snapshot"}
    outcome = report_to_outcome("env_vars", report)
    assert outcome.items == ()


def test_empty_items_list_maps_to_empty_tuple():
    report = {"status": "matched", "reason": None, "items": []}
    outcome = report_to_outcome("region_lang", report)
    assert outcome.items == ()


def test_items_pass_through_verbatim():
    """Per-item dicts SHALL round-trip unchanged (no mutation, no coercion,
    no re-keying) so the results view can render name/status/detail/
    expected/actual exactly as the module reported them."""
    items = [
        {"name": "pins", "status": "matched", "detail": None,
         "expected": None, "actual": None},
        {"name": "layout", "status": "failed", "detail": "mismatch",
         "expected": "2", "actual": "0"},
    ]
    report = {"status": "partial", "reason": None, "items": items}
    outcome = report_to_outcome("taskbar", report)

    assert outcome.items == tuple(items)
    # Each item dict itself is untouched (same values, not just same length).
    for original, stored in zip(items, outcome.items):
        assert stored == original


def test_outcome_name_matches_input_name():
    report = {"status": "matched", "reason": None, "items": []}
    outcome = report_to_outcome("desktop_icons", report)
    assert outcome.name == "desktop_icons"


# --- Property test: report_to_outcome never raises for aggregate_status's
# --- output vocabulary, across arbitrary reason/items combinations --------


def _item(status: str) -> dict:
    return {"name": "x", "status": status, "detail": None,
            "expected": None, "actual": None}


_status_strategy = st.sampled_from(["matched", "failed", "skipped"])
_items_strategy = st.lists(st.builds(_item, _status_strategy), max_size=10)

_report_strategy = st.builds(
    lambda items, reason, explorer_restart: {
        "status": aggregate_status(items),
        "reason": reason,
        "items": items,
        "explorer_restart_required": explorer_restart,
    },
    items=_items_strategy,
    reason=st.one_of(st.none(), st.text(max_size=50)),
    explorer_restart=st.booleans(),
)


@given(name=st.text(min_size=1, max_size=30), report=_report_strategy)
@settings(max_examples=200)
def test_report_to_outcome_never_raises_for_aggregate_status_output(name, report):
    """For any report dict whose "status" is a value aggregate_status can
    produce ("matched"/"partial"/"failed"/"skipped"), report_to_outcome
    SHALL never raise -- ModuleStatus's values are exactly that vocabulary,
    so the ModuleStatus(report["status"]) lookup always succeeds.

    **Validates: Requirements 1.1, 1.7, 3.3**
    """
    outcome = report_to_outcome(name, report)

    assert outcome.name == name
    assert outcome.status.value == report["status"]
    assert outcome.detail == report.get("reason")
    assert outcome.items == tuple(report["items"])


@given(report=_report_strategy)
@settings(max_examples=200)
def test_report_to_outcome_status_is_always_a_valid_module_status(report):
    """The resulting ModuleStatus SHALL always be one of the four known
    values -- report_to_outcome introduces no fifth state.

    **Validates: Requirement 1.5 (gui-backend-alignment)**
    """
    outcome = report_to_outcome("mod", report)
    assert outcome.status in (
        ModuleStatus.MATCHED,
        ModuleStatus.PARTIAL,
        ModuleStatus.FAILED,
        ModuleStatus.SKIPPED,
    )
