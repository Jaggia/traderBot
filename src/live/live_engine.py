"""Live trading engine — mirrors the backtest logic bar-by-bar.

On each 5-min bar close (called by DatabentoStreamer):
  1. Append bar to rolling buffer
  2. Recompute indicators + signals on the buffer
  3. Check exits on any open position
  4. Check for new entry if flat

Exit rules (matches backtest config — same order as engine.py):
  - Pct stop-loss on option current_price
  - Pct profit-target on option current_price
  - Opposite signal on bar close
  - EOD: close any open position at or after 15:55
  - Expiration: close on or after expiry date

Intrabar polling (options only):
  While a position is open, a background thread polls the option mid-price
  every ~30 seconds and closes immediately if profit target or stop loss is
  breached.  A threading lock prevents races between the bar-close path and
  the intrabar poll.
"""

import logging
import _thread
import datetime
import threading
import time
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from src.signals.strategy import SignalStrategy, create_strategy
from src.options.position import Position
from src.backtest.trade_logic import check_exit, build_entry, BarContext, ExitConfig

logger = logging.getLogger(__name__)

# Default interval (seconds) between intrabar price checks
_DEFAULT_POLL_INTERVAL = 30

# Fill-confirmation polling for buy orders (C-4)
_FILL_POLL_ATTEMPTS = 30
_FILL_POLL_WAIT = 2  # seconds between fill-status checks


class _SellFailedDict(dict):
    """Dict[int, bool] that is truthy when *any* value is True.

    Backward-compatible with code that treats ``engine._sell_failed`` as a bool.
    """
    def __bool__(self) -> bool:
        return any(self.values())


class LiveEngine:
    def __init__(self, strategy_configs: list[dict] | None = None, warmup_df: pd.DataFrame = None, trader=None,
                 poll_interval: float = _DEFAULT_POLL_INTERVAL,
                 data_dir: str | None = None,
                 strategies: list[SignalStrategy] | None = None,
                 # Backward-compatible aliases for single-strategy test helpers
                 config: list[dict] | dict | None = None,
                 strategy: SignalStrategy | None = None):
        """
        Parameters
        ----------
        strategy_configs : list of parsed strategy_params.yaml dicts
        warmup_df : historical bars (DataFrame with DatetimeIndex + OHLCV)
        trader : AlpacaTrader instance
        poll_interval : seconds between intrabar price checks (default 30)
        data_dir : directory for live bars/trades CSV output (default "results/live")
        strategies : list of SignalStrategy instances (default: created from configs)
        """
        # Handle backward-compatible aliases and single-dict shorthand
        if isinstance(strategy_configs, dict):
            strategy_configs = [strategy_configs]
        if strategy_configs is None and config is not None:
            strategy_configs = [config] if isinstance(config, dict) else config
        if strategies is None and strategy is not None:
            strategies = [strategy]

        self._configs = strategy_configs
        if strategies is not None:
            self._strategies = strategies
        else:
            self._strategies = [create_strategy(cfg) for cfg in strategy_configs]

        self._bars = warmup_df.copy()
        self._trader = trader
        
        # Track state for each strategy by index
        self._positions: dict[int, Position | None] = {i: None for i in range(len(self._strategies))}
        self._order_ids: dict[int, str | None] = {i: None for i in range(len(self._strategies))}
        self._closed_trades: list[dict] = []
        self._sell_failed: _SellFailedDict = _SellFailedDict({i: False for i in range(len(self._strategies))})  # set True when sell throws (C-5)

        # Per-strategy exit configurations
        self._exit_configs: list[ExitConfig] = []
        for cfg in strategy_configs:
            exits_cfg = cfg["exits"]
            self._exit_configs.append(ExitConfig(
                profit_target_pct=exits_cfg["profit_target_pct"],
                stop_loss_pct=exits_cfg["stop_loss_pct"],
                eod_close=exits_cfg.get("eod_close", True),
                opposite_signal=exits_cfg.get("opposite_signal", True),
                eod_cutoff_time=exits_cfg.get("eod_cutoff_time", "15:55"),
                zero_dte_safeguard=exits_cfg.get("zero_dte_safeguard", True),
                zero_dte_cutoff_time=exits_cfg.get("zero_dte_cutoff_time", "15:55"),
            ))

        # PriceFn adapter: wrap 5-arg _get_option_price into trade_logic's 6-arg form.
        self._price_fn = lambda sym, und, k, ot, dte, bt, **kw: self._get_option_price(
            sym, und, k, ot, dte, **kw
        )

        # Intrabar polling state
        self._lock = threading.RLock()
        self._poll_interval = poll_interval
        self._poll_stop = threading.Event()
        self._poll_thread: threading.Thread | None = None
        self._fatal_error: Exception | None = None

        # Data logging: save live bars and trades for later analysis
        self._setup_data_logging(data_dir or "results/live")

    # ------------------------------------------------------------------
    # Backward-compatible single-strategy properties for tests
    # ------------------------------------------------------------------

    @property
    def _position(self) -> Position | None:
        """Single-strategy alias: position for strategy 0."""
        return self._positions.get(0)

    @_position.setter
    def _position(self, value: Position | None):
        self._positions[0] = value

    @property
    def _strategy(self):
        """Single-strategy alias: strategy 0."""
        return self._strategies[0] if self._strategies else None

    @_strategy.setter
    def _strategy(self, value):
        if self._strategies:
            self._strategies[0] = value
        else:
            self._strategies.append(value)

    @property
    def _sell_failed_bool(self) -> bool:
        """Legacy bool alias — True if any strategy's sell failed."""
        return any(self._sell_failed.values())

    @property
    def _order_id(self) -> str | None:
        """Single-strategy alias: order ID for strategy 0."""
        return self._order_ids.get(0)

    @_order_id.setter
    def _order_id(self, value: str | None):
        self._order_ids[0] = value

    def _setup_data_logging(self, live_dir_str: str):
        """Create output directory and initialize CSV files for live bars and trades.

        Shared files (bars) go in results/live/YYYY-MM-DD/
        Session files (trades) go in results/live/YYYY-MM-DD/HHMMSS/
        """
        now = datetime.datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        
        # Base daily directory
        self._daily_dir = Path(live_dir_str) / today_str
        self._daily_dir.mkdir(parents=True, exist_ok=True)

        # Session-specific directory
        self._session_dir = self._daily_dir / now.strftime("%H%M%S")
        self._session_dir.mkdir(parents=True, exist_ok=True)

        # One bars file per calendar day — shared across restarts
        self._bars_5m_csv = self._daily_dir / "live_bars_5m.csv"
        self._bars_1m_csv = self._daily_dir / "live_bars_1m.csv"
        self._trades_csv = self._session_dir / "live_trades.csv"

        logger.info("Live daily directory: %s", self._daily_dir)
        logger.info("Live session directory: %s", self._session_dir)
        logger.info("Live bars CSV: %s", self._bars_5m_csv)
        logger.info("Live 1-min bars CSV: %s", self._bars_1m_csv)

    def _save_bar(self, bar: pd.Series, file_path: Path):
        """Append a bar to the specified CSV (only new bars, not warmup)."""
        # Extract timestamp from series name (assumed to be the index)
        bar_df = bar.to_frame().T
        bar_df.to_csv(file_path, mode='a', header=not file_path.exists(), index=True)

    def on_1min_bar(self, bar: pd.Series):
        """Callback invoked by streamers on each 1-min bar."""
        self._save_bar(bar, self._bars_1m_csv)

    def _raise_if_fatal(self):
        if self._fatal_error is None:
            return
        raise RuntimeError("LiveEngine halted after fatal intrabar polling error") from self._fatal_error

    def _record_fatal_error(self, exc: Exception):
        if self._fatal_error is None:
            self._fatal_error = exc
        self._poll_stop.set()
        _thread.interrupt_main()

    def reconcile_positions(self):
        """Check broker for orphaned positions and resume tracking for all strategies.

        Call once at startup before the streamer begins. If open
        option positions are found, reconstructs Position objects so the
        engine can manage exits for them.
        """
        self._raise_if_fatal()
        positions = self._trader.get_option_positions()
        if not positions:
            logger.info("Reconciliation: no open positions found")
            return

        for p in positions:
            pos = Position(
                direction=1,  # options are always long in this framework
                entry_price=p["avg_entry_price"],
                entry_time=pd.Timestamp.now(tz="America/New_York"),
                contracts=p["qty"],
                trade_mode="options",
                option_type=p["option_type"],
                strike=p["strike"],
                expiry=p["expiry"],
                raw_symbol=p["raw_symbol"],
            )
            pos.update_price(p["current_price"])

            # Map to a matching strategy slot
            assigned = False
            for i in range(len(self._strategies)):
                if self._positions[i] is not None:
                    continue
                
                cfg = self._configs[i]
                strat_underlying = cfg["strategy"].get("underlying", "SYMBOL")
                
                # Check for basic underlying and trade mode match.
                # In a more advanced version, we could check strike/expiry if multiple
                # strategies for the same ticker coexist, but for now we prioritize
                # filling an available slot for the correct asset.
                if p["underlying"] == strat_underlying and cfg["strategy"]["trade_mode"] == "options":
                    self._positions[i] = pos
                    logger.info(
                        "Reconciled open position for strategy %d: %s %s %.0f exp=%s | "
                        "entry=%.4f current=%.4f %dx",
                        i, p["symbol"], p["option_type"], p["strike"],
                        p["expiry"].strftime("%Y-%m-%d"),
                        p["avg_entry_price"], p["current_price"], p["qty"],
                    )
                    assigned = True
                    break
            
            if not assigned:
                logger.warning("Found orphaned position that matches no strategy or all slots full: %s", p["symbol"])
        
        if any(p is not None for p in self._positions.values()):
            self._start_poll()

    def on_bar(self, bar: pd.Series):
        """Callback invoked by streamer on each 5-min bar close."""
        self._raise_if_fatal()
        # Normalise timezone to America/New_York so the concat with the
        # tz-aware warmup DataFrame doesn't raise a tz-mixing error.
        bar_ts = bar.name
        if hasattr(bar_ts, "tzinfo") and bar_ts.tzinfo is not None:
            bar_ts = bar_ts.tz_convert("America/New_York")
        else:
            bar_ts = pd.Timestamp(bar_ts).tz_localize("America/New_York")

        # Skip stale/duplicate bars whose timestamp is not newer than the
        # last bar in the buffer (can happen when the streamer replays
        # yesterday's last bar during pre-market).
        if len(self._bars) > 0 and bar_ts <= self._bars.index[-1]:
            logger.debug("Skipping stale bar %s (buffer ends at %s)", bar_ts, self._bars.index[-1])
            return
        new_row = bar.to_frame().T
        new_row.index = pd.DatetimeIndex([bar_ts], tz="America/New_York")
        new_row.index.name = "timestamp"
        self._bars = pd.concat([self._bars, new_row])
        self._bars.index = pd.DatetimeIndex(self._bars.index, tz="America/New_York")
        self._bars = self._bars.iloc[-300:]  # keep last 300 bars in memory

        # Save live bar to CSV
        self._save_bar(bar, self._bars_5m_csv)

        ts = bar.name
        close = float(bar["close"])
        high = float(bar["high"])
        low = float(bar["low"])

        # If the trader supports simulated pricing, update its underlying price.
        if hasattr(self._trader, "set_underlying_price"):
            self._trader.set_underlying_price(close)

        # Recompute indicators and signals for each strategy
        for i in range(len(self._strategies)):
            strat = self._strategies[i]
            cfg = self._configs[i]

            df = strat.compute_indicators(self._bars, cfg)
            signals = strat.generate_signals(df, cfg)
            signal = int(signals.iloc[-1])

            logger.info(
                "[Strategy %d] %s signal=%d close=%.2f | cols=%s",
                i, cfg["strategy"].get("signal_system", "?"), signal, close,
                [c for c in df.columns if c not in ("open", "high", "low", "close", "volume")],
            )

            with self._lock:
                self._check_exits(i, close, high, low, signal, ts)
            
            # _check_entry manages its own locking internally to release during fill-polling
            self._check_entry(i, close, signal, ts)

    def _get_option_price(
        self,
        raw_symbol: str,
        underlying_close: float,
        strike: float,
        option_type: str,
        dte_years: float,
        sigma: float | None = None,
        field: str = "close",
    ) -> float:
        """Get option price from the live broker quote stream.

        The *field* parameter ('close', 'high', 'low') is used by the shared
        exit logic to determine conservative fill prices:

        - ``field="low"``  → used for stop-loss checks → returns bid price
          (worst case for a long position being stopped out)
        - ``field="high"`` → used for take-profit checks → returns ask price
          (conservative best-case fill)
        - ``field="close"`` (default) → returns mid-price

        If the broker does not expose separate bid/ask (or they are zero/None),
        falls back to mid-price for all fields.
        """
        # Try to get bid/ask for field-sensitive pricing (BUG-013 fix)
        if field in ("low", "high") and hasattr(self._trader, "get_option_quote"):
            try:
                result = self._trader.get_option_quote(raw_symbol)
                if result is None:
                    logger.warning("get_option_quote returned None for %s", raw_symbol)
                else:
                    bid, ask = result
                    if bid is not None and ask is not None and bid > 0 and ask > 0:
                        if field == "low":
                            return bid
                        elif field == "high":
                            return ask
            except (ConnectionError, TimeoutError) as e:
                logger.debug("Network error fetching quote for %s: %s", raw_symbol, e)

        # Default: return mid-price (live mode uses mid-price when bid/ask
        # is not separately available from the broker)
        try:
            price = self._trader.get_option_mid_price(raw_symbol)
        except (ConnectionError, TimeoutError) as e:
            logger.debug("Network error fetching mid-price for %s: %s", raw_symbol, e)
            return None
        if price is not None:
            return price
        # Return None instead of raising — callers (check_exit, _poll_check)
        # handle None gracefully by skipping price-dependent exit checks
        # while still running signal-based and EOD exits.
        logger.warning(
            "Live option quote unavailable for %s "
            "(underlying=%.2f, strike=%.1f, type=%s, field=%s) — returning None",
            raw_symbol, underlying_close, strike, option_type, field,
        )
        return None

    def _check_exits(self, strat_idx: int = 0, close: float = 0.0, high: float = 0.0, low: float = 0.0, signal: int = 0, ts: pd.Timestamp | None = None):
        pos = self._positions[strat_idx]
        if pos is None:
            return

        bar = BarContext(ts, close, close, high, low, signal, ts.hour, ts.minute)
        result = check_exit(pos, bar, self._exit_configs[strat_idx],
                            get_option_price=self._price_fn)
        if result:
            self._close(strat_idx, ts, result.reason, exit_price=result.fill_price)

    def _check_entry(self, strat_idx: int, close: float, signal: int, ts: pd.Timestamp):
        """Evaluate entry logic and manage order fill polling.

        Note: this method manages the main self._lock internally to ensure that
        polling for an order fill does not block other strategies or the
        intrabar polling thread.
        """
        # Quick check without lock (state might change, but we double-check inside lock)
        if self._positions[strat_idx] is not None or signal == 0:
            return

        cfg = self._configs[strat_idx]
        exit_cfg = self._exit_configs[strat_idx]
        trade_mode = cfg["strategy"]["trade_mode"]
        
        # Skip if outside normal entry window (matches backtest: no entries at/after cutoff)
        from src.backtest.trade_logic import _is_eod
        if _is_eod(ts.hour, ts.minute, exit_cfg.eod_cutoff_time):
            return

        contracts = cfg["position"].get("contracts_per_trade", 1)
        bar = BarContext(ts, close, close, close, close, signal, ts.hour, ts.minute)
        
        # build_entry might need quotes, but does not modify state — call outside lock
        try:
            pos = build_entry(signal, bar, contracts, trade_mode,
                              cfg, exit_cfg, self._price_fn)
        except RuntimeError as exc:
            logger.warning(
                "[Strategy %d] Entry skipped at %s — option quote unavailable: %s",
                strat_idx, ts, exc,
            )
            logger.debug("Full traceback for skipped entry", exc_info=True)
            return

        if pos is None:
            logger.warning("build_entry returned None for strategy %d at %s — skipping entry", strat_idx, ts)
            return

        # Execute entry order — broker call usually fast, but we double check position state first
        with self._lock:
            if self._positions[strat_idx] is not None:
                return # concurrent entry or reconciliation filled the slot
            
            # TODO: Move broker calls out of the lock to prevent blocking during high latency
            if trade_mode == "options":
                order_id = self._trader.buy_option(pos.raw_symbol, int(contracts))
            else:
                symbol = cfg["strategy"].get("symbol", cfg["strategy"].get("underlying"))
                order_id = self._trader.buy_equity(symbol, contracts, signal)

        # C-4: Verify the order was filled before tracking the position.
        # RELEASE THE LOCK during sleep intervals to avoid blocking other strategies.
        filled = False
        for attempt in range(1, _FILL_POLL_ATTEMPTS + 1):
            try:
                # Trader status check is generally thread-safe for reading status
                status = self._trader.get_order_status(order_id)
                if status == "filled":
                    filled = True
                    break
                logger.debug(
                    "Fill poll %d/%d for order %s: status=%s",
                    attempt, _FILL_POLL_ATTEMPTS, order_id, status,
                )
            except Exception:
                logger.exception(
                    "Fill poll %d/%d: error querying order %s",
                    attempt, _FILL_POLL_ATTEMPTS, order_id,
                )
            if attempt < _FILL_POLL_ATTEMPTS:
                time.sleep(_FILL_POLL_WAIT)

        if not filled:
            logger.error(
                "Order %s for %s not confirmed filled after %d attempts — "
                "skipping position tracking",
                order_id, pos.raw_symbol if trade_mode == "options" else cfg["strategy"].get("underlying", "?"), 
                _FILL_POLL_ATTEMPTS,
            )
            return

        # Finally, update state under lock — re-check that position hasn't been
        # closed or filled by a concurrent thread while we were polling (BUG-014).
        with self._lock:
            if self._positions[strat_idx] is not None:
                # Slot was filled by reconciliation or another concurrent path —
                # skip assignment to avoid overwriting with stale position data.
                logger.warning(
                    "[Strategy %d] Position slot already occupied after fill poll — "
                    "skipping stale assignment for order %s",
                    strat_idx, order_id,
                )
                return
            self._order_ids[strat_idx] = order_id
            self._positions[strat_idx] = pos

            if trade_mode == "options":
                logger.info(
                    "[Strategy %d] ENTERED %s %.0f exp=%s entry_price=%.4f close=%.2f "
                    "delta=%.3f gamma=%.5f theta=%.4f | %s",
                    strat_idx, pos.option_type, pos.strike,
                    pd.Timestamp(pos.expiry).date(),
                    pos.entry_price, close,
                    pos.delta, pos.gamma, pos.theta,
                    ts,
                )
            else:
                logger.info(
                    "[Strategy %d] ENTERED EQUITY %s contracts=%d entry_price=%.2f | %s",
                    strat_idx, "LONG" if signal == 1 else "SHORT", contracts, pos.entry_price, ts
                )

            self._start_poll()

    def _close(self, strat_idx: int, ts: "pd.Timestamp", reason: str, exit_price: float | None = None) -> None:
        cfg = self._configs[strat_idx]
        pos = self._positions[strat_idx]
        if pos is None:
            return
        effective_exit_price = pos.current_price if exit_price is None else exit_price
        pos.update_price(effective_exit_price)

        # C-5: Wrap sell so a broker-side failure is handled safely.
        # On failure we set _sell_failed[strat_idx]=True so the operator can investigate,
        # but we DO NOT clear the position — we keep it open so the bot doesn't
        # assume it is flat when the broker trade failed.
        # TODO: Move sell out of the lock to prevent blocking during high latency
        sell_exc: Exception | None = None
        try:
            trade_mode = getattr(pos, 'trade_mode', 'options')
            if trade_mode == "options":
                self._trader.sell_option(pos.raw_symbol, int(pos.contracts))
            else:
                # Equity position: use sell_equity with the underlying symbol
                symbol = cfg["strategy"].get("underlying", getattr(pos, "raw_symbol", "?"))
                self._trader.sell_equity(symbol, pos.contracts)
        except Exception as exc:
            sell_exc = exc
            self._sell_failed[strat_idx] = True
            logger.error(
                "[Strategy %d] SELL FAILED for %s %dx — keeping position open to retry later. "
                "MANUAL INTERVENTION MAY BE REQUIRED. reason=%s exc=%s",
                strat_idx, pos.raw_symbol, pos.contracts, reason, exc,
                exc_info=True,
            )
            return

        # P&L net of transaction costs (matches backtest Portfolio accounting)
        cfg = self._configs[strat_idx]
        costs_cfg = cfg.get("costs", {})
        def _txn_cost(price: float, contracts: float) -> float:
            commission = costs_cfg.get("commission_per_contract", 0.65) * contracts
            slippage = costs_cfg.get("slippage_per_contract", 0.0) * contracts
            return commission + slippage

        entry_cost = _txn_cost(pos.entry_price, pos.contracts)
        exit_cost = _txn_cost(effective_exit_price, pos.contracts)
        entry_notional = pos.entry_price * pos.contracts * 100
        exit_notional = effective_exit_price * pos.contracts * 100
        pnl = (exit_notional - entry_notional) - (entry_cost + exit_cost)
        pnl_pct = (
            (pnl / entry_notional) * 100
            if entry_notional != 0
            else 0.0
        )

        self._closed_trades.append({
            "strategy_id": strat_idx,
            "entry_time":  pos.entry_time,
            "exit_time":   ts,
            "option_type": pos.option_type,
            "strike":      pos.strike,
            "expiry":      pos.expiry,
            "entry_price": pos.entry_price,
            "exit_price":  effective_exit_price,
            "contracts":   pos.contracts,
            "pnl":         pnl,
            "pnl_pct":     pnl_pct,
            "reason":      reason,
            "delta":       pos.delta,
            "gamma":       pos.gamma,
            "theta":       pos.theta,
            "vega":        pos.vega,
            "order_id":    self._order_ids[strat_idx],
            "sell_failed": sell_exc is not None,
        })

        logger.info(
            "[Strategy %d] CLOSED %s %.0f — reason=%s exit_price=%.4f pnl=$%.2f (%.1f%%) | %s",
            strat_idx, pos.option_type, pos.strike, reason, effective_exit_price, pnl, pnl_pct, ts,
        )

        self._positions[strat_idx] = None
        self._order_ids[strat_idx] = None
        
        # If no more positions, stop polling (or it will break in _poll_loop)
        if not any(p is not None for p in self._positions.values()):
            self._stop_poll()

    def force_close(self, reason: str = "manual_stop"):
        """Close all open positions immediately (called on Ctrl+C or EOD cleanup)."""
        with self._lock:
            for i in list(self._positions.keys()):
                if self._positions[i]:
                    now = pd.Timestamp(datetime.datetime.now(tz=ZoneInfo("America/New_York")))
                    self._close(i, now, reason)
        self._trader.cancel_all_orders()

    def get_closed_trades(self) -> list:
        """Returns the list of closed trade records for EOD reporting.

        If no trades were made this session, the empty session folder is
        removed automatically to avoid cluttering results/live/.
        """
        if self._closed_trades:
            trades_df = pd.DataFrame(self._closed_trades)
            trades_df.to_csv(self._trades_csv, index=False)
            logger.info("Saved %d trade(s) to %s", len(self._closed_trades), self._trades_csv)
        else:
            # No trades — remove the empty session folder
            try:
                self._session_dir.rmdir()
                logger.info("No trades this session — removed empty folder %s", self._session_dir)
            except OSError:
                pass  # non-empty (unexpected file present) — leave it
        return self._closed_trades

    # ------------------------------------------------------------------
    # Intrabar polling — check option price between 5-min bar closes
    # ------------------------------------------------------------------

    def _start_poll(self):
        """Start the intrabar polling thread."""
        if self._poll_thread is not None and self._poll_thread.is_alive():
            logger.debug("Intrabar poll already running — skipping duplicate start")
            return
        self._poll_stop.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="intrabar-poll",
        )
        self._poll_thread.start()
        logger.debug("Intrabar poll started (interval=%ss)", self._poll_interval)

    def _stop_poll(self):
        """Signal the polling thread to stop and wait for it to finish."""
        self._poll_stop.set()
        if self._poll_thread is not None and self._poll_thread.is_alive():
            if threading.current_thread() is self._poll_thread:
                # Called from inside the poll thread — cannot join ourselves.
                logger.debug("_stop_poll called from within poll thread — skipping join")
            else:
                self._poll_thread.join(timeout=5)
        self._poll_thread = None

    def _poll_loop(self):
        """Background loop: fetch option mid-price and check thresholds."""
        while not self._poll_stop.wait(timeout=self._poll_interval):
            with self._lock:
                if not any(p is not None for p in self._positions.values()):
                    break
                try:
                    self._poll_check()
                except Exception as exc:
                    logger.exception("Intrabar poll error")
                    self._record_fatal_error(exc)
                    raise

    def _poll_check(self):
        """Single poll iteration — must be called while holding self._lock."""
        for i, pos in self._positions.items():
            if pos is None:
                continue

            mid = self._trader.get_option_mid_price(pos.raw_symbol)
            if mid is None:
                logger.warning(
                    "Intrabar poll: could not fetch option quote for %s — skipping this tick",
                    pos.raw_symbol,
                )
                continue

            pos.update_price(mid)
            pnl_pct = pos.pnl_pct()
            now = pd.Timestamp(
                datetime.datetime.now(tz=datetime.timezone.utc).astimezone()
            )

            exit_cfg = self._exit_configs[i]
            if pnl_pct <= -exit_cfg.stop_loss_pct:
                logger.info(
                    "[Strategy %d] INTRABAR STOP — %s %.0f mid=%.4f pnl=%.1f%% (threshold -%.1f%%)",
                    i, pos.option_type, pos.strike, mid, pnl_pct, exit_cfg.stop_loss_pct,
                )
                self._close(i, now, "intrabar_stop")
            elif pnl_pct >= exit_cfg.profit_target_pct:
                logger.info(
                    "[Strategy %d] INTRABAR TARGET — %s %.0f mid=%.4f pnl=%.1f%% (threshold +%.1f%%)",
                    i, pos.option_type, pos.strike, mid, pnl_pct, exit_cfg.profit_target_pct,
                )
                self._close(i, now, "intrabar_target")
