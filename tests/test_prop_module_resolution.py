"""
test_prop_module_resolution.py — Property-based test for module run resolution.

Feature: gui-backend-alignment, Requirement 5: Module ordering and list
derivation from the manifest.

gui.py's hardcoded MODULES_EXPORT_ORDER/MODULES_RESTORE_ORDER constants were
removed (Task 3.6); there is now exactly one canonical module order,
modules.manifest.MODULE_NAMES, and every resolve_run_modules call site
passes it as the order argument (Req 5.1, 5.2).

For any selection subset of the thirteen manifest modules and for any
ordering of those modules (the canonical manifest order, or an arbitrary
permutation of it -- resolve_run_modules itself is order-agnostic and makes
no assumption about *which* order it is given), `resolve_run_modules` SHALL
return exactly the selected modules, in the given order, with no duplicates
and no unselected modules -- i.e. `result == [m for m in order if m in
selection]`.

**Validates: Requirements 5.1, 5.2, 11.6**
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from hypothesis import given, settings
from hypothesis import strategies as st

from modules import manifest

from gui import resolve_run_modules

# The single canonical module order (Req 5.1) -- replaces the removed
# MODULES_EXPORT_ORDER/MODULES_RESTORE_ORDER pair.
ALL_MODULES = manifest.MODULE_NAMES

module_subsets = st.frozensets(st.sampled_from(ALL_MODULES))

# Strategy: the canonical manifest order, or an arbitrary permutation of it.
# resolve_run_modules takes an explicit `order` argument and must honor
# whatever order it is given -- this preserves the original property's
# breadth ("for any module ordering") even though gui.py itself now only
# ever passes one canonical order.
module_orders = st.permutations(ALL_MODULES).map(list)


@settings(max_examples=200)
@given(selection=module_subsets, order=module_orders)
def test_resolve_run_modules_returns_selected_in_canonical_order(selection, order):
    """Property 3: resolve_run_modules returns exactly the selected modules
    in the given order, with no duplicates and no unselected modules."""
    result = resolve_run_modules(selection, order)

    # The result must equal the filtered order
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


@settings(max_examples=200)
@given(selection=module_subsets)
def test_resolve_run_modules_with_manifest_order(selection):
    """resolve_run_modules called with the actual manifest.MODULE_NAMES
    order -- the single order gui.py now uses for both export and restore
    (Req 5.1, 5.2) -- returns the selected modules in manifest position,
    and 'apps' precedes 'startup'/'taskbar' whenever all three are
    selected (regression guard for the manifest ordering rationale)."""
    result = resolve_run_modules(selection, manifest.MODULE_NAMES)

    expected = [m for m in manifest.MODULE_NAMES if m in selection]
    assert result == expected

    if {"apps", "startup", "taskbar"} <= selection:
        assert result.index("apps") < result.index("startup")
        assert result.index("apps") < result.index("taskbar")
