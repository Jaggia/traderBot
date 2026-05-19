"""Tests for src/signals/composite_pipeline.py — EMA 233 intrabar-cross via composite pipeline."""
import numpy as np
import pandas as pd
import pytest

from src.signals.indicator_pair_pipeline import (
    compute_indicators,
    generate_signals,
)

def _identify_15m_close_bars(idx):
    from src.signals.indicator_pair_pipeline import _identify_resampled_close_bars
    return _identify_resampled_close_bars(idx, 15)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_5m_bars(n, start="2025-01-02 09:30", base_price=500.0, tz="America/New_York"):
    """Create n 5-min OHLCV bars at a flat price."""
    idx = pd.date_range(start, periods=n, freq="5min", tz=tz)
    return pd.DataFrame({
        "open": base_price,
        "high": base_price + 0.5,
        "low": base_price - 0.5,
        "close": base_price,
        "volume": 1_000_000.0,
    }, index=idx)


def _ema_config(period=233, offset=0.02):
    """Minimal config for the EMA pipeline."""
    return {
        "strategy": {"signal_system": "ema_233"},
        "signals_ema": {
            "ema_period": period,
            "entry_offset_cents": offset,
            "base_timeframe_min": 15,
        },
    }


# ---------------------------------------------------------------------------
# _identify_15m_close_bars
# ---------------------------------------------------------------------------

class TestIdentify15mCloseBars:
    def test_three_bars_per_candle(self):
        """In a clean 5-min series, every 3rd bar is the close bar of its 15-min candle."""
        idx = pd.date_range("2025-01-02 09:30", periods=6, freq="5min", tz="America/New_York")
        is_close = _identify_15m_close_bars(idx)
        # 09:30, 09:35, 09:40=close, 09:45, 09:50, 09:55=close
        expected = [False, False, True, False, False, True]
        assert list(is_close.values) == expected

    def test_last_bar_always_close(self):
        """The very last bar is always marked as a close bar."""
        idx = pd.date_range("2025-01-02 09:30", periods=4, freq="5min", tz="America/New_York")
        is_close = _identify_15m_close_bars(idx)
        assert bool(is_close.iloc[-1])


# ---------------------------------------------------------------------------
# compute_indicators
# ---------------------------------------------------------------------------

class TestComputeIndicators:
    def test_adds_expected_columns(self):
        """compute_indicators adds ema_233, is_15m_close_bar, ema_entry_long/short."""
        df = _make_5m_bars(60)
        result = compute_indicators(df, _ema_config(period=10))
        for col in ["ema_233", "is_15m_close_bar", "ema_entry_long", "ema_entry_short"]:
            assert col in result.columns, f"Missing column: {col}"

    def test_ema_entry_offset(self):
        """ema_entry_long = ema_233 + offset, ema_entry_short = ema_233 - offset."""
        df = _make_5m_bars(60)
        config = _ema_config(period=10, offset=0.05)
        result = compute_indicators(df, config)
        valid = result.dropna(subset=["ema_233"])
        np.testing.assert_allclose(
            valid["ema_entry_long"].values,
            valid["ema_233"].values + 0.05,
        )
        np.testing.assert_allclose(
            valid["ema_entry_short"].values,
            valid["ema_233"].values - 0.05,
        )


# ---------------------------------------------------------------------------
# generate_signals
# ---------------------------------------------------------------------------

class TestGenerateSignals:
    def _make_cross_scenario(self, cross_type="long"):
        """Build 5-min bars that produce a single intrabar cross on 15-min bars.

        For a long cross: prev 15-min close < EMA, current 15-min high > EMA.
        We use a short EMA period to make crosses predictable.
        """
        # Create enough bars for a short EMA to stabilize, then force a cross
        n = 30  # 30 5-min bars = 10 15-min candles
        idx = pd.date_range("2025-01-02 09:30", periods=n, freq="5min", tz="America/New_York")

        if cross_type == "long":
            # Price starts below EMA, then spikes above
            close = np.full(n, 100.0)
            high = np.full(n, 100.5)
            low = np.full(n, 99.5)
            # First 24 bars: price stays at 100
            # Bars 24-26 (candle 8): close stays at 100 → EMA near 100
            # Bars 27-29 (candle 9): close drops to 98 → EMA still ~100, prev_close=98 < EMA
            close[24:27] = 98.0
            high[24:27] = 98.5
            low[24:27] = 97.5
            # Candle 9 (bars 27-29): high spikes to 102 (> EMA ~100), prev_close was 98 (< EMA)
            close[27:30] = 99.0
            high[27:30] = 102.0
            low[27:30] = 98.5
        else:
            # Short cross: prev close > EMA, current low < EMA
            close = np.full(n, 100.0)
            high = np.full(n, 100.5)
            low = np.full(n, 99.5)
            close[24:27] = 102.0
            high[24:27] = 102.5
            low[24:27] = 101.5
            close[27:30] = 101.0
            high[27:30] = 101.5
            low[27:30] = 98.0

        return pd.DataFrame({
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1_000_000.0,
        }, index=idx)

    def test_long_cross_fires(self):
        """A long intrabar cross produces a +1 signal."""
        df = self._make_cross_scenario("long")
        config = _ema_config(period=5, offset=0.02)
        df = compute_indicators(df, config)
        signals = generate_signals(df, config)
        assert (signals == 1).any(), "Expected at least one long signal"

    def test_short_cross_fires(self):
        """A short intrabar cross produces a -1 signal."""
        df = self._make_cross_scenario("short")
        config = _ema_config(period=5, offset=0.02)
        df = compute_indicators(df, config)
        signals = generate_signals(df, config)
        assert (signals == -1).any(), "Expected at least one short signal"

    def test_no_cross_flat_price(self):
        """Flat price produces no signals (price == EMA, no cross)."""
        df = _make_5m_bars(60, base_price=500.0)
        config = _ema_config(period=10, offset=0.02)
        df = compute_indicators(df, config)
        signals = generate_signals(df, config)
        assert (signals == 0).all(), "Expected no signals on flat price"

    def test_signal_only_on_close_bar(self):
        """Signals only appear on bars where is_15m_close_bar is True."""
        df = self._make_cross_scenario("long")
        config = _ema_config(period=5, offset=0.02)
        df = compute_indicators(df, config)
        signals = generate_signals(df, config)
        is_close = df["is_15m_close_bar"]
        signal_bars = signals != 0
        # Every signal bar must also be a close bar
        assert (signal_bars & ~is_close).sum() == 0, "Signal found on non-close bar"

    def test_entry_price_hint_written(self):
        """generate_signals writes entry_price_hint as a side-effect."""
        df = self._make_cross_scenario("long")
        config = _ema_config(period=5, offset=0.02)
        df = compute_indicators(df, config)
        signals = generate_signals(df, config)
        assert "entry_price_hint" in df.columns
        # Hints should be non-NaN where signals fire
        signal_mask = signals != 0
        if signal_mask.any():
            hints = df.loc[signal_mask, "entry_price_hint"]
            assert hints.notna().all(), "entry_price_hint should be set where signals fire"

    def test_entry_price_hint_value(self):
        """Long hint = ema + offset, short hint = ema - offset."""
        df = self._make_cross_scenario("long")
        config = _ema_config(period=5, offset=0.05)
        df = compute_indicators(df, config)
        signals = generate_signals(df, config)
        long_mask = signals == 1
        if long_mask.any():
            expected = df.loc[long_mask, "ema_233"] + 0.05
            actual = df.loc[long_mask, "entry_price_hint"]
            np.testing.assert_allclose(actual.values, expected.values)


# ---------------------------------------------------------------------------
# _identify_15m_close_bars — gap robustness
# ---------------------------------------------------------------------------

class TestIdentify15mCloseBarsGapRobustness:
    """The 15-min close-bar detection must be robust to missing (gap) bars."""

    def test_gap_does_not_produce_false_close_bar(self):
        """A missing bar must NOT cause the bar after the gap to be flagged.

        Setup: 09:30, 09:35, 09:40, [09:45 MISSING], 09:50, 09:55, 10:00, 10:05, 10:10
        True close bars (last bar of each 15-min window):
          09:40  — last bar of [09:30–09:44] window
          09:55  — last bar of [09:45–09:59] window (09:45 is missing but 09:55 is still
                   the last *present* bar in the :45 window)
          10:10  — last bar of [10:00–10:14] window

        With the old sequential algorithm, 09:50 would be incorrectly flagged as a
        close bar because 09:40's floor (09:30) differs from 09:50's floor (09:45).
        The fixed algorithm must NOT flag 09:50 — only 09:40 and 09:55 should be
        flagged as close bars within the first two 15-min windows.
        """
        tz = "America/New_York"
        # Deliberately omit 09:45
        timestamps = pd.DatetimeIndex(
            [
                "2025-01-02 09:30",
                "2025-01-02 09:35",
                "2025-01-02 09:40",
                # 09:45 is intentionally missing
                "2025-01-02 09:50",
                "2025-01-02 09:55",
                "2025-01-02 10:00",
                "2025-01-02 10:05",
                "2025-01-02 10:10",
            ],
            tz=tz,
        )

        is_close = _identify_15m_close_bars(timestamps)

        # 09:40 must be flagged — it is the last present bar of [09:30–09:44]
        assert bool(is_close["2025-01-02 09:40"]), "09:40 should be a 15-min close bar"

        # 09:50 must NOT be flagged — it is NOT the last bar of any 15-min window;
        # 09:55 is the last present bar of [09:45–09:59]
        assert not bool(is_close["2025-01-02 09:50"]), (
            "09:50 should NOT be a 15-min close bar (old sequential bug)"
        )

        # 09:55 must be flagged — it is the last present bar of [09:45–09:59]
        assert bool(is_close["2025-01-02 09:55"]), "09:55 should be a 15-min close bar"

        # 10:10 must be flagged — it is the last present bar of [10:00–10:14]
        assert bool(is_close["2025-01-02 10:10"]), "10:10 should be a 15-min close bar"

    def test_no_gap_behaviour_unchanged(self):
        """With no gaps the fixed algorithm must produce the same result as before."""
        tz = "America/New_York"
        idx = pd.date_range("2025-01-02 09:30", periods=9, freq="5min", tz=tz)
        # 09:30, 09:35, 09:40*, 09:45, 09:50, 09:55*, 10:00, 10:05, 10:10*
        is_close = _identify_15m_close_bars(idx)
        expected = [False, False, True, False, False, True, False, False, True]
        assert list(is_close.values) == expected, (
            f"Expected {expected}, got {list(is_close.values)}"
        )

    def test_last_bar_always_flagged_even_with_gap(self):
        """The very last bar is always a close bar, regardless of its position."""
        tz = "America/New_York"
        timestamps = pd.DatetimeIndex(
            [
                "2025-01-02 09:30",
                "2025-01-02 09:35",
                # gap: 09:40 missing
                "2025-01-02 09:50",
            ],
            tz=tz,
        )
        is_close = _identify_15m_close_bars(timestamps)
        assert bool(is_close.iloc[-1]), "Last bar must always be a close bar"

    def test_mid_session_missing_bar_no_false_close(self):
        """Missing 09:40 bar — floor-based algorithm must still be correct.

        Bars: 09:30, 09:35, [09:40 missing], 09:45, 09:50, 09:55
        The floor algorithm checks: floor(ts, 15min) != floor(ts+5min, 15min)
        - 09:30: floor=09:30, +5min=09:35, floor=09:30 → False
        - 09:35: floor=09:30, +5min=09:40, floor=09:30 → False (09:40 is still in :30 window!)
        - 09:45: floor=09:45, +5min=09:50, floor=09:45 → False
        - 09:50: floor=09:45, +5min=09:55, floor=09:45 → False
        - 09:55: floor=09:45, +5min=10:00, floor=10:00 → True (window boundary)
        Last bar forced True → 09:55 is True anyway.
        """
        tz = "America/New_York"
        timestamps = pd.DatetimeIndex(
            [
                "2025-01-02 09:30",
                "2025-01-02 09:35",
                # 09:40 intentionally missing
                "2025-01-02 09:45",
                "2025-01-02 09:50",
                "2025-01-02 09:55",
            ],
            tz=tz,
        )
        is_close = _identify_15m_close_bars(timestamps)

        # 09:30, 09:35, 09:45, 09:50 must NOT be close bars
        assert not bool(is_close["2025-01-02 09:30"]), "09:30 should NOT be a close bar"
        assert not bool(is_close["2025-01-02 09:35"]), "09:35 should NOT be a close bar"
        assert not bool(is_close["2025-01-02 09:45"]), "09:45 should NOT be a close bar"
        assert not bool(is_close["2025-01-02 09:50"]), "09:50 should NOT be a close bar"
        # 09:55 IS the close bar (window boundary)
        assert bool(is_close["2025-01-02 09:55"]), "09:55 should be a close bar"
