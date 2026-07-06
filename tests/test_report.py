"""
test_report.py — Unit and property tests for modules/report.py.

Feature: backend-roundtrip-hardening, Task 1 (Design D1: Report shape,
aggregation, and exit code).

Covers the aggregation truth table, skip_all()/empty-report behavior,
worst_exit_code() over mixed report dicts, and a hypothesis property test
asserting aggregate_status() is total, order-independent, and returns
failed/partial iff a failed item exists.

**Validates: Requirements 7.1, 7.3, 7.4, 7.5**
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from hypothesis import given, settings
from hypothesis import strategies as st

from modules.report import Report, aggregate_status, worst_exit_code


def _item(status: str) -> dict:
    return {"name": "x", "status": status, "detail": None,
            "expected": None, "actual": None}


# --- aggregate_status truth table ------------------------------------------

def test_aggregate_status_empty_is_skipped():
    assert aggregate_status([]) == "skipped"


def test_aggregate_status_all_matched():
    assert aggregate_status([_item("matched"), _item("matched")]) == "matched"


def test_aggregate_status_all_skipped():
    assert aggregate_status([_item("skipped"), _item("skipped")]) == "skipped"


def test_aggregate_status_all_failed():
    assert aggregate_status([_item("failed"), _item("failed")]) == "failed"


def test_aggregate_status_failed_and_matched_is_partial():
    assert aggregate_status([_item("failed"), _item("matched")]) == "partial"


def test_aggregate_status_failed_and_skipped_no_matched_is_failed():
    assert aggregate_status([_item("failed"), _item("skipped")]) == "failed"


def test_aggregate_status_matched_and_skipped_is_matched():
    assert aggregate_status([_item("matched"), _item("skipped")]) == "matched"


def test_aggregate_status_failed_matched_skipped_is_partial():
    assert aggregate_status(
        [_item("failed"), _item("matched"), _item("skipped")]) == "partial"


# --- Report.add / sugar methods --------------------------------------------

def test_report_add_rejects_invalid_status():
    report = Report("dummy", "restore")
    try:
        report.add("thing", "bogus")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_report_sugar_methods_record_expected_status():
    report = Report("dummy", "restore")
    report.add_matched("a", detail="ok")
    report.add_failed("b", detail="boom")
    report.add_skipped("c", detail="denylisted")

    statuses = {item["name"]: item["status"] for item in report.items}
    assert statuses == {"a": "matched", "b": "failed", "c": "skipped"}


def test_report_add_records_expected_and_actual():
    report = Report("dummy", "verify")
    report.add("style", "failed", detail="mismatch", expected="2", actual="0")
    item = report.items[0]
    assert item["expected"] == "2"
    assert item["actual"] == "0"


# --- Report.finalize() aggregation + shape ---------------------------------

def test_finalize_matched_category():
    report = Report("dummy", "restore")
    report.add_matched("a")
    report.add_skipped("b", detail="not applicable")
    result = report.finalize()

    assert result["status"] == "matched"
    assert result["reason"] is None
    assert len(result["items"]) == 2
    assert result["explorer_restart_required"] is False


def test_finalize_partial_category():
    report = Report("dummy", "restore")
    report.add_matched("a")
    report.add_failed("b", detail="write failed")
    result = report.finalize()

    assert result["status"] == "partial"
    assert result["reason"] is None


def test_finalize_failed_category():
    report = Report("dummy", "restore")
    report.add_failed("a", detail="write failed")
    result = report.finalize()

    assert result["status"] == "failed"


def test_finalize_empty_report_is_skipped_with_default_reason():
    report = Report("dummy", "restore")
    result = report.finalize()

    assert result["status"] == "skipped"
    assert result["reason"] == "nothing to restore"
    assert result["items"] == []


def test_finalize_verify_phase_omits_explorer_restart_key():
    report = Report("dummy", "verify")
    report.add_matched("a")
    result = report.finalize()

    assert "explorer_restart_required" not in result


def test_require_explorer_restart_sets_flag_on_restore_phase():
    report = Report("taskbar", "restore")
    report.add_matched("pins copied")
    report.require_explorer_restart()
    result = report.finalize()

    assert result["explorer_restart_required"] is True


def test_finalize_all_items_skipped_has_non_none_reason():
    """A category whose every item is skipped (e.g. every env var was
    denylisted) must still report a non-None reason -- 'skipped' without
    an explanation is never acceptable (Req 7.6, 7.7)."""
    report = Report("env_vars", "restore")
    report.add_skipped("TEMP", detail="machine-specific (denylist)")
    report.add_skipped("USERPROFILE", detail="machine-specific (denylist)")
    result = report.finalize()

    assert result["status"] == "skipped"
    assert result["reason"] is not None
    assert "denylist" in result["reason"]


def test_finalize_all_items_skipped_without_detail_still_has_reason():
    """Even if individual items carry no detail text, finalize() must still
    synthesize a non-None reason for an all-skipped category."""
    report = Report("mouse_display", "restore")
    report.add_skipped("dpi")
    result = report.finalize()

    assert result["status"] == "skipped"
    assert result["reason"] is not None


# --- Report.skip_all() -------------------------------------------------

def test_skip_all_discards_items_and_sets_reason():
    report = Report("power", "restore")
    report.add_matched("would have been recorded")
    result = report.skip_all("requires elevation")

    assert result["status"] == "skipped"
    assert result["reason"] == "requires elevation"
    assert result["items"] == []


def test_skip_all_on_fresh_report():
    report = Report("apps", "restore")
    result = report.skip_all("winget not found on target")

    assert result["status"] == "skipped"
    assert result["reason"] == "winget not found on target"


# --- worst_exit_code() -------------------------------------------------

def test_worst_exit_code_all_matched_or_skipped_is_zero():
    reports = {
        "wallpaper": Report("wallpaper", "restore").finalize(),
        "power": Report("power", "restore").skip_all("requires elevation"),
    }
    assert worst_exit_code(reports) == 0


def test_worst_exit_code_any_failed_is_one():
    ok_report = Report("wallpaper", "restore")
    ok_report.add_matched("applied")

    failed_report = Report("power", "restore")
    failed_report.add_failed("import", detail="powercfg error")

    reports = {
        "wallpaper": ok_report.finalize(),
        "power": failed_report.finalize(),
    }
    assert worst_exit_code(reports) == 1


def test_worst_exit_code_combines_restore_and_verify_phases():
    restore_reports = {"wallpaper": Report("wallpaper", "restore").finalize()}
    verify_report = Report("wallpaper", "verify")
    verify_report.add_failed("sha256", detail="mismatch")
    verify_reports = {"wallpaper": verify_report.finalize()}

    combined = {**{f"restore:{k}": v for k, v in restore_reports.items()},
                **{f"verify:{k}": v for k, v in verify_reports.items()}}
    assert worst_exit_code(combined) == 1


def test_worst_exit_code_empty_reports_is_zero():
    assert worst_exit_code({}) == 0


# --- JSON serializability (report dicts must feed --report-json directly) --

def test_finalize_result_is_json_serializable():
    report = Report("wallpaper", "verify")
    report.add_matched("style", expected="10", actual="10")
    report.add_failed("sha256", detail="mismatch", expected="abc", actual="def")
    result = report.finalize()

    round_tripped = json.loads(json.dumps(result))
    assert round_tripped == result


def test_skip_all_result_is_json_serializable():
    report = Report("power", "restore")
    result = report.skip_all("requires elevation")

    round_tripped = json.loads(json.dumps(result))
    assert round_tripped == result


# --- Hypothesis property: aggregate_status is total, order-independent -----

_status_strategy = st.sampled_from(["matched", "failed", "skipped"])
_items_strategy = st.lists(
    st.builds(_item, _status_strategy),
    max_size=25,
)


@settings(max_examples=200)
@given(items=_items_strategy)
def test_aggregate_status_is_total_and_valid(items):
    """aggregate_status must always return one of the four known statuses,
    never raise, for any list of well-formed items."""
    result = aggregate_status(items)
    assert result in ("matched", "partial", "failed", "skipped")


@settings(max_examples=200)
@given(items=_items_strategy)
def test_aggregate_status_order_independent(items):
    """Shuffling the item list must not change the aggregate status."""
    reversed_items = list(reversed(items))
    assert aggregate_status(items) == aggregate_status(reversed_items)


@settings(max_examples=200)
@given(items=_items_strategy)
def test_aggregate_status_failed_or_partial_iff_failed_item_exists(items):
    """The result is 'failed' or 'partial' if and only if at least one item
    has status 'failed'."""
    has_failed = any(item["status"] == "failed" for item in items)
    result = aggregate_status(items)
    assert (result in ("failed", "partial")) == has_failed
