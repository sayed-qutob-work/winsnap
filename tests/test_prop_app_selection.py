"""
test_prop_app_selection.py — Property-based test for app-selection recording.

Feature: winsnap-gui, Property 5: App-selection recording

Validates: Requirements 5.2, 5.4, 5.5, 5.7
"""

import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from gui import record_app_selection

# Strategy: generate a list of app dicts with at least a "name" key
app_dict_strategy = st.fixed_dictionaries({"name": st.text(min_size=1)})


@st.composite
def apps_with_mask(draw):
    """Generate a list of app dicts and a parallel boolean mask of the same length."""
    apps = draw(st.lists(app_dict_strategy, min_size=0, max_size=20))
    mask = draw(st.lists(st.booleans(), min_size=len(apps), max_size=len(apps)))
    return apps, mask


@given(
    winget_data=apps_with_mask(),
    manual_data=apps_with_mask(),
    confirmed=st.booleans(),
)
@settings(max_examples=100)
def test_app_selection_recording(winget_data, manual_data, confirmed):
    """Property 5: App-selection recording.

    For any lists of discovered winget apps and manual apps and for any boolean
    selection masks over them: when confirmed, record_app_selection SHALL return
    exactly the entries whose mask is True within each group; when cancelled, it
    SHALL return ([], []) regardless of the masks. With the all-True default mask
    (initial state), confirming SHALL return every entry in both groups.

    **Validates: Requirements 5.2, 5.4, 5.5, 5.7**
    """
    winget, winget_states = winget_data
    manual, manual_states = manual_data

    result = record_app_selection(winget_states, manual_states, winget, manual, confirmed)

    if not confirmed:
        # Cancelled: SHALL return ([], []) regardless of masks
        assert result == ([], []), (
            f"Expected ([], []) when cancelled, got {result}"
        )
    else:
        # Confirmed: SHALL return exactly entries whose mask is True
        expected_winget = [app for app, sel in zip(winget, winget_states) if sel]
        expected_manual = [app for app, sel in zip(manual, manual_states) if sel]
        assert result == (expected_winget, expected_manual), (
            f"Expected ({expected_winget}, {expected_manual}) but got {result}"
        )


@given(
    winget=st.lists(app_dict_strategy, min_size=0, max_size=20),
    manual=st.lists(app_dict_strategy, min_size=0, max_size=20),
)
@settings(max_examples=100)
def test_app_selection_all_true_mask_returns_all(winget, manual):
    """Property 5 (all-True default mask sub-property).

    With the all-True default mask (initial state), confirming SHALL return
    every entry in both groups.

    **Validates: Requirements 5.2, 5.4, 5.5, 5.7**
    """
    winget_states = [True] * len(winget)
    manual_states = [True] * len(manual)

    result = record_app_selection(winget_states, manual_states, winget, manual, True)

    assert result == (winget, manual), (
        f"With all-True mask, expected all entries returned. Got {result}"
    )
