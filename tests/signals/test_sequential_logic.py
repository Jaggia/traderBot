"""Unit tests for src/signals/sequential_logic.py.

Tests the generic state machine and event detectors in isolation —
no indicators, no DataFrames, just the primitives.
"""
import numpy as np
import pandas as pd
import pytest

from src.signals.sequential_logic import (
    apply_sequential_logic,
    within_window,
    crossover,
    crossunder,
    series_crossover,
    series_crossunder,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bool_arr(*bits) -> np.ndarray:
    return np.array(bits, dtype=bool)


def _series(*values) -> pd.Series:
    return pd.Series(values, dtype=float)


# ---------------------------------------------------------------------------
# apply_sequential_logic
# ---------------------------------------------------------------------------

class TestApplySequentialLogic:
    def test_trigger0_then_trigger1_within_window(self):
        """Trigger0 at index 2, trigger1 at index 4 (within window=5) → signal at 4."""
        trigger0 = _bool_arr(0, 0, 1, 0, 0, 0, 0, 0, 0, 0)
        trigger1 = _bool_arr(0, 0, 0, 0, 1, 0, 0, 0, 0, 0)
        result = apply_sequential_logic(trigger0, trigger1, window=5)
        assert result[4] is np.bool_(True)
        assert result.sum() == 1

    def test_trigger1_outside_window_no_signal(self):
        """Trigger0 at index 2, trigger1 at index 9 (outside window=5) → no signal."""
        trigger0 = _bool_arr(0, 0, 1, 0, 0, 0, 0, 0, 0, 0)
        trigger1 = _bool_arr(0, 0, 0, 0, 0, 0, 0, 0, 0, 1)
        result = apply_sequential_logic(trigger0, trigger1, window=5)
        assert result.sum() == 0

    def test_resets_after_single_fire(self):
        """Trigger0 at 2, trigger1 at 4, second trigger1 at 5 → only one signal (reset)."""
        trigger0 = _bool_arr(0, 0, 1, 0, 0, 0, 0, 0, 0, 0)
        trigger1 = _bool_arr(0, 0, 0, 0, 1, 1, 0, 0, 0, 0)
        result = apply_sequential_logic(trigger0, trigger1, window=5)
        assert result[4] is np.bool_(True)
        assert result[5] is np.bool_(False)
        assert result.sum() == 1

    def test_rearms_after_fire(self):
        """Fire → reset → new trigger0 → second trigger1 → two signals total."""
        trigger0 = _bool_arr(0, 1, 0, 0, 0, 1, 0, 0, 0, 0)
        trigger1 = _bool_arr(0, 0, 0, 1, 0, 0, 0, 1, 0, 0)
        result = apply_sequential_logic(trigger0, trigger1, window=5)
        assert result[3] is np.bool_(True)
        assert result[7] is np.bool_(True)
        assert result.sum() == 2

    def test_trigger0_before_trigger1_no_signal_before_trigger0(self):
        """Trigger1 event before any trigger0 → no signal."""
        trigger0 = _bool_arr(0, 0, 0, 0, 1, 0, 0, 0, 0, 0)
        trigger1 = _bool_arr(1, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        result = apply_sequential_logic(trigger0, trigger1, window=5)
        assert result[0] is np.bool_(False)

    def test_trigger0_and_trigger1_same_bar(self):
        """Trigger0 and trigger1 on the same bar → signal fires (trigger0 then trigger1 on same i)."""
        trigger0 = _bool_arr(0, 0, 1, 0, 0)
        trigger1 = _bool_arr(0, 0, 1, 0, 0)
        result = apply_sequential_logic(trigger0, trigger1, window=2)
        assert result[2] is np.bool_(True)
        assert result.sum() == 1

    def test_window_boundary_exactly(self):
        """Fire exactly at window boundary (i - trigger0_bar == window) → signal fires."""
        trigger0 = _bool_arr(0, 1, 0, 0, 0, 0, 0)
        trigger1 = _bool_arr(0, 0, 0, 0, 0, 0, 1)
        # trigger0_bar=1, fire at i=6: 6-1=5 == window=5, so still armed
        result = apply_sequential_logic(trigger0, trigger1, window=5)
        assert result[6] is np.bool_(True)

    def test_window_exceeded_by_one(self):
        """Fire one bar past window → no signal."""
        trigger0 = _bool_arr(0, 1, 0, 0, 0, 0, 0, 0)
        trigger1 = _bool_arr(0, 0, 0, 0, 0, 0, 0, 1)
        # trigger0_bar=1, fire at i=7: 7-1=6 > window=5 → expired
        result = apply_sequential_logic(trigger0, trigger1, window=5)
        assert result.sum() == 0

    def test_rearm_resets_window(self):
        """Second trigger0 event resets window so fire can occur later than original trigger0+window."""
        trigger0 = _bool_arr(1, 0, 0, 0, 0, 1, 0, 0, 0, 0)
        trigger1 = _bool_arr(0, 0, 0, 0, 0, 0, 0, 0, 0, 1)
        result = apply_sequential_logic(trigger0, trigger1, window=4)
        assert result[9] is np.bool_(True)

    def test_empty_arrays(self):
        """Empty input → empty output, no error."""
        result = apply_sequential_logic(np.array([], dtype=bool), np.array([], dtype=bool), window=5)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# within_window
# ---------------------------------------------------------------------------

class TestWithinWindow:
    def test_trigger_fires_within_window(self):
        """Trigger at bar 3 with window=2 → bars 3, 4, 5 are True."""
        trigger = pd.Series([False, False, False, True, False, False, False])
        result = within_window(trigger, window=2)
        assert result.iloc[3] is np.bool_(True)
        assert result.iloc[4] is np.bool_(True)
        assert result.iloc[5] is np.bool_(True)
        assert result.iloc[6] is np.bool_(False)

    def test_no_trigger_all_false(self):
        trigger = pd.Series([False] * 10)
        result = within_window(trigger, window=3)
        assert not result.any()

    def test_window_zero(self):
        """window=0 means only the bar of the trigger itself is True."""
        trigger = pd.Series([False, False, True, False, False])
        result = within_window(trigger, window=0)
        assert result.iloc[2] is np.bool_(True)
        assert result.iloc[3] is np.bool_(False)


# ---------------------------------------------------------------------------
# crossover / crossunder
# ---------------------------------------------------------------------------

class TestCrossoverCrossunder:
    def test_crossover_fires_on_cross_bar(self):
        s = _series(5, 5, 9, 15, 15)  # crosses above 10 at index 3
        result = crossover(s, 10)
        assert not result.iloc[2]  # still below at index 2 (9 < 10)
        assert result.iloc[3]      # crosses above at index 3

    def test_crossover_no_false_positive_staying_above(self):
        s = _series(5, 15, 20, 25)   # crosses at index 1, stays above after
        result = crossover(s, 10)
        assert result.iloc[1]
        assert not result.iloc[2]  # already above — not a new cross

    def test_crossunder_fires_on_cross_bar(self):
        s = _series(15, 15, 8, 5)   # crosses below 10 at index 2
        result = crossunder(s, 10)
        assert result.iloc[2]

    def test_crossunder_no_signal_when_staying_below(self):
        s = _series(5, 3, 2, 1)
        result = crossunder(s, 10)
        assert not result.iloc[1]  # already below from the start (no prior above)

    def test_crossover_first_bar_never_fires(self):
        """First bar has no prior value — shift produces NaN → no false cross."""
        s = _series(15, 5, 5)  # 15 > 10 on bar 0, but no prior bar
        result = crossover(s, 10)
        assert not result.iloc[0]


# ---------------------------------------------------------------------------
# series_crossover / series_crossunder
# ---------------------------------------------------------------------------

class TestSeriesCrossoverCrossunder:
    def test_series_crossover_fast_crosses_slow(self):
        fast = _series(1, 2, 3, 6, 9)
        slow = _series(5, 5, 5, 5, 5)
        result = series_crossover(fast, slow)
        # fast crosses above slow at index 3 (3→6, slow stays at 5)
        assert result.iloc[3]
        assert not result.iloc[4]  # already above

    def test_series_crossunder_fast_crosses_slow(self):
        fast = _series(9, 8, 7, 4, 2)
        slow = _series(5, 5, 5, 5, 5)
        result = series_crossunder(fast, slow)
        # fast crosses below slow at index 3 (7→4, slow at 5)
        assert result.iloc[3]
        assert not result.iloc[4]
