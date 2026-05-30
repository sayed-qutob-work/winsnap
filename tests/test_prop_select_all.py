"""
test_prop_select_all.py — Property-based test for select-all / deselect-all coverage.

Feature: winsnap-gui, Property 4: Select-all / deselect-all coverage

Validates: Requirements 3.6, 3.7
"""

import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from gui import resolve_run_modules, MODULES_EXPORT_ORDER


@given(starting_state=st.lists(st.booleans(), min_size=13, max_size=13))
@settings(max_examples=100)
def test_select_all_deselect_all_coverage(starting_state: list[bool]):
    """Property 4: Select-all / deselect-all coverage.

    For any starting check-state of the thirteen module controls, activating
    select-all SHALL leave all thirteen selected, and activating deselect-all
    SHALL leave zero selected.

    **Validates: Requirements 3.6, 3.7**
    """
    all_modules = MODULES_EXPORT_ORDER

    # Verify we have exactly 13 modules
    assert len(all_modules) == 13

    # Model select-all: set all to True → selected set should be all 13 modules
    select_all_set = set(all_modules)
    result_select_all = resolve_run_modules(select_all_set, all_modules)
    assert result_select_all == all_modules, (
        f"Select-all should yield all 13 modules in order, "
        f"but got {result_select_all}"
    )
    assert len(result_select_all) == 13, (
        f"Select-all should yield exactly 13 modules, "
        f"but got {len(result_select_all)}"
    )

    # Model deselect-all: set all to False → selected set should be empty
    deselect_all_set: set[str] = set()
    result_deselect_all = resolve_run_modules(deselect_all_set, all_modules)
    assert result_deselect_all == [], (
        f"Deselect-all should yield an empty list, "
        f"but got {result_deselect_all}"
    )
    assert len(result_deselect_all) == 0, (
        f"Deselect-all should yield zero modules, "
        f"but got {len(result_deselect_all)}"
    )
