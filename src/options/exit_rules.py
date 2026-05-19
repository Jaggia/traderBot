"""Shared options exit logic — used by both BacktestEngine and LiveEngine."""
import logging
from typing import Optional

import pandas as pd

from src.options.position import Position

logger = logging.getLogger(__name__)


def _is_eod(hour: int, minute: int, cutoff_time: str) -> bool:
    """Return True if (hour, minute) is at or after the HH:MM cutoff.

    Lazy-imports from trade_logic to avoid circular imports
    (trade_logic imports check_option_exit from this module).
    """
    from src.backtest.trade_logic import _is_eod as _impl
    return _impl(hour, minute, cutoff_time)


def check_option_exit(
    pos: Position,
    signal: int,
    ts: pd.Timestamp,
    profit_target_pct: float,
    stop_loss_pct: float,
    eod_close: bool,
    opposite_signal_enabled: bool,
    eod_cutoff_time: str = "15:55",
    zero_dte_safeguard: bool = True,
    zero_dte_cutoff_time: str = "15:55",
) -> Optional[str]:
    """Return exit reason string or None if position should be held.

    Checks in order (first match wins):
      1. stop_loss      — pnl_pct <= -stop_loss_pct
      2. profit_target  — pnl_pct >= profit_target_pct
      3. opposite_signal
      4. eod_close      — ts >= cutoff
      5. expiration     — same-day EOD or late-day check
    """
    pnl_pct = pos.pnl_pct()

    # Guard against accidental fraction values (0 < x < 1) when 0-100 scale is expected
    if 0 < stop_loss_pct < 1:
        logger.warning(
            f"stop_loss_pct={stop_loss_pct} looks like a fraction (0-1 scale) but "
            f"pnl_pct() returns 0-100 scale. Use e.g. 20.0 for 20%, not 0.20."
        )
    if 0 < profit_target_pct < 1:
        logger.warning(
            f"profit_target_pct={profit_target_pct} looks like a fraction (0-1 scale) but "
            f"pnl_pct() returns 0-100 scale. Use e.g. 20.0 for 20%, not 0.20."
        )

    if pnl_pct <= -stop_loss_pct:
        return "stop_loss"
    if pnl_pct >= profit_target_pct:
        return "profit_target"
    if opposite_signal_enabled and signal != 0 and signal != pos.direction:
        return "opposite_signal"

    # EOD Cutoff Logic
    if eod_close and _is_eod(ts.hour, ts.minute, eod_cutoff_time):
        return "eod_close"

    # Expiration Safeguard: Force exit on the day of expiry at cutoff,
    # or any time after the expiry date.
    if pos.expiry is not None and zero_dte_safeguard:
        expiry_ts = pd.Timestamp(pos.expiry)
        # 1. It's the expiry day and we reached the cutoff
        if ts.date() == expiry_ts.date() and _is_eod(ts.hour, ts.minute, zero_dte_cutoff_time):
            return "expiration"
        # 2. It's past the expiry day (safety fallback)
        if ts.date() > expiry_ts.date():
            return "expiration"

    return None
