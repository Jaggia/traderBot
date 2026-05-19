"""
Tests for src/live/ibkr_streamer.py.

Mocks ib_insync so no real IB Gateway connection is made.
Covers:
  - _parse_bar_ts():          naive→EST localize, tz-aware→EST convert, invalid raises
  - Market hours filtering:   pre-market and post-market bars dropped
  - Bar accumulation:         1-min bars accumulate without callback mid-window
  - Window boundary:          callback invoked when minute % 5 == 4
  - Aggregation:              open=first, high=max, low=min, close=last, volume=sum
  - Stale-bar reset:          incomplete window discarded on next window start
  - Emitted bar type and timestamp
  - Reconnection:             exponential backoff retries on error
  - Reconnection:             KeyboardInterrupt exits cleanly
  - Reconnection:             gives up after _MAX_RETRIES consecutive failures
"""

import sys
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# Mock ib_insync at module level so the source file's top-level
# `from ib_insync import IB, Stock` never hits the real package.
sys.modules.setdefault("ib_insync", MagicMock())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _est(hour: int, minute: int, date: str = "2026-01-05") -> pd.Timestamp:
    return pd.Timestamp(f"{date} {hour:02d}:{minute:02d}:00", tz="America/New_York")


def _mock_ib_bar(ts: pd.Timestamp, open_: float, high: float, low: float,
                 close: float, volume: int) -> MagicMock:
    """Create a mock ib_insync bar with the date as ISO string."""
    bar = MagicMock()
    # ib_insync returns date as a string like '2026-01-05 09:34:00 US/Eastern'
    # _parse_bar_ts in ibkr_streamer handles this via pd.Timestamp(date_str)
    bar.date = ts.strftime("%Y-%m-%d %H:%M:%S")
    bar.open   = open_
    bar.high   = high
    bar.low    = low
    bar.close  = close
    bar.volume = volume
    return bar


def _streamer(callback=None):
    """Build an IBKRStreamer with ib_insync mocked so no connection is attempted."""
    with patch.dict("sys.modules", {"ib_insync": MagicMock()}):
        from src.live.ibkr_streamer import IBKRStreamer
        cb = callback or MagicMock()
        streamer = IBKRStreamer(on_bar_close=cb, host="127.0.0.1", port=4002, client_id=1)
        return streamer, cb


# ---------------------------------------------------------------------------
# Market hours filtering
# ---------------------------------------------------------------------------

class TestMarketHoursFiltering:
    def test_pre_market_bar_ignored(self):
        """Bar at 09:25 (before open) must not accumulate."""
        streamer, cb = _streamer()
        bar = _mock_ib_bar(_est(9, 25), 400.0, 401.0, 399.0, 400.0, 100)
        streamer._on_1min_bar(bar)

        assert len(streamer._pending) == 0
        cb.assert_not_called()

    def test_930_bar_accepted(self):
        """Bar at exactly 09:30 must be accepted."""
        streamer, cb = _streamer()
        bar = _mock_ib_bar(_est(9, 30), 400.0, 401.0, 399.0, 400.0, 100)
        streamer._on_1min_bar(bar)

        assert len(streamer._pending) == 1

    def test_after_hours_bar_ignored(self):
        """Bar at 16:00 (after close) must not accumulate."""
        streamer, cb = _streamer()
        bar = _mock_ib_bar(_est(16, 0), 400.0, 401.0, 399.0, 400.0, 100)
        streamer._on_1min_bar(bar)

        assert len(streamer._pending) == 0
        cb.assert_not_called()

    def test_bar_at_1500_accepted(self):
        """Bar at 15:00 must be accepted (still market hours)."""
        streamer, cb = _streamer()
        bar = _mock_ib_bar(_est(15, 0), 400.0, 401.0, 399.0, 400.0, 100)
        streamer._on_1min_bar(bar)

        assert len(streamer._pending) == 1


# ---------------------------------------------------------------------------
# Bar accumulation (mid-window, no callback)
# ---------------------------------------------------------------------------

class TestBarAccumulation:
    def test_bars_accumulate_without_callback_mid_window(self):
        """Bars at :30-:33 accumulate; callback NOT fired yet."""
        streamer, cb = _streamer()

        for minute in range(30, 34):
            bar = _mock_ib_bar(_est(9, minute), 400.0, 401.0, 399.0, 400.0, 100)
            streamer._on_1min_bar(bar)

        assert len(streamer._pending) == 4
        cb.assert_not_called()

    def test_second_window_accumulates_after_first_emits(self):
        """After first window emits, pending resets and second window accumulates."""
        streamer, cb = _streamer()

        for minute in range(30, 35):
            bar = _mock_ib_bar(_est(9, minute), 400.0, 401.0, 399.0, 400.0, 100)
            streamer._on_1min_bar(bar)

        assert cb.call_count == 1
        assert len(streamer._pending) == 0

        bar = _mock_ib_bar(_est(9, 35), 401.0, 402.0, 400.0, 401.0, 200)
        streamer._on_1min_bar(bar)

        assert len(streamer._pending) == 1
        assert cb.call_count == 1


# ---------------------------------------------------------------------------
# Window boundary
# ---------------------------------------------------------------------------

class TestWindowBoundary:
    def test_callback_invoked_on_minute_mod5_4(self):
        streamer, cb = _streamer()

        for minute in range(30, 35):
            bar = _mock_ib_bar(_est(9, minute), 400.0, 401.0, 399.0, 400.0, 100)
            streamer._on_1min_bar(bar)

        cb.assert_called_once()

    def test_callback_not_invoked_mid_window(self):
        streamer, cb = _streamer()

        for minute in range(35, 39):  # :35-:38, not :39
            bar = _mock_ib_bar(_est(9, minute), 401.0, 402.0, 400.5, 401.5, 200)
            streamer._on_1min_bar(bar)

        cb.assert_not_called()

    def test_two_complete_windows_emit_twice(self):
        streamer, cb = _streamer()

        for minute in range(30, 40):
            bar = _mock_ib_bar(_est(9, minute), 400.0, 401.0, 399.0, 400.0, 100)
            streamer._on_1min_bar(bar)

        assert cb.call_count == 2

    def test_pending_cleared_after_emit(self):
        streamer, cb = _streamer()

        for minute in range(30, 35):
            bar = _mock_ib_bar(_est(9, minute), 400.0, 401.0, 399.0, 400.0, 100)
            streamer._on_1min_bar(bar)

        assert streamer._pending == []


# ---------------------------------------------------------------------------
# Aggregation correctness
# ---------------------------------------------------------------------------

class TestAggregation:
    def _send_window(self, streamer, opens, highs, lows, closes, volumes, start=30):
        for i, minute in enumerate(range(start, start + 5)):
            bar = _mock_ib_bar(_est(9, minute), opens[i], highs[i], lows[i], closes[i], volumes[i])
            streamer._on_1min_bar(bar)

    def test_open_is_first_bar_open(self):
        streamer, cb = _streamer()
        self._send_window(streamer,
                          opens=[100.0, 101.0, 102.0, 103.0, 104.0],
                          highs=[105.0] * 5, lows=[99.0] * 5, closes=[101.0] * 5,
                          volumes=[100] * 5)
        assert cb.call_args[0][0]["open"] == pytest.approx(100.0)

    def test_high_is_max(self):
        streamer, cb = _streamer()
        self._send_window(streamer,
                          opens=[100.0] * 5,
                          highs=[101.0, 103.0, 102.0, 100.5, 101.5],
                          lows=[99.0] * 5, closes=[100.0] * 5, volumes=[100] * 5)
        assert cb.call_args[0][0]["high"] == pytest.approx(103.0)

    def test_low_is_min(self):
        streamer, cb = _streamer()
        self._send_window(streamer,
                          opens=[100.0] * 5, highs=[101.0] * 5,
                          lows=[98.0, 99.5, 97.0, 99.0, 99.8],
                          closes=[100.0] * 5, volumes=[100] * 5)
        assert cb.call_args[0][0]["low"] == pytest.approx(97.0)

    def test_close_is_last_bar_close(self):
        streamer, cb = _streamer()
        self._send_window(streamer,
                          opens=[100.0] * 5, highs=[101.0] * 5, lows=[99.0] * 5,
                          closes=[100.1, 100.2, 100.3, 100.4, 100.5],
                          volumes=[100] * 5)
        assert cb.call_args[0][0]["close"] == pytest.approx(100.5)

    def test_volume_is_sum(self):
        streamer, cb = _streamer()
        self._send_window(streamer,
                          opens=[100.0] * 5, highs=[101.0] * 5, lows=[99.0] * 5,
                          closes=[100.0] * 5, volumes=[100, 200, 300, 400, 500])
        assert cb.call_args[0][0]["volume"] == 1500

    def test_emitted_bar_is_pandas_series(self):
        streamer, cb = _streamer()
        for minute in range(30, 35):
            bar = _mock_ib_bar(_est(9, minute), 400.0, 401.0, 399.0, 400.0, 100)
            streamer._on_1min_bar(bar)
        assert isinstance(cb.call_args[0][0], pd.Series)

    def test_emitted_bar_name_is_first_bar_timestamp(self):
        streamer, cb = _streamer()
        first_ts = _est(9, 30)
        for minute in range(30, 35):
            bar = _mock_ib_bar(_est(9, minute), 400.0, 401.0, 399.0, 400.0, 100)
            streamer._on_1min_bar(bar)
        emitted = cb.call_args[0][0]
        # Normalize both to UTC before comparing to avoid tz-repr differences
        assert emitted.name.tz_convert("UTC") == first_ts.tz_convert("UTC")


# ---------------------------------------------------------------------------
# Stale-bar reset at window boundary
# ---------------------------------------------------------------------------

class TestStalePendingReset:
    def test_stale_bars_discarded_on_new_window(self):
        """Incomplete :30-:32 bars are discarded when :35 (new window) arrives."""
        streamer, cb = _streamer()

        for minute in (30, 31, 32):
            bar = _mock_ib_bar(_est(9, minute), 400.0, 401.0, 399.0, 400.0, 100)
            streamer._on_1min_bar(bar)

        assert len(streamer._pending) == 3

        bar_35 = _mock_ib_bar(_est(9, 35), 401.0, 402.0, 400.0, 401.5, 200)
        streamer._on_1min_bar(bar_35)

        assert len(streamer._pending) == 1
        cb.assert_not_called()

    def test_no_reset_mid_window(self):
        """Bars mid-window (:36, :37) do not trigger a reset."""
        streamer, cb = _streamer()

        for minute in (35, 36, 37):
            bar = _mock_ib_bar(_est(9, minute), 401.0, 402.0, 400.0, 401.0, 150)
            streamer._on_1min_bar(bar)

        assert len(streamer._pending) == 3
        cb.assert_not_called()


# ---------------------------------------------------------------------------
# Reconnection logic
# ---------------------------------------------------------------------------

def _make_streamer_class():
    import importlib, sys
    ib_mock = MagicMock()
    with patch.dict("sys.modules", {"ib_insync": ib_mock}):
        if "src.live.ibkr_streamer" in sys.modules:
            del sys.modules["src.live.ibkr_streamer"]
        mod = importlib.import_module("src.live.ibkr_streamer")
        return mod.IBKRStreamer, ib_mock


class TestReconnectionLogic:
    def test_reconnects_on_connection_error(self):
        """run() retries after a connection error and propagates KI on second attempt."""
        IBKRStreamer, _ = _make_streamer_class()
        cb = MagicMock()
        streamer = IBKRStreamer(on_bar_close=cb)

        call_count = 0

        def _run_once_side():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("simulated drop")
            raise KeyboardInterrupt

        with patch.object(streamer, "_run_once", side_effect=_run_once_side), \
             patch("src.live.ibkr_streamer.time.sleep"):
            with pytest.raises(KeyboardInterrupt):
                streamer.run()

        assert call_count == 2

    def test_gives_up_after_max_retries(self):
        """run() raises RuntimeError after _MAX_RETRIES consecutive failures."""
        IBKRStreamer, _ = _make_streamer_class()
        cb = MagicMock()
        streamer = IBKRStreamer(on_bar_close=cb)

        with patch.object(streamer, "_run_once", side_effect=ConnectionError("always fails")), \
             patch("src.live.ibkr_streamer.time.sleep"):
            with pytest.raises(RuntimeError, match="failed after"):
                streamer.run()

    def test_keyboard_interrupt_exits_cleanly(self):
        """KeyboardInterrupt propagates after logging, without retrying."""
        IBKRStreamer, _ = _make_streamer_class()
        cb = MagicMock()
        streamer = IBKRStreamer(on_bar_close=cb)

        call_count = 0

        def _raise_keyboard():
            nonlocal call_count
            call_count += 1
            raise KeyboardInterrupt

        with patch.object(streamer, "_run_once", side_effect=_raise_keyboard), \
             patch("src.live.ibkr_streamer.time.sleep"):
            with pytest.raises(KeyboardInterrupt):
                streamer.run()

        assert call_count == 1

    def test_attempt_counter_resets_on_clean_return(self):
        """After a successful _run_once (stale timeout), attempt counter resets
        so that Gateway socket drops don't accumulate towards max retries."""
        IBKRStreamer, _ = _make_streamer_class()
        cb = MagicMock()
        streamer = IBKRStreamer(on_bar_close=cb)

        call_count = 0

        def _run_once_side():
            nonlocal call_count
            call_count += 1
            # First 6 calls return normally (simulating stale-timeout reconnects),
            # then raise KeyboardInterrupt to exit the loop.
            if call_count >= 7:
                raise KeyboardInterrupt

        with patch.object(streamer, "_run_once", side_effect=_run_once_side), \
             patch("src.live.ibkr_streamer.time.sleep"):
            with pytest.raises(KeyboardInterrupt):
                streamer.run()

        # All 7 calls should have been allowed — counter never hit max.
        assert call_count == 7

    def test_retry_waits_use_exponential_backoff(self):
        """time.sleep is called with increasing backoff durations."""
        IBKRStreamer, _ = _make_streamer_class()
        cb = MagicMock()
        streamer = IBKRStreamer(on_bar_close=cb)

        sleep_calls = []
        call_count = 0

        def _fake_sleep(secs):
            sleep_calls.append(secs)

        def _run_once_side():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("drop")
            raise KeyboardInterrupt

        with patch.object(streamer, "_run_once", side_effect=_run_once_side), \
             patch("src.live.ibkr_streamer.time.sleep", side_effect=_fake_sleep):
            with pytest.raises(KeyboardInterrupt):
                streamer.run()

        assert sleep_calls[0] == 5
        assert sleep_calls[1] == 10


# ---------------------------------------------------------------------------
# _parse_bar_ts
# ---------------------------------------------------------------------------

class TestParseBarTs:
    def _get_parse_bar_ts(self):
        import importlib
        with patch.dict("sys.modules", {"ib_insync": MagicMock()}):
            if "src.live.ibkr_streamer" in sys.modules:
                del sys.modules["src.live.ibkr_streamer"]
            mod = importlib.import_module("src.live.ibkr_streamer")
        return mod._parse_bar_ts

    def test_invalid_date_string_raises(self):
        """Unparseable input must raise, not return silently."""
        _parse_bar_ts = self._get_parse_bar_ts()
        with pytest.raises(Exception):
            _parse_bar_ts("not-a-date")

    def test_naive_timestamp_localized_to_est(self):
        """Naive timestamp string is localized to America/New_York."""
        _parse_bar_ts = self._get_parse_bar_ts()
        ts = _parse_bar_ts("2026-01-05 09:30:00")
        assert str(ts.tzinfo) == "America/New_York"

    def test_tz_aware_timestamp_converted_to_est(self):
        """Tz-aware timestamp string is converted to America/New_York."""
        _parse_bar_ts = self._get_parse_bar_ts()
        ts = _parse_bar_ts("2026-01-05 14:30:00+00:00")  # UTC noon = 09:30 EST
        assert str(ts.tzinfo) == "America/New_York"
        assert ts.hour == 9
        assert ts.minute == 30
