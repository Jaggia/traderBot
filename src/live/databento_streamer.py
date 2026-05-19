"""Databento Live WebSocket streamer — aggregates 1-min bars into 5-min bars.

Connects to XNAS.ITCH ohlcv-1m for the configured symbol (default SYMBOL).
Every time a 5-min bar closes (minute % 5 == 4), builds a 5-min OHLCV row
and calls `on_bar_close`.

Price fix-up: Databento Live returns prices as fixed-point int64
(1 unit = 1e-9 dollars), so we divide by 1e9. Timestamps are
nanoseconds since epoch UTC, converted to America/New_York.

Reconnection: run() wraps the stream loop in an exponential-backoff retry
loop (max 5 attempts, waits of 5/10/20/40/60 s).  A configurable stale-
connection timeout (default 120 s) treats silence as a dropped connection
and triggers a reconnect.
"""

import logging
import time

import databento as db
import pandas as pd

from src.constants import STREAMER_RETRY_WAITS, STREAMER_STALE_TIMEOUT_S

logger = logging.getLogger(__name__)


_PRICE_SCALE = 1e9  # Databento fixed-point: divide by 1e9 to get dollars

_MAX_RETRIES = 5
_RETRY_WAITS = STREAMER_RETRY_WAITS
_DEFAULT_STALE_TIMEOUT = STREAMER_STALE_TIMEOUT_S


def _to_ts(ts_event_ns: int) -> pd.Timestamp:
    return pd.Timestamp(ts_event_ns, unit="ns", tz="UTC").tz_convert("America/New_York")


class DatabentoStreamer:
    def __init__(self, api_key: str, on_bar_close, on_1min_bar=None,
                 symbol: str = "SYMBOL",
                 stale_timeout: float = _DEFAULT_STALE_TIMEOUT,
                 eod_cutoff_time: str = "15:55"):
        """
        Parameters
        ----------
        api_key : DATA_BENTO_PW
        on_bar_close : callable(bar: pd.Series)
            Called with a single-row Series (index = timestamp, values = OHLCV)
            each time a complete 5-min bar is assembled.
        on_1min_bar : callable(bar: pd.Series)
            Called with a single-row Series each time a 1-min bar arrives.
        symbol : str
            Ticker symbol to subscribe to (default "SYMBOL").
        stale_timeout : float
            Seconds without receiving any record before the connection is
            considered stale and a reconnect is attempted. Default 120 s.
        eod_cutoff_time : str
            HH:MM cutoff time for bars (default 15:55).
        """
        self._key = api_key
        self._on_bar_close = on_bar_close
        self._on_1min_bar_callback = on_1min_bar
        self._symbol = symbol
        self._stale_timeout = stale_timeout
        self._eod_cutoff_time = eod_cutoff_time
        self._pending: list[dict] = []  # accumulates 1-min bars for current window

    def run(self):
        """Block and stream indefinitely. Reconnects with exponential backoff.

        Reconnection policy:
        - Up to _MAX_RETRIES consecutive failures before giving up.
        - Waits _RETRY_WAITS[attempt] seconds between attempts.
        - A successful connection resets the failure counter.
        - If no record arrives within self._stale_timeout seconds, the
          connection is treated as stale and a reconnect is triggered.
        Ctrl+C / KeyboardInterrupt exits cleanly without retrying.
        """
        attempt = 0
        while True:
            try:
                self._run_once()
                # _run_once returned normally (stale timeout) — treat as a
                # transient failure and retry.
                attempt += 1
            except KeyboardInterrupt:
                logger.info("DatabentoStreamer interrupted by user — stopping")
                return
            except Exception as exc:
                attempt += 1
                logger.warning(
                    "DatabentoStreamer connection error (attempt %d/%d): %s",
                    attempt, _MAX_RETRIES, exc,
                )

            if attempt >= _MAX_RETRIES:
                logger.error(
                    "DatabentoStreamer exceeded %d reconnection attempts — giving up",
                    _MAX_RETRIES,
                )
                raise RuntimeError(
                    f"DatabentoStreamer failed after {_MAX_RETRIES} attempts"
                )

            wait = _RETRY_WAITS[min(attempt - 1, len(_RETRY_WAITS) - 1)]
            logger.warning(
                "DatabentoStreamer reconnecting in %ds (attempt %d/%d) …",
                wait, attempt + 1, _MAX_RETRIES,
            )
            time.sleep(wait)

    def _run_once(self):
        """Connect, subscribe, and iterate records until stale or error.

        Returns normally when a stale-connection timeout is detected so that
        run() can trigger a reconnect.  Raises on unexpected errors.
        """
        client = db.Live(key=self._key)
        client.subscribe(
            dataset="XNAS.ITCH",
            schema=db.Schema.OHLCV_1M,
            symbols=[self._symbol],
            stype_in=db.SType.RAW_SYMBOL,
        )
        logger.info("Connected to Databento Live — XNAS.ITCH ohlcv-1m %s", self._symbol)

        last_received: float = time.monotonic()

        for record in client:
            last_received = time.monotonic()
            if isinstance(record, db.OHLCVMsg):
                self._handle(record)

            # Heartbeat / stale-connection check
            if time.monotonic() - last_received > self._stale_timeout:
                logger.warning(
                    "DatabentoStreamer: no record received for %.0fs — "
                    "treating connection as stale, reconnecting",
                    self._stale_timeout,
                )
                return  # triggers a reconnect in run()

    def _handle(self, record: db.OHLCVMsg):
        ts = _to_ts(record.ts_event)

        # Filter to regular market hours only (09:30–cutoff)
        if ts.hour < 9 or (ts.hour == 9 and ts.minute < 30):
            return
        
        from src.backtest.trade_logic import _is_eod
        if _is_eod(ts.hour, ts.minute, self._eod_cutoff_time) and ts.minute != int(self._eod_cutoff_time.split(":")[1]):
            # If it's after the cutoff minute, skip.
            # E.g. 15:55 bar is the 15:55:00-15:55:59 window.
            if ts.hour > int(self._eod_cutoff_time.split(":")[0]) or ts.minute > int(self._eod_cutoff_time.split(":")[1]):
                return

        bar = {
            "timestamp": ts,
            "open":   record.open  / _PRICE_SCALE,
            "high":   record.high  / _PRICE_SCALE,
            "low":    record.low   / _PRICE_SCALE,
            "close":  record.close / _PRICE_SCALE,
            "volume": record.volume,
        }

        # Prepare 1-min bar Series for callback
        if self._on_1min_bar_callback:
            one_min = pd.Series({
                "open":   bar["open"],
                "high":   bar["high"],
                "low":    bar["low"],
                "close":  bar["close"],
                "volume": bar["volume"],
            }, name=ts)
            self._on_1min_bar_callback(one_min)

        # Reset at window boundary to prevent stale bars from leaking across windows
        if ts.minute % 5 == 0:
            if self._pending:
                logger.warning(
                    "Discarding %d stale bar(s) from incomplete window before %s",
                    len(self._pending), ts.strftime("%H:%M"),
                )
            self._pending = []

        self._pending.append(bar)

        # Emit when this 1-min bar is the last in a 5-min window
        # Windows: :30-:34, :35-:39, ..., :55-:59  → last bar has minute % 5 == 4
        if ts.minute % 5 == 4 and self._pending:
            self._emit()

    def _emit(self):
        bars = self._pending
        self._pending = []

        # Window open timestamp = first bar's timestamp
        open_ts = bars[0]["timestamp"]

        five_min_bar = pd.Series({
            "open":   bars[0]["open"],
            "high":   max(b["high"]   for b in bars),
            "low":    min(b["low"]    for b in bars),
            "close":  bars[-1]["close"],
            "volume": sum(b["volume"] for b in bars),
        }, name=open_ts)

        logger.info(
            "5-min bar closed: %s O=%.2f H=%.2f L=%.2f C=%.2f",
            open_ts.strftime("%H:%M"),
            five_min_bar["open"], five_min_bar["high"],
            five_min_bar["low"], five_min_bar["close"],
        )

        self._on_bar_close(five_min_bar)
