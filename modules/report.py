"""
report.py
Shared Report builder and aggregation logic used by every module's
restore()/verify() implementations, and by restore.py's orchestration.

WinSnap's restore used to "succeed" whenever no exception escaped a module,
even when individual registry writes or file copies silently failed. This
module gives every module a small, uniform way to record per-item outcomes
(matched / failed / skipped) and roll them up into an honest category
status, instead of printing a warning and moving on.

Report dicts are plain JSON-friendly dicts (no dataclass) so they serialize
directly into --report-json output and are trivial to construct in tests:

    {
        "status": "matched" | "partial" | "failed" | "skipped",
        "reason": str | None,           # required when status == "skipped"
        "items": [
            {"name": str, "status": "matched"|"failed"|"skipped",
             "detail": str | None, "expected": Any | None, "actual": Any | None},
        ],
        "explorer_restart_required": bool,   # restore-phase reports only
    }

Aggregation rule (finalize()):
  - any failed item and at least one matched item -> "partial"
  - any failed item and no matched item           -> "failed"
  - no failed, at least one matched               -> "matched"
  - no failed, no matched, at least one skipped    -> "skipped"
  - no items at all                                -> "skipped" (builder reason)
"""

from typing import Any, Optional


_VALID_ITEM_STATUSES = ("matched", "failed", "skipped")


class Report:
    """
    Accumulates per-item outcomes for one module's restore() or verify()
    call and rolls them up into a single category status via finalize().
    """

    def __init__(self, module: str, phase: str):
        """
        module: the module name (e.g. "taskbar"), used only for context/debugging.
        phase: "restore" or "verify" -- controls whether finalize() includes
               the explorer_restart_required key.
        """
        self.module = module
        self.phase = phase
        self.items: list[dict] = []
        self._explorer_restart_required = False
        self._empty_reason: Optional[str] = None

    def add(self, name: str, status: str, detail: Optional[str] = None,
            expected: Any = None, actual: Any = None) -> None:
        """Record one item outcome. status must be matched/failed/skipped."""
        if status not in _VALID_ITEM_STATUSES:
            raise ValueError(
                f"invalid item status {status!r}; must be one of {_VALID_ITEM_STATUSES}")
        self.items.append({
            "name": name,
            "status": status,
            "detail": detail,
            "expected": expected,
            "actual": actual,
        })

    # Sugar methods -----------------------------------------------------

    def add_matched(self, name: str, detail: Optional[str] = None,
                     expected: Any = None, actual: Any = None) -> None:
        self.add(name, "matched", detail, expected, actual)

    def add_failed(self, name: str, detail: Optional[str] = None,
                   expected: Any = None, actual: Any = None) -> None:
        self.add(name, "failed", detail, expected, actual)

    def add_skipped(self, name: str, detail: Optional[str] = None,
                    expected: Any = None, actual: Any = None) -> None:
        self.add(name, "skipped", detail, expected, actual)

    def require_explorer_restart(self) -> None:
        """Mark that Explorer must be restarted for this module's changes to
        take effect. Only meaningful for restore-phase reports."""
        self._explorer_restart_required = True

    def skip_all(self, reason: str) -> dict:
        """Terminal helper: short-circuit the whole category as skipped with
        a single reason (e.g. 'winget not found', 'requires elevation'),
        discarding any items already recorded. Returns the finalized dict."""
        self.items = []
        self._empty_reason = reason
        return self.finalize()

    def finalize(self) -> dict:
        """Apply the aggregation rule and return the finalized report dict.

        A "reason" is always populated when the aggregate status is
        "skipped" -- whether because no items were recorded at all, or
        because every recorded item was itself skipped -- so a skipped
        category is never reported without an explanation (Req 7.6, 7.7).
        """
        status = aggregate_status(self.items)
        reason = None
        if status == "skipped":
            if not self.items:
                reason = self._empty_reason or "nothing to restore"
            else:
                # Every item is skipped (no matched/failed present):
                # summarize from the individual item details.
                details = [item["detail"] for item in self.items if item.get("detail")]
                reason = "; ".join(details) if details else "all items skipped"
        report: dict = {
            "status": status,
            "reason": reason,
            "items": list(self.items),
        }
        if self.phase == "restore":
            report["explorer_restart_required"] = self._explorer_restart_required
        return report


def aggregate_status(items: list[dict]) -> str:
    """
    Pure aggregation function, usable independently of the Report builder
    (and property-testable in isolation).

    - any failed item and at least one matched item -> "partial"
    - any failed item and no matched item           -> "failed"
    - no failed, at least one matched               -> "matched"
    - no failed, no matched, at least one skipped    -> "skipped"
    - no items at all                                -> "skipped"
    """
    if not items:
        return "skipped"

    has_failed = any(item.get("status") == "failed" for item in items)
    has_matched = any(item.get("status") == "matched" for item in items)

    if has_failed and has_matched:
        return "partial"
    if has_failed:
        return "failed"
    if has_matched:
        return "matched"
    return "skipped"


def worst_exit_code(reports: dict) -> int:
    """
    reports: mapping of category name -> finalized report dict (as produced
    by Report.finalize()/skip_all()), typically a combination of restore-
    phase and verify-phase reports.

    Returns 0 iff no report has status "failed" (Req 7.5), else 1.
    """
    for report in reports.values():
        if report.get("status") == "failed":
            return 1
    return 0
