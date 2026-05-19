"""
Tests for src/live/databento_streamer.py.

Mocks databento.Live so no real WebSocket connection is made.
Covers:
  - Price scale:  Databento fixed-point int64 values are divided by 1e9 to get dollars
  - Timestamp:    nanosecond UTC timestamps converted to America/New_York
  - Market hours filtering: bars outside 09:30-15:59 EST are dropped
  - Bar accumulation: multiple 1-min bars accumulate before 5-min window closes
  - Window boundary: callback invoked exactly once when minute % 5 == 4
  - Callback NOT invoked mid-window (minute % 5 != 4)
  - Aggregation: open=first, high=max, low=min, close=last, volume=sum
  - C-3 reconnection: exponential backoff retries on connection error
  - C-3 reconnection: stale-connection timeout triggers a reconnect
  - C-3 reconnection: KeyboardInterrupt exits cleanly without retrying
  - C-3 reconnection: gives up after _MAX_RETRIES consecutive failures
"""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PRICE_SCALE = 1e9  # matches databento_streamer._PRICE_SCALE


def _ns(ts: pd.Timestamp) -> int:
    """Convert a Timestamp to nanoseconds since epoch (as used by Databento)."""
    return int(ts.value)


def _mock_record(ts: pd.Timestamp, open_: float, high: float, low: float,
                 close: float, volume: int) -> MagicMock:
    """Create a mock OHLCVMsg with fixed-point prices and nanosecond timestamp."""
    rec = MagicMock()
    rec.ts_event = _ns(ts)
    rec.open   = int(open_  * _PRICE_SCALE)
    rec.high   = int(high   * _PRICE_SCALE)
    rec.low    = int(low    * _PRICE_SCALE)
    rec.close  = int(close  * _PRICE_SCALE)
    rec.volume = volume
    return rec


def _est(hour: int, minute: int, date: str = "2026-01-05") -> pd.Timestamp:
    """Return a timezone-aware EST timestamp (America/New_York)."""
    return pd.Timestamp(f"{date} {hour:02d}:{minute:02d}:00", tz="America/New_York")


def _streamer(callback=None):
    """Build a DatabentoStreamer with the real __init__ (no live connection needed)."""
    # Patch databento at the module level so the import succeeds
    with patch.dict("sys.modules", {"databento": MagicMock()}):
        from src.live.databento_streamer import DatabentoStreamer
        cb = callback or MagicMock()
        streamer = DatabentoStreamer(api_key="dummy", on_bar_close=cb)
        return streamer, cb


# ---------------------------------------------------------------------------
# Price scale
# ---------------------------------------------------------------------------

class TestPriceScale:
    def test_prices_divided_by_1e9(self):
        """Fixed-point int64 values must be divided by 1e9 to recover dollar prices."""
        streamer, cb = _streamer()

        # Build a record at minute :34 (will trigger a 5-min emit)
        ts = _est(9, 34)
        rec = _mock_record(ts, open_=400.0, high=401.0, low=399.0, close=400.5, volume=1000)

        # Inject 5 bars so the window is fully accumulated (:30-:34)
        for minute in range(30, 35):
            t = _est(9, minute)
            r = _mock_record(t, open_=400.0, high=401.0, low=399.0, close=400.5, volume=1000)
            streamer._handle(r)

        assert cb.call_count == 1
        emitted = cb.call_args[0][0]
        assert emitted["open"]  == pytest.approx(400.0)
        assert emitted["high"]  == pytest.approx(401.0)
        assert emitted["low"]   == pytest.approx(399.0)
        assert emitted["close"] == pytest.approx(400.5)


# ---------------------------------------------------------------------------
# Market hours filtering
# ---------------------------------------------------------------------------

class TestMarketHoursFiltering:
    def test_pre_market_bar_ignored(self):
        """Bar at 09:25 (before market open) must not accumulate or trigger callback."""
        streamer, cb = _streamer()

        ts = _est(9, 25)
        rec = _mock_record(ts, 400.0, 401.0, 399.0, 400.0, 100)
        streamer._handle(rec)

        assert len(streamer._pending) == 0
        cb.assert_not_called()

    def test_exactly_930_bar_accepted(self):
        """Bar at exactly 09:30 must be accepted."""
        streamer, cb = _streamer()

        ts = _est(9, 30)
        rec = _mock_record(ts, 400.0, 401.0, 399.0, 400.0, 100)
        streamer._handle(rec)

        assert len(streamer._pending) == 1

    def test_after_market_hours_bar_ignored(self):
        """Bar at 16:00 (after close) must not accumulate or trigger callback."""
        streamer, cb = _streamer()

        ts = _est(16, 0)
        rec = _mock_record(ts, 400.0, 401.0, 399.0, 400.0, 100)
        streamer._handle(rec)

        assert len(streamer._pending) == 0
        cb.assert_not_called()

    def test_bar_at_1500_accepted(self):
        """Bar at 15:00 must be accepted (market still open)."""
        streamer, cb = _streamer()

        ts = _est(15, 0)
        rec = _mock_record(ts, 400.0, 401.0, 399.0, 400.0, 100)
        streamer._handle(rec)

        assert len(streamer._pending) == 1


# ---------------------------------------------------------------------------
# Bar accumulation (mid-window, no callback)
# ---------------------------------------------------------------------------

class TestBarAccumulation:
    def test_bars_accumulate_without_callback_mid_window(self):
        """Bars at minutes :30, :31, :32, :33 accumulate; callback NOT called yet."""
        streamer, cb = _streamer()

        for minute in range(30, 34):  # :30, :31, :32, :33 — not :34
            ts = _est(9, minute)
            rec = _mock_record(ts, 400.0, 401.0, 399.0, 400.0, 100)
            streamer._handle(rec)

        assert len(streamer._pending) == 4
        cb.assert_not_called()

    def test_second_window_accumulated_after_first_emits(self):
        """After first 5-min window emits, pending list resets and accumulates again."""
        streamer, cb = _streamer()

        # First window: :30-:34
        for minute in range(30, 35):
            ts = _est(9, minute)
            rec = _mock_record(ts, 400.0, 401.0, 399.0, 400.0, 100)
            streamer._handle(rec)

        assert cb.call_count == 1
        assert len(streamer._pending) == 0

        # Start of second window: :35
        ts = _est(9, 35)
        rec = _mock_record(ts, 401.0, 402.0, 400.0, 401.0, 200)
        streamer._handle(rec)

        assert len(streamer._pending) == 1
        assert cb.call_count == 1  # still only one emission


# ---------------------------------------------------------------------------
# Window boundary (callback invoked on minute % 5 == 4)
# ---------------------------------------------------------------------------

class TestWindowBoundary:
    def test_callback_invoked_when_minute_mod5_is_4(self):
        """Callback triggered when bar minute is 34 (:30-:34 window closes)."""
        streamer, cb = _streamer()

        for minute in range(30, 35):
            ts = _est(9, minute)
            rec = _mock_record(ts, 400.0, 401.0, 399.0, 400.0, 100)
            streamer._handle(rec)

        cb.assert_called_once()

    def test_callback_not_invoked_at_minute_mod5_not_4(self):
        """Bars at :35-:38 (not :39) must not trigger the callback."""
        streamer, cb = _streamer()

        for minute in range(35, 39):  # :35, :36, :37, :38 — NOT :39
            ts = _est(9, minute)
            rec = _mock_record(ts, 401.0, 402.0, 400.5, 401.5, 200)
            streamer._handle(rec)

        cb.assert_not_called()

    def test_callback_invoked_for_each_5min_window(self):
        """Two complete 5-min windows should invoke the callback exactly twice."""
        streamer, cb = _streamer()

        # First window: :30-:34
        for minute in range(30, 35):
            ts = _est(9, minute)
            rec = _mock_record(ts, 400.0, 401.0, 399.0, 400.0, 100)
            streamer._handle(rec)

        # Second window: :35-:39
        for minute in range(35, 40):
            ts = _est(9, minute)
            rec = _mock_record(ts, 401.0, 402.0, 400.0, 401.5, 150)
            streamer._handle(rec)

        assert cb.call_count == 2

    def test_pending_cleared_after_emit(self):
        """After the callback fires, _pending is reset to an empty list."""
        streamer, cb = _streamer()

        for minute in range(30, 35):
            ts = _est(9, minute)
            rec = _mock_record(ts, 400.0, 401.0, 399.0, 400.0, 100)
            streamer._handle(rec)

        assert streamer._pending == []


# ---------------------------------------------------------------------------
# Aggregation correctness
# ---------------------------------------------------------------------------

class TestAggregation:
    def _send_window(self, streamer, opens, highs, lows, closes, volumes, start_minute=30):
        for i, minute in enumerate(range(start_minute, start_minute + 5)):
            ts = _est(9, minute)
            rec = _mock_record(ts, opens[i], highs[i], lows[i], closes[i], volumes[i])
            streamer._handle(rec)

    def test_open_is_first_bar_open(self):
        streamer, cb = _streamer()
        self._send_window(
            streamer,
            opens=[100.0, 101.0, 102.0, 103.0, 104.0],
            highs=[101.0, 102.0, 103.0, 104.0, 105.0],
            lows=[99.0, 100.0, 101.0, 102.0, 103.0],
            closes=[101.0, 102.0, 103.0, 104.0, 105.0],
            volumes=[100, 200, 300, 400, 500],
        )
        bar = cb.call_args[0][0]
        assert bar["open"] == pytest.approx(100.0)

    def test_high_is_max_of_all_bars(self):
        streamer, cb = _streamer()
        self._send_window(
            streamer,
            opens=[100.0] * 5,
            highs=[101.0, 103.0, 102.0, 100.5, 101.5],
            lows=[99.0] * 5,
            closes=[100.0] * 5,
            volumes=[100] * 5,
        )
        bar = cb.call_args[0][0]
        assert bar["high"] == pytest.approx(103.0)

    def test_low_is_min_of_all_bars(self):
        streamer, cb = _streamer()
        self._send_window(
            streamer,
            opens=[100.0] * 5,
            highs=[101.0] * 5,
            lows=[98.0, 99.5, 97.0, 99.0, 99.8],
            closes=[100.0] * 5,
            volumes=[100] * 5,
        )
        bar = cb.call_args[0][0]
        assert bar["low"] == pytest.approx(97.0)

    def test_close_is_last_bar_close(self):
        streamer, cb = _streamer()
        self._send_window(
            streamer,
            opens=[100.0] * 5,
            highs=[101.0] * 5,
            lows=[99.0] * 5,
            closes=[100.1, 100.2, 100.3, 100.4, 100.5],
            volumes=[100] * 5,
        )
        bar = cb.call_args[0][0]
        assert bar["close"] == pytest.approx(100.5)

    def test_volume_is_sum_of_all_bars(self):
        streamer, cb = _streamer()
        self._send_window(
            streamer,
            opens=[100.0] * 5,
            highs=[101.0] * 5,
            lows=[99.0] * 5,
            closes=[100.0] * 5,
            volumes=[100, 200, 300, 400, 500],
        )
        bar = cb.call_args[0][0]
        assert bar["volume"] == 1500

    def test_emitted_bar_is_pandas_series(self):
        """The emitted 5-min bar must be a pd.Series."""
        streamer, cb = _streamer()
        for minute in range(30, 35):
            ts = _est(9, minute)
            rec = _mock_record(ts, 400.0, 401.0, 399.0, 400.0, 100)
            streamer._handle(rec)

        bar = cb.call_args[0][0]
        assert isinstance(bar, pd.Series)

    def test_emitted_bar_name_is_first_bar_timestamp(self):
        """The Series name (index label) should be the first bar's timestamp."""
        streamer, cb = _streamer()
        first_ts = _est(9, 30)
        for minute in range(30, 35):
            ts = _est(9, minute)
            rec = _mock_record(ts, 400.0, 401.0, 399.0, 400.0, 100)
            streamer._handle(rec)

        bar = cb.call_args[0][0]
        assert bar.name == first_ts


# ---------------------------------------------------------------------------
# C-3: Reconnection / exponential backoff / stale timeout
# ---------------------------------------------------------------------------

def _make_streamer_class():
    """Return a real DatabentoStreamer class with databento mocked at import time."""
    import importlib
    import sys
    db_mock = MagicMock()
    with patch.dict("sys.modules", {"databento": db_mock}):
        # Force re-import so the module-level `import databento as db` picks up the mock
        if "src.live.databento_streamer" in sys.modules:
            del sys.modules["src.live.databento_streamer"]
        mod = importlib.import_module("src.live.databento_streamer")
        return mod.DatabentoStreamer, db_mock


class TestReconnectionLogic:
    def test_reconnects_on_connection_error(self):
        """run() should retry after a connection error and succeed on second attempt."""
        DatabentoStreamer, db_mock = _make_streamer_class()
        cb = MagicMock()
        streamer = DatabentoStreamer(api_key="dummy", on_bar_close=cb, stale_timeout=120)

        call_count = 0

        def _run_once_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("simulated drop")
            # Second call: succeed immediately (no records → stale timeout path,
            # but we raise KeyboardInterrupt to stop the outer loop)
            raise KeyboardInterrupt

        with patch.object(streamer, "_run_once", side_effect=_run_once_side_effect), \
             patch("src.live.databento_streamer.time.sleep"):
            streamer.run()

        assert call_count == 2

    def test_gives_up_after_max_retries(self):
        """run() should raise RuntimeError after _MAX_RETRIES consecutive failures."""
        DatabentoStreamer, db_mock = _make_streamer_class()
        cb = MagicMock()
        streamer = DatabentoStreamer(api_key="dummy", on_bar_close=cb, stale_timeout=120)

        with patch.object(streamer, "_run_once", side_effect=ConnectionError("always fails")), \
             patch("src.live.databento_streamer.time.sleep"):
            with pytest.raises(RuntimeError, match="failed after"):
                streamer.run()

    def test_keyboard_interrupt_exits_cleanly(self):
        """KeyboardInterrupt during streaming must stop without retrying."""
        DatabentoStreamer, db_mock = _make_streamer_class()
        cb = MagicMock()
        streamer = DatabentoStreamer(api_key="dummy", on_bar_close=cb, stale_timeout=120)

        call_count = 0

        def _run_once_raises_keyboard():
            nonlocal call_count
            call_count += 1
            raise KeyboardInterrupt

        with patch.object(streamer, "_run_once", side_effect=_run_once_raises_keyboard), \
             patch("src.live.databento_streamer.time.sleep"):
            # Should return normally, NOT re-raise and NOT retry
            streamer.run()

        assert call_count == 1

    def test_stale_timeout_triggers_reconnect(self):
        """_run_once should return (triggering reconnect) when stale timeout exceeded."""
        import time as _time
        DatabentoStreamer, db_mock = _make_streamer_class()
        cb = MagicMock()
        streamer = DatabentoStreamer(api_key="dummy", on_bar_close=cb, stale_timeout=1)

        # _run_once iterates over client records.  Simulate one record arriving,
        # then the next monotonic() call reports enough time has passed.
        live_client = MagicMock()
        db_mock.Live.return_value = live_client

        # Produce one non-OHLCVMsg record (so isinstance check fails) so we get
        # into the body of the for-loop, then end the iterator.
        non_ohlcv = MagicMock(spec=[])  # no OHLCVMsg attribute
        db_mock.OHLCVMsg = type("OHLCVMsg", (), {})  # distinct class so isinstance fails
        live_client.__iter__ = MagicMock(return_value=iter([non_ohlcv]))

        # time.monotonic() is called three times in _run_once():
        #   1. last_received = time.monotonic()  (before loop)
        #   2. last_received = time.monotonic()  (inside loop, on each record)
        #   3. time.monotonic() - last_received  (stale check, same iteration)
        # Values: init=t0, record-update=t0 (same moment), stale-check=t0+200 (triggers return)
        t0 = 1000.0
        with patch("src.live.databento_streamer.time.monotonic",
                   side_effect=[t0, t0, t0 + 200]):
            # _run_once should return normally (stale path), not raise
            streamer._run_once()  # must not raise

    def test_retry_waits_use_exponential_backoff(self):
        """Verify time.sleep is called with increasing wait durations."""
        DatabentoStreamer, db_mock = _make_streamer_class()
        cb = MagicMock()
        streamer = DatabentoStreamer(api_key="dummy", on_bar_close=cb, stale_timeout=120)

        sleep_calls = []

        def _fake_sleep(secs):
            sleep_calls.append(secs)

        call_count = 0

        def _run_once_side():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("drop")
            raise KeyboardInterrupt  # stop after 3 attempts

        with patch.object(streamer, "_run_once", side_effect=_run_once_side), \
             patch("src.live.databento_streamer.time.sleep", side_effect=_fake_sleep):
            streamer.run()

        # First sleep = 5s, second sleep = 10s (exponential backoff)
        assert sleep_calls[0] == 5
        assert sleep_calls[1] == 10


# ---------------------------------------------------------------------------
# L-11: Stale pending reset at window boundary
# ---------------------------------------------------------------------------

class TestStalePendingReset:
    def test_stale_bars_discarded_on_new_window(self):
        """Incomplete window bars (:30-:32) are discarded when next window (:35) starts.

        Scenario: bars at :30, :31, :32 arrive (incomplete window), then :33
        and :34 are dropped (e.g. network gap), then :35 arrives (start of the
        next 5-min window).  The 3 stale bars must be discarded; _pending must
        contain only the :35 bar, and the callback must never have fired.
        """
        streamer, cb = _streamer()

        # Send 3 bars into the :30-:34 window (incomplete — no :33 or :34)
        for minute in (30, 31, 32):
            ts = _est(9, minute)
            rec = _mock_record(ts, 400.0, 401.0, 399.0, 400.0, 100)
            streamer._handle(rec)

        assert len(streamer._pending) == 3
        cb.assert_not_called()

        # :35 is the start of the next window (35 % 5 == 0) — stale bars must be reset
        ts_35 = _est(9, 35)
        rec_35 = _mock_record(ts_35, 401.0, 402.0, 400.0, 401.5, 200)
        streamer._handle(rec_35)

        # Only the :35 bar should remain; the 3 stale bars from :30-:32 are gone
        assert len(streamer._pending) == 1
        assert streamer._pending[0]["timestamp"] == ts_35

        # The first window never completed, so the callback was never called
        cb.assert_not_called()

    def test_no_reset_mid_window(self):
        """Bars arriving mid-window (minute % 5 != 0) do NOT trigger a reset.

        Send bars at :35 (window start — resets to 1), :36, :37.  After those
        three bars _pending should contain exactly 3 bars with no reset in
        between :36 and :37.
        """
        streamer, cb = _streamer()

        for minute in (35, 36, 37):
            ts = _est(9, minute)
            rec = _mock_record(ts, 401.0, 402.0, 400.0, 401.0, 150)
            streamer._handle(rec)

        # :35 reset to [], then appended → 1; :36 appended → 2; :37 appended → 3
        assert len(streamer._pending) == 3
        cb.assert_not_called()
