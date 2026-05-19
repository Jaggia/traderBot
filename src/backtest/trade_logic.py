"""Trade logic deep module — check_exit and build_entry.

Extracts the exit-evaluation and entry-construction logic that was previously
inlined in the BacktestEngine hot loop. Both engines (backtest + live) now
delegate to these two public functions.

Exit priority (matches original engine.py exactly):
  1. Equity intrabar stop  (bar.low/high vs stop_price)
  2. Equity intrabar limit (bar.high/low vs limit_price)
  3. Options: delegate to check_option_exit
  4. Equity fallback: opposite_signal, eod_close
"""

from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Optional

import pandas as pd

from src.options.position import Position
from src.options.exit_rules import check_option_exit
from src.options.entry_logic import build_option_position
from src.options.utils import dte_years

# PriceFn: (raw_symbol, underlying, strike, option_type, dte_years, bar_time, **kwargs) -> float
# Accepts optional sigma kwarg so engines can preserve the shared pricing interface.
PriceFn = Callable


@dataclass(frozen=True)
class BarContext:
    """Immutable snapshot of a single bar for trade logic."""
    timestamp: object  # numpy.datetime64 (backtest) or pd.Timestamp (live)
    open: float
    close: float
    high: float
    low: float
    signal: int
    hour: int
    minute: int


@dataclass(frozen=True)
class ExitConfig:
    """Immutable exit-rule configuration."""
    profit_target_pct: float   # Gross (pre-cost) price threshold; trade log pnl_pct is net of costs
    stop_loss_pct: float       # Gross (pre-cost) price threshold; trade log pnl_pct is net of costs
    eod_close: bool
    opposite_signal: bool
    eod_cutoff_time: str = "15:55"  # HH:MM — time at/after which EOD close fires
    zero_dte_safeguard: bool = True
    zero_dte_cutoff_time: str = "15:55"


@dataclass(frozen=True)
class ExitResult:
    """Outcome of an exit check — reason + fill price."""
    reason: str
    fill_price: float


@lru_cache(maxsize=8)
def _parse_cutoff_time(cutoff_time: str) -> tuple[int, int]:
    """Parse and validate an HH:MM cutoff time string.

    Returns (hour, minute).  Raises ValueError for malformed or out-of-range input.
    """
    parts = cutoff_time.split(":")
    if len(parts) != 2:
        raise ValueError(
            f"Invalid cutoff_time {cutoff_time!r}: expected HH:MM format"
        )
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError(
            f"Invalid cutoff_time {cutoff_time!r}: hour and minute must be integers"
        )
    if not (0 <= h <= 23):
        raise ValueError(
            f"Invalid cutoff_time {cutoff_time!r}: hour must be 0-23, got {h}"
        )
    if not (0 <= m <= 59):
        raise ValueError(
            f"Invalid cutoff_time {cutoff_time!r}: minute must be 0-59, got {m}"
        )
    return h, m


def _is_eod(hour: int, minute: int, cutoff_time: str) -> bool:
    """Return True if (hour, minute) is at or after the HH:MM cutoff."""
    cutoff_h, cutoff_m = _parse_cutoff_time(cutoff_time)
    return hour > cutoff_h or (hour == cutoff_h and minute >= cutoff_m)


def check_exit(
    pos: Position,
    bar: BarContext,
    config: ExitConfig,
    get_option_price: Optional[PriceFn] = None,
) -> Optional[ExitResult]:
    """Evaluate whether *pos* should be closed on this bar.

    Returns ExitResult(reason, fill_price) or None.
    Priority matches the original engine.py hot loop exactly.
    """
    # 1. Update current_price (via update_price to track high_water)
    if pos.trade_mode == "equities":
        pos.update_price(bar.close)
    else:
        _iv_kw = {"sigma": pos.entry_iv} if pos.entry_iv is not None else {}
        _opt_price = get_option_price(
            pos.raw_symbol, bar.close, pos.strike, pos.option_type,
            dte_years(pos.expiry, bar.timestamp), bar.timestamp,
            **_iv_kw,
        )
        if _opt_price is not None:
            pos.update_price(_opt_price)
            pos.price_is_stale = False
        else:
            # Stale/missing option data — mark so downstream knows price is not fresh
            pos.price_is_stale = True

    exit_price = pos.current_price

    # 2. Equity intrabar stop (checked first — wins on wide-range bars)
    #    GAP AWARE: if open is already beyond stop_price, fill at open.
    if pos.trade_mode == "equities" and pos.stop_price is not None:
        if pos.direction == 1: # Long
            if bar.open <= pos.stop_price:
                return ExitResult("stop_loss", bar.open)
            if bar.low <= pos.stop_price:
                return ExitResult("stop_loss", pos.stop_price)
        if pos.direction == -1: # Short
            if bar.open >= pos.stop_price:
                return ExitResult("stop_loss", bar.open)
            if bar.high >= pos.stop_price:
                return ExitResult("stop_loss", pos.stop_price)

    # 3. Equity intrabar limit
    #    GAP AWARE: if open is already beyond limit_price, fill at open.
    if pos.trade_mode == "equities" and pos.limit_price is not None:
        if pos.direction == 1: # Long
            if bar.open >= pos.limit_price:
                return ExitResult("profit_target", bar.open)
            if bar.high >= pos.limit_price:
                return ExitResult("profit_target", pos.limit_price)
        if pos.direction == -1: # Short
            if bar.open <= pos.limit_price:
                return ExitResult("profit_target", bar.open)
            if bar.low <= pos.limit_price:
                return ExitResult("profit_target", pos.limit_price)

    # 4. Options: intrabar check using actual option bar high/low from Databento
    #    Uses the 1-min option bar extremes within the 5-min window — no model needed.
    if pos.trade_mode == "options":
        if get_option_price is not None and pos.entry_price is not None and pos.entry_price != 0:
            _dte = dte_years(pos.expiry, bar.timestamp)

            # Stop-loss: use option bar low (worst price for a long option)
            price_at_low = get_option_price(
                pos.raw_symbol, bar.close, pos.strike, pos.option_type,
                _dte, bar.timestamp, field="low",
            )
            if price_at_low is not None:
                pnl_pct_at_low = (price_at_low - pos.entry_price) / pos.entry_price * 100
                if pnl_pct_at_low <= -config.stop_loss_pct:
                    return ExitResult("stop_loss", price_at_low)

            # Profit target: use option bar high (best price for a long option)
            price_at_high = get_option_price(
                pos.raw_symbol, bar.close, pos.strike, pos.option_type,
                _dte, bar.timestamp, field="high",
            )
            if price_at_high is not None:
                pnl_pct_at_high = (price_at_high - pos.entry_price) / pos.entry_price * 100
                if pnl_pct_at_high >= config.profit_target_pct:
                    return ExitResult("profit_target", price_at_high)
        if _opt_price is None:
            # Stale/missing data — skip all exit checks to avoid false exits
            return None
        reason = check_option_exit(
            pos, bar.signal, pd.Timestamp(bar.timestamp),
            config.profit_target_pct, config.stop_loss_pct,
            config.eod_close, config.opposite_signal,
            config.eod_cutoff_time,
            config.zero_dte_safeguard, config.zero_dte_cutoff_time,
        )
        if reason:
            return ExitResult(reason, exit_price)
        return None

    # 5. Equity fallback: opposite signal + EOD
    if config.opposite_signal and bar.signal != 0 and bar.signal != pos.direction:
        return ExitResult("opposite_signal", exit_price)
    if config.eod_close and _is_eod(bar.hour, bar.minute, config.eod_cutoff_time):
        return ExitResult("eod_close", exit_price)

    return None


def build_entry(
    signal: int,
    bar: BarContext,
    contracts: float,
    trade_mode: str,
    config: dict,
    exit_config: ExitConfig,
    get_option_price: Optional[PriceFn] = None,
) -> Optional[Position]:
    """Build a Position for a new entry, or None if signal is zero.

    Parameters
    ----------
    trade_mode : "equities" or "options" (never "both" — caller dispatches)
    """
    if signal == 0:
        return None

    if trade_mode == "equities":
        sl_pct = exit_config.stop_loss_pct / 100.0
        tp_pct = exit_config.profit_target_pct / 100.0
        # Use bar.open (the actual fill price) for stop/limit, not bar.close (signal bar's close)
        fill_price = bar.open
        if signal == 1:  # long
            stop_px = fill_price * (1 - sl_pct)
            limit_px = fill_price * (1 + tp_pct)
        else:  # short
            stop_px = fill_price * (1 + sl_pct)
            limit_px = fill_price * (1 - tp_pct)

        return Position(
            direction=signal,
            entry_price=fill_price,
            entry_time=bar.timestamp,
            contracts=contracts,
            trade_mode="equities",
            stop_price=stop_px,
            limit_price=limit_px,
        )

    if trade_mode == "options":
        ts = bar.timestamp

        def _price_adapter(sym, und, k, ot, dte):
            return get_option_price(sym, und, k, ot, dte, ts)

        return build_option_position(
            signal, bar.close, pd.Timestamp(ts), contracts, config,
            get_price_fn=_price_adapter,
        )

    return None
