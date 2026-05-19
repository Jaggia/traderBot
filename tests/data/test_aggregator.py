"""
Tests for src/data/aggregator.py — aggregate_1m_to_5m().

The function is a pure OHLCV resampler: takes a 1-min tz-aware DataFrame
and returns 5-min bars filtered to regular trading hours (09:30–15:55) and
weekdays only.  No I/O, no external dependencies.

Coverage:
  - OHLCV aggregation rules (open=first, high=max, low=min, close=last, volume=sum)
  - Bar label is the left edge of each 5-min window
  - Pre-market and post-market rows are excluded
  - Saturday and Sunday rows are excluded
  - Optional columns: symbol (first), trade_count (sum)
  - vwap column is silently dropped (can't be naively aggregated)
  - NaN bars produced by resample from empty windows are dropped
"""
import numpy as np
import pandas as pd
import pytest

from src.data.aggregator import aggregate_1m_to_5m, aggregate_to_Nmin


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_1m(start: str, periods: int, tz: str = "America/New_York", **kwargs) -> pd.DataFrame:
    """Build a synthetic 1-min OHLCV DataFrame.

    All five OHLCV columns default to flat 100/101/99/100/1000.
    Pass any column name as a keyword arg to override (list or scalar).
    """
    idx = pd.date_range(start, periods=periods, freq="1min", tz=tz)
    n = len(idx)
    data = {
        "open":   np.broadcast_to(kwargs.get("open",   100.0), n).copy().astype(float),
        "high":   np.broadcast_to(kwargs.get("high",   101.0), n).copy().astype(float),
        "low":    np.broadcast_to(kwargs.get("low",     99.0), n).copy().astype(float),
        "close":  np.broadcast_to(kwargs.get("close",  100.0), n).copy().astype(float),
        "volume": np.broadcast_to(kwargs.get("volume", 1000.0), n).copy().astype(float),
    }
    return pd.DataFrame(data, index=idx)


# ---------------------------------------------------------------------------
# OHLCV aggregation rules
# ---------------------------------------------------------------------------

class TestOHLCVAggregation:
    """Verify that each OHLCV column is reduced with the correct function."""

    def test_open_is_first(self):
        """5-min open equals the first 1-min open in the window."""
        opens = [10.0, 20.0, 30.0, 40.0, 50.0]
        df = _make_1m("2025-01-02 09:30", 5, open=opens)
        result = aggregate_1m_to_5m(df)
        assert result["open"].iloc[0] == pytest.approx(10.0)

    def test_high_is_max(self):
        """5-min high equals the maximum of the five 1-min highs."""
        highs = [101.0, 105.0, 102.0, 103.0, 100.0]
        df = _make_1m("2025-01-02 09:30", 5, high=highs)
        result = aggregate_1m_to_5m(df)
        assert result["high"].iloc[0] == pytest.approx(105.0)

    def test_low_is_min(self):
        """5-min low equals the minimum of the five 1-min lows."""
        lows = [99.0, 96.0, 98.0, 97.0, 99.0]
        df = _make_1m("2025-01-02 09:30", 5, low=lows)
        result = aggregate_1m_to_5m(df)
        assert result["low"].iloc[0] == pytest.approx(96.0)

    def test_close_is_last(self):
        """5-min close equals the last 1-min close in the window."""
        closes = [100.0, 101.0, 102.0, 103.0, 104.0]
        df = _make_1m("2025-01-02 09:30", 5, close=closes)
        result = aggregate_1m_to_5m(df)
        assert result["close"].iloc[0] == pytest.approx(104.0)

    def test_volume_is_sum(self):
        """5-min volume equals the sum of the five 1-min volumes."""
        vols = [100.0, 200.0, 300.0, 400.0, 500.0]
        df = _make_1m("2025-01-02 09:30", 5, volume=vols)
        result = aggregate_1m_to_5m(df)
        assert result["volume"].iloc[0] == pytest.approx(1500.0)

    def test_bar_labeled_with_window_start(self):
        """Bar timestamp is the left edge of the 5-min window (label='left')."""
        df = _make_1m("2025-01-02 09:30", 5)
        result = aggregate_1m_to_5m(df)
        ts = result.index[0]
        assert (ts.hour, ts.minute) == (9, 30)

    def test_ten_bars_produce_two_windows(self):
        """10 consecutive 1-min bars at 09:30 yield exactly 2 five-min bars."""
        df = _make_1m("2025-01-02 09:30", 10)
        result = aggregate_1m_to_5m(df)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Time filter: 09:30 – 15:55
# ---------------------------------------------------------------------------

class TestTimeFilter:
    """Pre-market, post-market, and within-RTH filtering of aggregated bars."""

    def test_premarket_bucket_excluded(self):
        """1-min bars starting at 09:25 produce a 09:25 bucket that is dropped.

        The 09:30 bucket from the same data is kept.
        """
        # bars 09:25–09:34: 09:25 bucket filtered out, 09:30 bucket kept
        df = _make_1m("2025-01-02 09:25", 10)
        result = aggregate_1m_to_5m(df)
        assert len(result) == 1
        assert (result.index[0].hour, result.index[0].minute) == (9, 30)

    def test_post_market_bucket_excluded(self):
        """The 16:00 bucket is excluded; 15:55 is the last valid bar."""
        # bars 15:50–16:04: buckets 15:50, 15:55, 16:00 — last one dropped
        df = _make_1m("2025-01-02 15:50", 15)
        result = aggregate_1m_to_5m(df)
        bar_times = {(t.hour, t.minute) for t in result.index}
        assert (16, 0) not in bar_times
        assert (15, 55) in bar_times

    def test_all_bars_within_rth(self):
        """Every bar in the output falls within 09:30–15:55 inclusive."""
        # Full RTH: 09:30 through 16:04 (395 bars covers end of day)
        df = _make_1m("2025-01-02 09:30", 395)
        result = aggregate_1m_to_5m(df)
        for ts in result.index:
            assert (ts.hour * 60 + ts.minute) >= (9 * 60 + 30)
            assert (ts.hour * 60 + ts.minute) <= (15 * 60 + 55)


# ---------------------------------------------------------------------------
# Weekday filter
# ---------------------------------------------------------------------------

class TestWeekdayFilter:
    """Weekend day exclusion: Saturday and Sunday bars are dropped, weekdays are kept."""

    def test_saturday_excluded(self):
        """All bars on a Saturday are dropped (empty result)."""
        # 2025-01-04 is a Saturday
        df = _make_1m("2025-01-04 09:30", 5)
        result = aggregate_1m_to_5m(df)
        assert len(result) == 0

    def test_sunday_excluded(self):
        """All bars on a Sunday are dropped (empty result)."""
        # 2025-01-05 is a Sunday
        df = _make_1m("2025-01-05 09:30", 5)
        result = aggregate_1m_to_5m(df)
        assert len(result) == 0

    def test_weekday_kept(self):
        """Bars on a weekday pass the filter."""
        # 2025-01-06 is a Monday
        df = _make_1m("2025-01-06 09:30", 5)
        result = aggregate_1m_to_5m(df)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Optional columns
# ---------------------------------------------------------------------------

class TestOptionalColumns:
    """Handling of optional columns: symbol (first), trade_count (sum), vwap (dropped)."""

    def test_symbol_carried_forward(self):
        """symbol column is aggregated with 'first' and present in output."""
        df = _make_1m("2025-01-02 09:30", 5)
        df["symbol"] = "SYMBOL"
        result = aggregate_1m_to_5m(df)
        assert "symbol" in result.columns
        assert result["symbol"].iloc[0] == "SYMBOL"

    def test_trade_count_summed(self):
        """trade_count column is summed across the five 1-min bars."""
        df = _make_1m("2025-01-02 09:30", 5)
        df["trade_count"] = 10
        result = aggregate_1m_to_5m(df)
        assert "trade_count" in result.columns
        assert result["trade_count"].iloc[0] == pytest.approx(50.0)

    def test_vwap_dropped(self):
        """vwap column is silently excluded — cannot be naively aggregated."""
        df = _make_1m("2025-01-02 09:30", 5)
        df["vwap"] = 100.0
        result = aggregate_1m_to_5m(df)
        assert "vwap" not in result.columns

    def test_missing_optional_columns_no_error(self):
        """Aggregation succeeds when symbol and trade_count are absent."""
        df = _make_1m("2025-01-02 09:30", 5)
        result = aggregate_1m_to_5m(df)
        assert set(result.columns) == {"open", "high", "low", "close", "volume"}


# ---------------------------------------------------------------------------
# NaN / incomplete window handling
# ---------------------------------------------------------------------------

class TestNaNHandling:
    """NaN and incomplete-window handling: all-NaN resample buckets are dropped."""

    def test_empty_windows_dropped(self):
        """Resample windows with no 1-min bars (all-NaN) are dropped.

        Achieved by creating a gap: two clusters separated by a missing window.
        The gap bucket has no bars and its aggregated row is NaN → dropped.
        """
        # cluster A: 09:30–09:34 (window 09:30), cluster B: 09:40–09:44 (window 09:40)
        # gap:       09:35–09:39 (window 09:35) — no bars → NaN → dropped
        cluster_a = _make_1m("2025-01-02 09:30", 5)
        cluster_b = _make_1m("2025-01-02 09:40", 5)
        df = pd.concat([cluster_a, cluster_b]).sort_index()

        result = aggregate_1m_to_5m(df)

        bar_minutes = [t.minute for t in result.index]
        assert 35 not in bar_minutes  # gap window dropped
        assert 30 in bar_minutes
        assert 40 in bar_minutes


# ---------------------------------------------------------------------------
# aggregate_to_Nmin — generic resampler
# ---------------------------------------------------------------------------

def _make_5m(start: str, periods: int, tz: str = "America/New_York", **kwargs) -> pd.DataFrame:
    """Build synthetic 5-min OHLCV bars."""
    idx = pd.date_range(start, periods=periods, freq="5min", tz=tz)
    n = len(idx)
    data = {
        "open":   np.broadcast_to(kwargs.get("open",   100.0), n).copy().astype(float),
        "high":   np.broadcast_to(kwargs.get("high",   101.0), n).copy().astype(float),
        "low":    np.broadcast_to(kwargs.get("low",     99.0), n).copy().astype(float),
        "close":  np.broadcast_to(kwargs.get("close",  100.0), n).copy().astype(float),
        "volume": np.broadcast_to(kwargs.get("volume", 1000.0), n).copy().astype(float),
    }
    return pd.DataFrame(data, index=idx)


class TestAggregateToNmin:
    """Tests for the generic N-minute resampler."""

    def test_5m_to_15m_three_bars_collapse(self):
        """3 five-min bars collapse into 1 fifteen-min bar."""
        df = _make_5m("2025-01-02 09:30", 3)
        result = aggregate_to_Nmin(df, 15)
        assert len(result) == 1

    def test_5m_to_15m_ohlcv_rules(self):
        """OHLCV aggregation rules: open=first, high=max, low=min, close=last, volume=sum."""
        opens  = [10.0, 20.0, 30.0]
        highs  = [15.0, 25.0, 35.0]
        lows   = [5.0,  15.0, 25.0]
        closes = [12.0, 22.0, 32.0]
        vols   = [100.0, 200.0, 300.0]
        df = _make_5m("2025-01-02 09:30", 3, open=opens, high=highs, low=lows, close=closes, volume=vols)
        result = aggregate_to_Nmin(df, 15)
        assert result["open"].iloc[0] == pytest.approx(10.0)
        assert result["high"].iloc[0] == pytest.approx(35.0)
        assert result["low"].iloc[0] == pytest.approx(5.0)
        assert result["close"].iloc[0] == pytest.approx(32.0)
        assert result["volume"].iloc[0] == pytest.approx(600.0)

    def test_partial_candle_kept_if_valid(self):
        """A partial window with valid data is kept (unlike all-NaN which is dropped)."""
        # 4 bars: first 3 form a complete 15-min candle, 4th is partial but valid
        df = _make_5m("2025-01-02 09:30", 4)
        result = aggregate_to_Nmin(df, 15)
        assert len(result) == 2  # both complete and partial candle survive

    def test_empty_gap_window_dropped(self):
        """A resample window with no bars (all-NaN) is dropped."""
        # Two clusters with a 15-min gap
        cluster_a = _make_5m("2025-01-02 09:30", 3)  # 09:30 candle
        cluster_b = _make_5m("2025-01-02 10:00", 3)  # 10:00 candle (gap at 09:45)
        df = pd.concat([cluster_a, cluster_b]).sort_index()
        result = aggregate_to_Nmin(df, 15)
        bar_minutes = [t.minute for t in result.index]
        assert 45 not in bar_minutes  # gap window dropped
        assert 30 in bar_minutes
        assert 0 in bar_minutes

    def test_no_time_filter_applied(self):
        """Unlike aggregate_1m_to_5m, no time-of-day filter is applied."""
        # Pre-market bars at 08:00 should survive
        df = _make_5m("2025-01-02 08:00", 6)
        result = aggregate_to_Nmin(df, 15)
        assert len(result) == 2  # 6 bars → 2 fifteen-min candles
        # First bar at 08:00 — would be filtered by aggregate_1m_to_5m
        assert result.index[0].hour == 8

    def test_n5_on_1m_matches_aggregate_1m_to_5m_core(self):
        """N=5 on 1-min data produces same OHLCV values as aggregate_1m_to_5m (ignoring time filter)."""
        df_1m = _make_1m("2025-01-02 09:30", 10,
                         open=[10, 11, 12, 13, 14, 15, 16, 17, 18, 19],
                         high=[20, 21, 22, 23, 24, 25, 26, 27, 28, 29],
                         low=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
                         close=[15, 16, 17, 18, 19, 20, 21, 22, 23, 24],
                         volume=[100, 200, 300, 400, 500, 600, 700, 800, 900, 1000])
        result_generic = aggregate_to_Nmin(df_1m, 5)
        result_specific = aggregate_1m_to_5m(df_1m)
        # Both should have 2 bars with identical OHLCV
        assert len(result_generic) == len(result_specific)
        for col in ["open", "high", "low", "close", "volume"]:
            np.testing.assert_allclose(
                result_generic[col].values, result_specific[col].values
            )
