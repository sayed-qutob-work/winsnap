"""Unit tests for record_app_selection function.

Validates Requirements: 5.2, 5.4, 5.5, 5.7
"""

import pytest

from gui import record_app_selection


class TestRecordAppSelectionConfirmed:
    """Tests for confirmed=True behavior (Requirement 5.4)."""

    def test_returns_selected_entries_only(self):
        """When confirmed, only entries with True mask are returned."""
        winget = [{"name": "app1"}, {"name": "app2"}, {"name": "app3"}]
        manual = [{"name": "man1"}, {"name": "man2"}]
        winget_states = [True, False, True]
        manual_states = [False, True]

        result = record_app_selection(winget_states, manual_states, winget, manual, True)

        assert result == ([{"name": "app1"}, {"name": "app3"}], [{"name": "man2"}])

    def test_all_true_returns_all_entries(self):
        """With all-True mask (initial state), confirming returns every entry (Req 5.2)."""
        winget = [{"name": "a"}, {"name": "b"}]
        manual = [{"name": "x"}, {"name": "y"}]
        winget_states = [True, True]
        manual_states = [True, True]

        result = record_app_selection(winget_states, manual_states, winget, manual, True)

        assert result == (winget, manual)

    def test_all_false_returns_empty_lists(self):
        """When confirmed with all-False mask, returns empty lists for both groups."""
        winget = [{"name": "a"}, {"name": "b"}]
        manual = [{"name": "x"}]
        winget_states = [False, False]
        manual_states = [False]

        result = record_app_selection(winget_states, manual_states, winget, manual, True)

        assert result == ([], [])

    def test_empty_groups_return_empty_lists(self):
        """Empty groups produce empty lists in the result (Req 5.7)."""
        result = record_app_selection([], [], [], [], True)

        assert result == ([], [])

    def test_one_empty_group_one_populated(self):
        """One empty group and one populated group works correctly."""
        winget = [{"name": "a"}]
        winget_states = [True]

        result = record_app_selection(winget_states, [], winget, [], True)

        assert result == ([{"name": "a"}], [])


class TestRecordAppSelectionCancelled:
    """Tests for confirmed=False behavior (Requirement 5.5)."""

    def test_cancelled_returns_empty_tuples(self):
        """When cancelled, returns ([], []) regardless of masks."""
        winget = [{"name": "a"}, {"name": "b"}]
        manual = [{"name": "x"}]
        winget_states = [True, True]
        manual_states = [True]

        result = record_app_selection(winget_states, manual_states, winget, manual, False)

        assert result == ([], [])

    def test_cancelled_ignores_all_true_masks(self):
        """Cancellation ignores even all-True masks."""
        winget = [{"name": "a"}]
        manual = [{"name": "x"}]

        result = record_app_selection([True], [True], winget, manual, False)

        assert result == ([], [])

    def test_cancelled_with_empty_groups(self):
        """Cancellation with empty groups still returns ([], [])."""
        result = record_app_selection([], [], [], [], False)

        assert result == ([], [])
