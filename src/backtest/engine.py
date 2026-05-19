import logging
import os
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from src.signals.strategy import SignalStrategy, create_strategy
from src.backtest.portfolio import Portfolio
from src.backtest.trade_logic import check_exit, build_entry, BarContext, ExitConfig, _is_eod
from src.data.databento_loader import DatabentoOptionsLoader
from src.utils.time_utils import get_market_hours_window

logger = logging.getLogger(__name__)


def _align_ts(ts: pd.Timestamp, index_tz) -> pd.Timestamp:
    """Align a timestamp's timezone to match the data index."""
    if ts.tz is None and index_tz is not None:
        return ts.tz_localize(index_tz)
    if ts.tz is not None and index_tz is not None:
        return ts.tz_convert(index_tz)
    return ts


class BacktestEngine:
    """Main backtesting loop supporting equities or options."""

    def __init__(self, config: dict, equity_data: pd.DataFrame, initial_cash: float = None,
                 trade_start=None, oos_start=None, strategy: SignalStrategy = None):
        from src.utils.config_utils import validate_config
        validate_config(config)
            
        self.config = config
        self.trade_mode = config["strategy"]["trade_mode"]
        if self.trade_mode not in ("equities", "options"):
            raise ValueError(
                f"trade_mode must be 'equities' or 'options', got {self.trade_mode!r}. "
                "'both' mode has been removed — run two separate backtests instead."
            )
        if initial_cash is None:
            initial_cash = config.get("strategy", {}).get("initial_capital", 100_000)
        self.portfolio = Portfolio(initial_cash=initial_cash, config=config)

        # Strategy pattern: caller can inject, otherwise create from config
        self._strategy = strategy if strategy is not None else create_strategy(config)

        # Pre-compute indicators and signals
        self.data = self._strategy.compute_indicators(equity_data, config)
        self.data["signal"] = self._strategy.generate_signals(self.data, config)

        # Cache config values used in hot loop
        self._exits_cfg = config["exits"]
        self._pos_cfg = config.get("position", {})
        self._sizing_mode = self._pos_cfg.get("sizing_mode", "fixed")
        self._sizing_pct = self._pos_cfg.get("sizing_pct", 50) / 100.0
        self._fixed_contracts = self._pos_cfg.get("contracts_per_trade", 1)
        self._profit_target = self._exits_cfg["profit_target_pct"]
        self._stop_loss = self._exits_cfg["stop_loss_pct"]
        self._eod_close = self._exits_cfg.get("eod_close", True)
        self._opposite_signal = self._exits_cfg.get("opposite_signal", True)
        self._eod_cutoff_time = config.get("backtest", {}).get("eod_cutoff_time", "15:55")

        # Trade start: bars before this timestamp are warm-up only (no trading)
        self.trade_start = pd.Timestamp(trade_start) if trade_start is not None else None

        # OOS start: bars before this (but after trade_start) are in-sample
        self.oos_start = pd.Timestamp(oos_start) if oos_start is not None else None
        # Resolved bar index; set during run() so callers can slice trade log
        self.oos_start_idx: int = 0

        # Exit config for trade_logic (frozen, built once)
        self._exit_config = ExitConfig(
            profit_target_pct=self._profit_target,
            stop_loss_pct=self._stop_loss,
            eod_close=self._eod_close,
            opposite_signal=self._opposite_signal,
            eod_cutoff_time=self._eod_cutoff_time,
            zero_dte_safeguard=config.get("exits", {}).get("zero_dte_safeguard", True),
            zero_dte_cutoff_time=config.get("exits", {}).get("zero_dte_cutoff_time", "15:55"),
        )

        # PriceFn adapter: reorder _get_option_price args into trade_logic's 6-arg form
        # Accepts optional sigma kwarg so per-position entry_iv can be threaded through
        self._price_fn = lambda sym, und, k, ot, dte, bt, **kw: self._get_option_price(
            und, k, dte, ot, sym, bt, **kw
        )

        # Options data loader (lazy init — only if needed)
        self._options_loader: Optional[DatabentoOptionsLoader] = None

        # Per-day cache for option bars to avoid N+1 fetches.
        # Key: (raw_symbol, date_str) -> DataFrame of that day's 1-min bars.
        # Evicted when the trading date changes so memory stays bounded to
        # at most N-contracts × 1-day of 1-min bars.
        self._option_bar_cache: dict = {}
        self._option_cache_date = None

    @property
    def options_loader(self) -> Optional[DatabentoOptionsLoader]:
        if self._options_loader is None and self.trade_mode == "options":
            api_key = os.getenv("DATA_BENTO_PW") or os.getenv("DATABENTO_API_KEY")
            if not api_key:
                raise ValueError(
                    "Missing Databento API key. Please set DATA_BENTO_PW or DATABENTO_API_KEY "
                    "environment variable. Silent fallback to cache-only mode is disabled "
                    "to prevent missing data errors."
                )
            logger.debug("Databento options loader initialized with API access")
            opts_dir = self.config.get("data", {}).get("options_dir", "data/options/SYMBOL/1min")
            self._options_loader = DatabentoOptionsLoader(api_key=api_key, cache_dir=opts_dir)
        return self._options_loader

    def _get_option_price(
        self, underlying_price: float, strike: float, dte_years: float,
        option_type: str, raw_symbol: Optional[str], bar_time: datetime,
        sigma: Optional[float] = None, field: str = "close",
    ) -> float:
        """Get option price from Databento market data.

        Backtests must use observed option prices only. Missing or unusable
        market data is a hard failure rather than a model-based fallback.

        Parameters
        ----------
        field : str
            "close" — close of the most recent 1-min bar at bar_time (default).
            "low"   — min(low) across 1-min bars in the 5-min window [T, T+4].
            "high"  — max(high) across 1-min bars in the 5-min window [T, T+4].
        """
        if not raw_symbol:
            raise RuntimeError(
                f"Option price unavailable: no raw_symbol for strike={strike}, type={option_type}"
            )
        if not self.options_loader:
            raise RuntimeError(
                f"Option price unavailable: no options loader for {raw_symbol}"
            )

        day_start, day_end = get_market_hours_window(bar_time)
        logger.debug("Fetching %s | window: %s → %s", raw_symbol, day_start, day_end)

        # Check per-day cache to avoid N+1 fetches
        cache_key = (raw_symbol, day_start.date())
        current_date = day_start.date()
        if current_date != self._option_cache_date:
            # New trading day — evict stale entries to bound memory
            self._option_bar_cache.clear()
            self._option_cache_date = current_date
        if cache_key in self._option_bar_cache:
            df = self._option_bar_cache[cache_key]
        else:
            df = self.options_loader.load_option_bars(
                raw_symbol, start=day_start, end=day_end
            )
            if df.empty:
                raise RuntimeError(
                    f"Option price unavailable: no market data returned for {raw_symbol} "
                    f"between {day_start} and {day_end}"
                )
            self._option_bar_cache[cache_key] = df

        bar_ts = pd.Timestamp(bar_time)

        if field in ("high", "low"):
            # Intrabar extreme: scan 1-min bars in the 5-min window [T, T+4min]
            # (equity bars use label="left", closed="left")
            window_end = bar_ts + pd.Timedelta(minutes=4)
            window = df.loc[(df.index >= bar_ts) & (df.index <= window_end)]
            if window.empty:
                # Fall back to nearest available bar
                idx = df.index.searchsorted(bar_ts, side="right") - 1
                if idx < 0:
                    raise RuntimeError(
                        f"Option price unavailable: no bar at or before {bar_ts} for {raw_symbol}"
                    )
                price = float(df.iloc[idx][field])
            else:
                price = float(window[field].min() if field == "low" else window[field].max())
            logger.debug("Option %s=%.4f over [%s, %s] for %s", field, price, bar_ts, window_end, raw_symbol)
            return price

        # Default: close of the most recent 1-min bar
        last_known_idx = df.index.searchsorted(bar_ts, side="right") - 1
        if last_known_idx < 0:
            raise RuntimeError(
                f"Option price unavailable: no historical bar at or before {bar_ts} for {raw_symbol}"
            )

        found_ts = df.index[last_known_idx]
        staleness = bar_ts - found_ts
        # Staleness threshold: configurable via config["data"]["max_option_staleness_minutes"],
        # defaults to 25 minutes (5 bars × 5 min).
        max_stale_min = (
            self.config.get("data", {}).get("max_option_staleness_minutes", 25)
        )
        if staleness > pd.Timedelta(minutes=max_stale_min):
            logger.warning(
                "Stale option price for %s: source bar %s is %s behind requested %s "
                "(threshold=%d min) — skipping mark-to-market",
                raw_symbol, found_ts, staleness, bar_ts, max_stale_min,
            )
            return None

        price = df.iloc[last_known_idx]["close"]
        logger.debug("Market price=%.4f at %s (source bar %s)", price, bar_ts, found_ts)
        return price

    def _compute_shares(self, price: float) -> float:
        """Calculate number of contracts or shares based on sizing_mode.

        Returns float for equities (fractional shares) and int for options.
        """
        if self._sizing_mode == "equity_pct":
            if self.trade_mode == "options":
                raise ValueError("equity_pct sizing mode cannot be used with options trade mode")
            if price <= 0:
                logger.warning("Cannot size position: price is %s", price)
                return 0
            equity = self.portfolio.get_equity()
            return (equity * self._sizing_pct) / price

        # fixed_contracts mode
        val = self._fixed_contracts
        if self.trade_mode == "options":
            return int(val)
        return float(val)

    def run(self) -> Portfolio:
        """Execute the backtest over all bars.

        Uses pre-extracted numpy arrays for speed instead of iterrows().
        """
        if self.trade_mode == "options":
            from src.options.entry_logic import clear_iv_cache
            clear_iv_cache()

        data = self.data
        total_bars = len(data)
        logger.info("Running backtest: %d bars, mode=%s", total_bars, self.trade_mode)

        # Pre-extract arrays — avoids per-row pandas overhead
        timestamps = data.index.to_numpy()
        opens = data["open"].to_numpy(dtype=np.float64)
        closes = data["close"].to_numpy(dtype=np.float64)
        highs = data["high"].to_numpy(dtype=np.float64)
        lows = data["low"].to_numpy(dtype=np.float64)
        signals = data["signal"].to_numpy(dtype=np.int64)
        hours = data.index.hour.to_numpy()
        minutes = data.index.minute.to_numpy()

        portfolio = self.portfolio
        is_options = self.trade_mode == "options"
        is_equities = self.trade_mode == "equities"

        # Find the index where trading starts (avoids tz comparison issues in hot loop)
        trade_start_idx = 0
        if self.trade_start is not None:
            trade_start_ts = _align_ts(pd.Timestamp(self.trade_start), data.index.tz)
            trade_start_idx = data.index.searchsorted(trade_start_ts)

        if trade_start_idx >= total_bars:
            logger.warning(
                "trade_start=%s is beyond the data range (last bar=%s) — backtest will produce no trades.",
                self.trade_start,
                data.index[-1] if total_bars > 0 else "N/A",
            )

        # Find the index where OOS starts (used by runner to split IS vs OOS trade log)
        oos_start_idx = trade_start_idx  # default: all trading bars are OOS (no IS split)
        if self.oos_start is not None:
            oos_start_ts = _align_ts(pd.Timestamp(self.oos_start), data.index.tz)
            oos_start_idx = data.index.searchsorted(oos_start_ts)
        self.oos_start_idx = oos_start_idx

        price_fn = self._price_fn
        exit_config = self._exit_config

        # Pending entry: signal fires on bar[i], entry fills at bar[i+1].open (industry standard)
        # Tuple: (signal: int, signal_bar: BarContext, entry_price_hint: float | None, age: int)
        pending_entry = None

        # Check if entry_price_hint column exists (System 2 EMA pipeline writes it)
        has_hint = "entry_price_hint" in data.columns
        hints = data["entry_price_hint"].to_numpy(dtype=np.float64) if has_hint else None

        # Record initial equity as t=0 baseline before the first bar
        if total_bars > 0:
            portfolio.record_initial_equity(timestamps[trade_start_idx] if trade_start_idx < total_bars else timestamps[0])

        for i in range(total_bars):
            bar = BarContext(timestamps[i], opens[i], closes[i], highs[i], lows[i],
                             int(signals[i]), int(hours[i]), int(minutes[i]))

            # Skip trading during warm-up period (indicators computed but no trades)
            if i < trade_start_idx:
                pending_entry = None  # discard any signal from warm-up boundary
                continue

            # --- 0. Execute pending entry at this bar's open ---
            if pending_entry is not None:
                pend_signal, pend_bar, entry_price_hint, age = pending_entry
                
                # C-3: Expire pending entry if it's too old (limit persistence when can_open() is false)
                # Next-bar-open is age 0. If it carries over, it becomes age 1.
                # We expire it after age 1 (max 2 fill attempts).
                if age > 1:
                    logger.debug("Pending entry expired at %s (age=%d)", bar.timestamp, age)
                    pending_entry = None
                else:
                    _fill_at_eod = exit_config.eod_close and _is_eod(bar.hour, bar.minute, exit_config.eod_cutoff_time)
                    
                    if _fill_at_eod:
                        pending_entry = None
                    elif portfolio.can_open():
                        if entry_price_hint is not None and (entry_price_hint < bar.low or entry_price_hint > bar.high):
                            # Hint outside bar range — replace with open price so the entry retries
                            logger.debug(
                                "Pending entry hint %.4f outside bar range %.4f-%.4f at %s — falling back to open",
                                entry_price_hint, bar.low, bar.high, bar.timestamp,
                            )
                            entry_price_hint = opens[i]

                        pending_entry = None  # consume since we will try to open
                        fill_price = entry_price_hint if entry_price_hint is not None else opens[i]
                        entry_bar = BarContext(
                            bar.timestamp, fill_price, fill_price, bar.high, bar.low,
                            pend_signal, bar.hour, bar.minute,
                        )
                        contracts = self._compute_shares(fill_price)
                        if contracts == 0:
                            logger.warning(
                                "Skipping pending entry at %s: computed 0 contracts.",
                                bar.timestamp,
                            )
                        else:
                            if is_equities:
                                pos = build_entry(pend_signal, entry_bar, contracts, "equities",
                                                  self.config, exit_config)
                            else:
                                pos = build_entry(pend_signal, entry_bar, contracts, "options",
                                                  self.config, exit_config, price_fn)
                            if pos:
                                try:
                                    portfolio.open_position(pos)
                                except ValueError as exc:
                                    logger.warning(
                                        "Skipping pending entry at %s: %s",
                                        bar.timestamp, exc,
                                    )
                    else:
                        # Portfolio full: increment age and try again next bar
                        pending_entry = (pend_signal, pend_bar, entry_price_hint, age + 1)

            # --- 1. Check exits on open positions ---
            eod_closed_this_bar = False
            for pos in list(portfolio.positions):
                result = check_exit(pos, bar, exit_config, get_option_price=price_fn)
                if result:
                    portfolio.close_position(pos, result.fill_price, bar.timestamp, result.reason)
                    if result.reason == "eod_close":
                        eod_closed_this_bar = True

            # --- 2. Try new entries ---
            # Block entries at/after eod_cutoff_time when eod_close is enabled: even if no
            # position was open (so eod_closed_this_bar is False), a new position
            # opened at/after the cutoff would never be EOD-closed, violating the rule.
            is_eod_bar = exit_config.eod_close and _is_eod(bar.hour, bar.minute, exit_config.eod_cutoff_time)
            if bar.signal != 0 and not eod_closed_this_bar and not is_eod_bar:
                # Buffer signal — entry fills at next bar's open (industry standard: next-bar-open)
                hint_price = None
                if has_hint:
                    raw = hints[i]
                    if not np.isnan(raw):
                        hint_price = float(raw)
                pending_entry = (bar.signal, bar, hint_price, 0)

            # --- 3. Mark to market (every bar for accurate Sharpe) ---
            portfolio.mark_to_market(bar.timestamp)

        # Discard any signal that fired on the last bar — no next bar to fill on
        if pending_entry is not None:
            logger.warning(
                "Signal fired on final bar — entry dropped (no next bar to fill at open)"
            )
        pending_entry = None

        # Close any remaining positions at last bar
        if total_bars > 0:
            last_ts = timestamps[-1]
            for pos in list(portfolio.positions):
                if pos.price_is_stale or pos.current_price is None:
                    logger.warning(
                        "Closing position at stale price at backtest end: %s (current_price=%s)",
                        pos.raw_symbol if hasattr(pos, "raw_symbol") else "equity",
                        pos.current_price,
                    )
                portfolio.close_position(pos, pos.current_price, last_ts, "backtest_end")
            # Re-record equity after backtest_end closes so the final curve point
            # reflects realized cash, not open mark-to-market values.
            portfolio.mark_to_market(last_ts)

        logger.info("Backtest complete. %d trades executed.", len(portfolio.closed_trades))
        return portfolio
