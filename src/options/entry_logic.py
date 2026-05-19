"""Shared options entry builder — used by both BacktestEngine and LiveEngine."""
import logging
from typing import Callable, Optional

import pandas as pd

from src.constants import DEFAULT_SIGMA
from src.options.position import Position
from src.options.strike_selector import select_strike
from src.options.greeks import compute_greeks
from src.options.option_pricer import implied_vol
from src.options.utils import dte_years

logger = logging.getLogger(__name__)

_last_known_iv: dict[str, float] = {}

def clear_iv_cache():
    """Clear the last known IV cache — call this between backtest runs to ensure independence."""
    global _last_known_iv
    _last_known_iv = {}

def build_option_position(
    signal: int,
    close: float,
    ts: pd.Timestamp,
    contracts: int,
    config: dict,
    get_price_fn: Callable[[str, float, float, str, float], float],
) -> Optional[Position]:
    """Create an options Position for a given signal.

    Parameters
    ----------
    signal       : +1 (long call) or -1 (long put)
    close        : underlying price at entry bar
    ts           : bar timestamp
    contracts    : number of contracts
    config       : parsed strategy_params.yaml
    get_price_fn : callable(raw_symbol, underlying, strike, option_type, dte_years) -> float
                   Caller injects the data source (Databento for backtest, Alpaca for live).
    """
    option_type = "C" if signal == 1 else "P"
    contract = select_strike(
        underlying_price=close,
        current_time=ts,
        option_type=option_type,
        config=config,
    )
    expiry_ts = pd.Timestamp(contract["expiry"])
    entry_ts = pd.Timestamp(ts)
    # Ensure both are tz-aware in America/New_York for subtraction
    if expiry_ts.tz is None:
        expiry_ts = expiry_ts.tz_localize("America/New_York")
    if entry_ts.tz is None:
        entry_ts = entry_ts.tz_localize("America/New_York")
    t = dte_years(expiry_ts, entry_ts)
    entry_price = get_price_fn(
        contract["raw_symbol"], close, contract["strike"], option_type, t
    )
    if entry_price is None or entry_price <= 0:
        logger.warning(
            "build_option_position: entry_price=%s <= 0 for %s strike=%s ts=%s — skipping entry",
            entry_price, contract.get("raw_symbol"), contract.get("strike"), ts,
        )
        return None
    sigma = config.get("options", {}).get("sigma", DEFAULT_SIGMA)
    r = config.get("options", {}).get("risk_free_rate", 0.05)
    q = config.get("options", {}).get("dividend_yield", 0.0)
    # Back-solve IV from the market entry price; fall back to config sigma if it fails.
    # implied_vol raises ValueError when the market price is outside the BS vol range,
    # and returns None when T<=0 (expiry day).  Both cases fall back to config sigma.
    try:
        entry_iv = implied_vol(entry_price, close, contract["strike"], t, r=r, q=q, option_type=option_type)
    except ValueError as exc:
        entry_iv = None
        fallback_iv = _last_known_iv.get(contract.get("raw_symbol"), sigma)
        logger.warning(
            "implied_vol raised for %s (entry_price=%.4f): %s — falling back to iv=%.2f",
            contract.get("raw_symbol"), entry_price, exc, fallback_iv,
        )
    if entry_iv is None:
        entry_iv = _last_known_iv.get(contract.get("raw_symbol"), sigma)
        logger.warning(
            "implied_vol returned None for %s (entry_price=%.4f, T=%.6f) — "
            "using fallback iv=%.2f (Greeks may be unreliable on expiry day)",
            contract.get("raw_symbol"), entry_price, t, entry_iv,
        )
    else:
        _last_known_iv[contract.get("raw_symbol")] = entry_iv
        logger.debug(
            "implied_vol back-solved: %s entry_iv=%.4f (market_price=%.4f)",
            contract.get("raw_symbol"), entry_iv, entry_price,
        )
    greeks = compute_greeks(
        S=close, K=contract["strike"], T=t,
        sigma=entry_iv, r=r, q=q,
        option_type=option_type,
    )
    return Position(
        direction=signal,
        entry_price=entry_price,
        entry_time=ts,
        contracts=contracts,
        trade_mode="options",
        option_type=option_type,
        strike=contract["strike"],
        expiry=contract["expiry"],
        raw_symbol=contract["raw_symbol"],
        delta=greeks["delta"],
        gamma=greeks["gamma"],
        theta=greeks["theta"],
        vega=greeks["vega"],
        entry_iv=entry_iv,
        current_price=entry_price,
    )
