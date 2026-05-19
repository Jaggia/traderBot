"""IBKR live streamer — 1-min bars built from reqMktData tick stream,
aggregated to 5-min, then calls on_bar_close.

Uses reqMktData with marketDataType=3 (delayed) which streams near-real-time
tick data for US equities on paper accounts where reqHistoricalData returns
~16 min delayed bars and reqRealTimeBars is blocked (Error 420).

Ticks are accumulated into 1-min OHLCV bars.  Each completed 1-min bar is
passed to _on_1min_bar() which aggregates to 5-min and emits via callback.

Connects to IB Gateway or TWS (paper account on port 4002 / 7497).
Uses the same reconnection policy as DatabentoStreamer: up to 5 attempts
with exponential backoff (5/10/20/40/60 s), and a stale-connection timeout
that triggers a reconnect if no tick arrives within 120 s.

Requires IB Gateway or TWS to be running and the API socket to be enabled.
"""

import logging
import math
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
from ib_insync import IB, Stock, Ticker

from src.constants import STREAMER_RETRY_WAITS, STREAMER_STALE_TIMEOUT_S

logger = logging.getLogger(__name__)

_MAX_RETRIES = 5
_RETRY_WAITS = STREAMER_RETRY_WAITS
_DEFAULT_STALE_TIMEOUT = STREAMER_STALE_TIMEOUT_S

_EST = ZoneInfo("America/New_York")


def _parse_bar_ts(date_str: str) -> pd.Timestamp:
    """Convert an ib_insync bar date string to a tz-aware EST Timestamp.

    ib_insync returns dates as 'YYYYMMDD  HH:MM:SS' for intraday bars.
    Raises ValueError if the date string cannot be parsed.
    """
    ts = pd.Timestamp(date_str)
    if ts.tzinfo is None:
        ts = ts.tz_localize(_EST)
    else:
        ts = ts.tz_convert(_EST)
    return ts


class IBKRStreamer:
    """Stream 1-min bars from IBKR and emit 5-min bars via callback.

    Uses reqMktData with marketDataType=3 to get near-real-time tick data,
    then builds 1-min OHLCV bars from the tick stream.

    Parameters
    ----------
    on_bar_close : callable(bar: pd.Series)
        Called with a single-row Series (name=timestamp, values=OHLCV)
        each time a complete 5-min bar is assembled.
    symbol : str
        Ticker symbol to stream (default "SYMBOL").
    host : str
        IB Gateway / TWS hostname (default 127.0.0.1).
    port : int
        Socket port: 4002 = IB Gateway paper, 7497 = TWS paper,
        4001 = IB Gateway live, 7496 = TWS live.
    client_id : int
        Must be unique per connection to IB Gateway (use a different ID
        from IBKRTrader to avoid conflicts).
    stale_timeout : float
        Seconds without receiving a tick before reconnecting. Default 120 s.
    eod_cutoff_time : str
        HH:MM cutoff time for bars (default 15:55).
    """

    def __init__(self, on_bar_close, on_1min_bar=None, symbol: str = "SYMBOL",
                 host: str = "127.0.0.1", port: int = 4002,
                 client_id: int = 1, stale_timeout: float = _DEFAULT_STALE_TIMEOUT,
                 eod_cutoff_time: str = "15:55",
                 warmup_end_ts: pd.Timestamp | None = None):
        self._on_bar_close = on_bar_close
        self._on_1min_bar_callback = on_1min_bar
        self._symbol = symbol
        self._host = host
        self._port = port
        self._warmup_end_ts = warmup_end_ts
        self._client_id = client_id
        self._stale_timeout = stale_timeout
        self._eod_cutoff_time = eod_cutoff_time
        self._pending: list[dict] = []  # 1-min bars pending 5-min aggregation
        self._last_bar_time: float = time.monotonic()

    def run(self):
        """Block and stream indefinitely. Reconnects with exponential backoff.

        Ctrl+C / KeyboardInterrupt exits cleanly without retrying.

        The attempt counter only accumulates on consecutive failures.  A
        successful session (even one that ends with a stale-timeout
        reconnect) resets the counter, because the connection was healthy —
        the Gateway just dropped the socket, which is normal for paper
        accounts.
        """
        attempt = 0
        while True:
            try:
                self._run_once()
                # _run_once returned normally (stale timeout or clean exit).
                attempt = 0
            except KeyboardInterrupt:
                logger.info("IBKRStreamer interrupted by user — stopping")
                raise
            except Exception as exc:
                attempt += 1
                logger.warning(
                    "IBKRStreamer connection error (attempt %d/%d): %s",
                    attempt, _MAX_RETRIES, exc,
                )

            if attempt >= _MAX_RETRIES:
                logger.error(
                    "IBKRStreamer exceeded %d reconnection attempts — giving up",
                    _MAX_RETRIES,
                )
                raise RuntimeError(
                    f"IBKRStreamer failed after {_MAX_RETRIES} attempts"
                )

            wait = _RETRY_WAITS[min(attempt - 1, len(_RETRY_WAITS) - 1)]
            logger.warning(
                "IBKRStreamer reconnecting in %ds (attempt %d/%d) …",
                wait, attempt + 1, _MAX_RETRIES,
            )
            time.sleep(wait)

    def _run_once(self):
        """Connect, stream ticks via reqMktData(type=3), build 1-min bars.

        Uses reqMktData with marketDataType=3 (delayed) which provides
        near-real-time tick updates for US equities on paper accounts.
        Ticks are collected into 1-min OHLCV candles.

        Returns normally on a stale-connection timeout so run() can reconnect.
        Raises on unexpected connection errors.
        """
        ib = IB()
        ib.connect(self._host, self._port, clientId=self._client_id)
        ib.reqMarketDataType(3)  # DELAYED — Error 10089 with type=1, subs are TWS-only
        logger.info(
            "IBKRStreamer connected to IB Gateway at %s:%s (clientId=%s, mktDataType=1/LIVE)",
            self._host, self._port, self._client_id,
        )

        contract = Stock(self._symbol, "SMART", "USD")
        ib.qualifyContracts(contract)

        # Subscribe to streaming tick data (non-snapshot, non-regulatory)
        ticker = ib.reqMktData(contract, '', False, False)
        logger.info(
            "IBKRStreamer: subscribed to %s tick stream via reqMktData(type=1/LIVE)",
            self._symbol,
        )

        # 1-min bar accumulator: keyed by minute bucket (HH:MM)
        # Each bucket collects: prices list, for OHLCV construction
        self._current_bucket: str | None = None  # "YYYY-MM-DD HH:MM"
        self._bucket_prices: list[float] = []
        self._bucket_open: float | None = None
        self._bucket_high: float = -math.inf
        self._bucket_low: float = math.inf
        self._bucket_volume_ticks: int = 0  # tick count (volume not reliable)

        self._last_tick_time: float = time.monotonic()
        self._ib = ib  # store ref for cleanup

        # Open tick log file (append mode for reconnect resilience)
        from pathlib import Path
        _today_str = datetime.now(_EST).strftime("%Y-%m-%d")
        _daily_dir = Path("results/live") / _today_str
        _daily_dir.mkdir(parents=True, exist_ok=True)
        _ticks_path = _daily_dir / "live_ticks.csv"
        _needs_header = not _ticks_path.exists() or _ticks_path.stat().st_size == 0
        _tick_file = open(_ticks_path, "a", buffering=1)  # line-buffered
        _tick_count = 0
        if _needs_header:
            _tick_file.write("timestamp,price,bid,ask,last_size,bid_size,ask_size\n")
            _tick_file.flush()
        logger.info("IBKRStreamer: logging ticks to %s", _ticks_path)

        def _on_tick(tickers):
            """Called by ib_insync on each tick update."""
            nonlocal _tick_count
            t = tickers if isinstance(tickers, Ticker) else (
                list(tickers)[0] if hasattr(tickers, '__iter__') else tickers
            )
            price = t.last
            if price != price:  # NaN check
                price = t.marketPrice() if callable(t.marketPrice) else t.marketPrice
            if price != price:  # still NaN
                return

            self._last_tick_time = time.monotonic()
            now_est = datetime.now(_EST)
            bucket = now_est.strftime("%Y-%m-%d %H:%M")

            # Log tick to CSV
            try:
                ts_iso = now_est.strftime("%Y-%m-%d %H:%M:%S.%f")
                def _fmt(val):
                    try:
                        if val != val:  # NaN
                            return ""
                        return str(val)
                    except Exception:
                        return ""
                _tick_file.write(f"{ts_iso},{price},{_fmt(t.bid)},{_fmt(t.ask)},{_fmt(t.lastSize)},{_fmt(t.bidSize)},{_fmt(t.askSize)}\n")
                _tick_count += 1
                if _tick_count % 10 == 0:
                    _tick_file.flush()
                if _tick_count == 1:
                    logger.info("IBKRStreamer: first tick logged: price=%.2f", price)
            except Exception as e:
                logger.warning("IBKRStreamer: tick log write error: %s", e)

            # If we moved to a new minute, finalize the previous bucket
            if self._current_bucket is not None and bucket != self._current_bucket:
                self._finalize_bar(self._current_bucket)

            # Start or continue accumulating into the current bucket
            if self._current_bucket != bucket:
                self._current_bucket = bucket
                self._bucket_prices = []
                self._bucket_open = price
                self._bucket_high = price
                self._bucket_low = price
                self._bucket_volume_ticks = 0

            self._bucket_prices.append(price)
            self._bucket_high = max(self._bucket_high, price)
            self._bucket_low = min(self._bucket_low, price)
            self._bucket_volume_ticks += 1

        ib.pendingTickersEvent += _on_tick

        # Check interval: poll every second for stale timeout
        try:
            while True:
                ib.sleep(1)
                if time.monotonic() - self._last_tick_time > self._stale_timeout:
                    logger.warning(
                        "IBKRStreamer: no tick received for %.0fs — "
                        "treating connection as stale, reconnecting",
                        self._stale_timeout,
                    )
                    # Finalize any pending bar before reconnecting
                    if self._current_bucket is not None and self._bucket_prices:
                        self._finalize_bar(self._current_bucket)
                    _tick_file.flush()
                    _tick_file.close()
                    ib.cancelMktData(contract)
                    ib.disconnect()
                    return  # triggers a reconnect in run()
        except Exception:
            try:
                _tick_file.flush()
                _tick_file.close()
            except Exception:
                pass
            try:
                ib.cancelMktData(contract)
            except Exception:
                pass
            ib.disconnect()
            raise

    def _finalize_bar(self, bucket: str):
        """Build a 1-min bar from accumulated ticks and pass to _on_1min_bar."""
        if not self._bucket_prices:
            return

        close = self._bucket_prices[-1]
        ts = pd.Timestamp(bucket, tz=_EST)

        # Build a bar-like object for _on_1min_bar
        class _Bar:
            pass
        bar = _Bar()
        bar.date = ts.strftime("%Y%m%d  %H:%M:%S")
        bar.open = self._bucket_open
        bar.high = self._bucket_high
        bar.low = self._bucket_low
        bar.close = close
        bar.volume = self._bucket_volume_ticks

        self._on_1min_bar(bar)
        self._bucket_prices = []

    def _on_1min_bar(self, bar):
        """Handle a single 1-min bar from ib_insync."""
        ts = _parse_bar_ts(bar.date)
        now_est = pd.Timestamp.now(tz=_EST)
        lag_s = (now_est - ts).total_seconds()
        logger.info(
            "1-min bar: %s O=%.2f H=%.2f L=%.2f C=%.2f lag=%.0fs",
            ts.strftime("%H:%M"), float(bar.open), float(bar.high),
            float(bar.low), float(bar.close), lag_s,
        )

        # Filter to regular market hours (09:30–cutoff, matching aggregate_1m_to_5m)
        if ts.hour < 9 or (ts.hour == 9 and ts.minute < 30):
            return

        from src.backtest.trade_logic import _is_eod
        if _is_eod(ts.hour, ts.minute, self._eod_cutoff_time) and ts.minute != int(self._eod_cutoff_time.split(":")[1]):
            # If it's after the cutoff minute, skip.
            # We want to include the bar AT the cutoff minute.
            # E.g. 15:55 bar is the 15:55:00-15:55:59 window.
            if ts.hour > int(self._eod_cutoff_time.split(":")[0]) or ts.minute > int(self._eod_cutoff_time.split(":")[1]):
                return

        # Prepare 1-min bar Series for callback
        if self._on_1min_bar_callback:
            one_min = pd.Series({
                "open":   float(bar.open),
                "high":   float(bar.high),
                "low":    float(bar.low),
                "close":  float(bar.close),
                "volume": int(bar.volume),
            }, name=ts)
            self._on_1min_bar_callback(one_min)

        # Reset pending at the start of each 5-min window to discard stale bars
        if ts.minute % 5 == 0:
            if self._pending:
                logger.warning(
                    "IBKRStreamer: discarding %d stale bar(s) from incomplete window before %s",
                    len(self._pending), ts.strftime("%H:%M"),
                )
            self._pending = []

        self._pending.append({
            "timestamp": ts,
            "open":   float(bar.open),
            "high":   float(bar.high),
            "low":    float(bar.low),
            "close":  float(bar.close),
            "volume": int(bar.volume),
        })

        # Emit when this is the last 1-min bar of the 5-min window
        if ts.minute % 5 == 4 and self._pending:
            self._emit()

    def _emit(self):
        bars = self._pending
        self._pending = []
        open_ts = bars[0]["timestamp"]

        five_min = pd.Series({
            "open":   bars[0]["open"],
            "high":   max(b["high"]   for b in bars),
            "low":    min(b["low"]    for b in bars),
            "close":  bars[-1]["close"],
            "volume": sum(b["volume"] for b in bars),
        }, name=open_ts)

        logger.info(
            "5-min bar closed: %s O=%.2f H=%.2f L=%.2f C=%.2f",
            open_ts.strftime("%H:%M"),
            five_min["open"], five_min["high"],
            five_min["low"], five_min["close"],
        )

        self._on_bar_close(five_min)
