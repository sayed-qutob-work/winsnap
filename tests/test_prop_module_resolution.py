"""
test_prop_module_resolution.py — Property-based test for module run resolution.

Feature: winsnap-gui, Property 3: Module run resolution

For any selection subset of the thirteen modules and for any module ordering
(export order or restore order), `resolve_run_modules` SHALL return exactly the
selected modules, in the given canonical order, with no duplicates and no
unselected modules — i.e. `result == [m for m in order if m in selection]`.

**Validates: Requirements 3.2, 3.3, 9.3**
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from hypothesis import given, settings
from hypothesis import strategies as st

from gui import resolve_run_modules, MODULES_EXPORT_ORDER, MODULES_RESTORE_ORDER

# Strategy: generate arbitrary subsets of the 13 module names
ALL_MODULES = MODULES_EXPORT_ORDER  # Both lists contain the same 13 names

module_subsets = st.frozensets(st.sampled_from(ALL_MODULES))

# Strategy: pick either export or restore order
module_orders = st.sampled_from([MODULES_EXPORT_ORDER, MODULES_RESTORE_ORDER])


@settings(max_examples=200)
@given(selection=module_subsets, order=module_orders)
def test_resolve_run_modules_returns_selected_in_canonical_order(selection, order):
    """Property 3: resolve_run_modules returns exactly the selected modules
    in canonical order, with no duplicates and no unselected modules."""
    result = resolve_run_modules(selection, order)

    # The result must equal the filtered canonical order
    expected = [m for m in order if m in selection]
    assert result == expected, (
        f"Expected {expected}, got {result} "
        f"for selection={selection}, order={order}"
    )

    # No duplicates
    assert len(result) == len(set(result)), (
        f"Duplicates found in result: {result}"
    )

    # All items in result are in selection
    for m in result:
        assert m in selection, (
            f"Module {m!r} in result but not in selection {selection}"
        )

    # No unselected modules appear in result
    for m in result:
        assert m in selection

    # All selected modules that exist in order appear in result
    for m in selection:
        if m in order:
            assert m in result, (
                f"Selected module {m!r} missing from result"
            )
