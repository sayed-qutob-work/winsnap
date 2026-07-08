"""
test_prop_skip_outcome.py — Unit and property tests for gui.skip_outcome.

Feature: gui-backend-alignment, Task 3.3: skip_outcome maps a skip reason
code -- "deselected" (a GUI-only concept) or one of
restore.partition_modules's two codes ("not_found_in_snapshot",
"export_error") -- to a ModuleOutcome(SKIPPED, ...), with wording matching
the CLI's printed skip messages for the latter two.

Also covers the removal of classify_restore_outcome (Req 1.2): it must no
longer be importable from gui.

Validates: Requirements 1.2, 1.3, 1.6, 2.2 (gui-backend-alignment)
"""

import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from gui import ModuleOutcome, ModuleStatus, skip_outcome


# --- Unit tests: each reason code maps to SKIPPED with the right wording --


def test_deselected_maps_to_skipped_with_deselected_wording():
    outcome = skip_outcome("wallpaper", "deselected")
    assert outcome == ModuleOutcome(
        name="wallpaper",
        status=ModuleStatus.SKIPPED,
        detail="Deselected by user",
    )


def test_not_found_in_snapshot_maps_to_skipped_with_cli_wording():
    """Wording matches restore.py's own skip message for a module absent
    from the snapshot (restore.py's run_modules loop / _DRY_RUN_SKIP_MESSAGES:
    "Not found in snapshot...")."""
    outcome = skip_outcome("startup", "not_found_in_snapshot")
    assert outcome.status == ModuleStatus.SKIPPED
    assert outcome.name == "startup"
    assert outcome.detail == "Not found in snapshot"


def test_export_error_maps_to_skipped_with_cli_wording():
    """Wording matches restore.py's own skip message for a module recorded
    with an export error ("Was not captured (export error)...")."""
    outcome = skip_outcome("power", "export_error")
    assert outcome.status == ModuleStatus.SKIPPED
    assert outcome.name == "power"
    assert outcome.detail == "Was not captured (export error)"


def test_outcome_items_default_to_empty_tuple():
    outcome = skip_outcome("fonts", "deselected")
    assert outcome.items == ()


# --- Exact wording parity against restore.py's printed skip messages ------


def test_exact_wording_matches_restore_partition_reason_text():
    """The two partition_modules-derived reason codes SHALL surface with
    the same core wording restore.py prints for a skipped module, so a
    module skipped by the GUI and the CLI read the same to the user."""
    import restore

    # restore.py's messages carry a trailing "Skipping." (a print-context
    # instruction that makes no sense inside an already-labeled SKIPPED
    # results row) -- the GUI's wording SHALL be a prefix of the CLI's,
    # i.e. the same core sentence.
    cli_not_found = restore._DRY_RUN_SKIP_MESSAGES["not_found_in_snapshot"]
    cli_export_error = restore._DRY_RUN_SKIP_MESSAGES["export_error"]

    assert cli_not_found.startswith(skip_outcome("x", "not_found_in_snapshot").detail)
    assert cli_export_error.startswith(skip_outcome("x", "export_error").detail)


# --- Property test: every reason code always yields SKIPPED ---------------


_reason_codes = st.sampled_from(["deselected", "not_found_in_snapshot", "export_error"])


@given(name=st.text(min_size=1, max_size=30), reason_code=_reason_codes)
@settings(max_examples=100)
def test_skip_outcome_always_skipped(name, reason_code):
    """For any of the three known reason codes, skip_outcome SHALL always
    produce a SKIPPED outcome carrying the input name and a non-empty
    detail string.

    **Validates: Requirements 1.3, 2.2**
    """
    outcome = skip_outcome(name, reason_code)

    assert outcome.name == name
    assert outcome.status == ModuleStatus.SKIPPED
    assert outcome.detail
    assert outcome.items == ()


# --- Removal of classify_restore_outcome (Req 1.2) -------------------------


def test_classify_restore_outcome_no_longer_exists():
    """Req 1.2: the exception-based classify_restore_outcome SHALL be
    removed entirely -- no path in the GUI SHALL classify a module as
    passed solely because its restore() call did not raise."""
    import gui

    assert not hasattr(gui, "classify_restore_outcome")
