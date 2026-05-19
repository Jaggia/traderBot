"""Simulated broker — satisfies BrokerProtocol without placing real orders.

Used with run_live_ibkr.py (or any live runner) when you want the full
signal → entry → exit pipeline to run against live/delayed market data
but manage the P&L in-process rather than routing real orders.

Option pricing uses Black-Scholes (same fallback as the backtest engine).
The caller must call set_underlying_price() after each bar so that
get_option_mid_price() returns prices consistent with the current bar.
"""
import logging
import re
import datetime
from zoneinfo import ZoneInfo

from src.constants import OCC_STRIKE_MULTIPLIER, DEFAULT_RISK_FREE_RATE, DEFAULT_SIGMA
from src.options.option_pricer import black_scholes_price
from src.options.utils import dte_years

import pandas as pd

logger = logging.getLogger(__name__)

_EST = ZoneInfo("America/New_York")

# Incrementing fake order IDs
_order_counter = 0


def _next_order_id() -> str:
    global _order_counter
    _order_counter += 1
    return f"SIM-{_order_counter:06d}"


def _parse_occ(occ_symbol: str) -> dict:
    """Parse OCC symbol (padded or stripped) into components."""
    stripped = occ_symbol.replace(" ", "")
    m = re.match(r'^([A-Z]+)(\d{6})([CP])(\d{8})$', stripped)
    if not m:
        raise ValueError(f"Cannot parse OCC symbol: {occ_symbol!r}")
    underlying = m.group(1)
    yymmdd = m.group(2)
    option_type = m.group(3)
    strike = int(m.group(4)) / OCC_STRIKE_MULTIPLIER
    expiry = datetime.datetime.strptime("20" + yymmdd, "%Y%m%d").replace(
        hour=16, minute=0, second=0, tzinfo=_EST
    )
    return {
        "underlying":  underlying,
        "option_type": option_type,
        "strike":      strike,
        "expiry":      expiry,
        "raw_symbol":  occ_symbol,
    }


class SimulatedTrader:
    """Paper-trades options in-process using Black-Scholes pricing.

    The engine calls set_underlying_price() once per bar (before any
    get_option_mid_price() calls) so pricing is anchored to bar close.

    Satisfies BrokerProtocol — drop-in replacement for IBKRTrader /
    AlpacaTrader in LiveEngine.
    """

    def __init__(self, sigma: float = DEFAULT_SIGMA, r: float = DEFAULT_RISK_FREE_RATE):
        self._sigma = sigma
        self._r = r
        self._underlying_price: float = 0.0
        # symbol -> {qty, avg_entry_price, option_type, strike, expiry, raw_symbol}
        self._positions: dict[str, dict] = {}
        logger.info("SimulatedTrader initialised (sigma=%.2f, r=%.4f)", sigma, r)

    def set_underlying_price(self, price: float) -> None:
        """Call this once per bar with the bar's close price."""
        self._underlying_price = price

    # ------------------------------------------------------------------
    # BrokerProtocol interface
    # ------------------------------------------------------------------

    def get_option_mid_price(self, occ_symbol: str) -> float | None:
        """Black-Scholes price using the last set underlying price."""
        if self._underlying_price <= 0:
            logger.warning("SimulatedTrader: underlying price not set — returning None")
            return None
        try:
            parsed = _parse_occ(occ_symbol)
        except ValueError as exc:
            logger.warning("SimulatedTrader: %s", exc)
            return None

        now = pd.Timestamp.now(tz=_EST)
        expiry_ts = pd.Timestamp(parsed["expiry"])
        t = dte_years(expiry_ts, now)
        price = black_scholes_price(
            S=self._underlying_price,
            K=parsed["strike"],
            T=t,
            sigma=self._sigma,
            r=self._r,
            option_type=parsed["option_type"],
        )
        logger.debug(
            "SimulatedTrader quote: %s S=%.2f K=%.1f T=%.4f sigma=%.2f -> %.4f",
            occ_symbol.replace(" ", ""), self._underlying_price,
            parsed["strike"], t, self._sigma, price,
        )
        return price

    def buy_option(self, occ_symbol: str, qty: int) -> str:
        """Record a simulated BUY. Returns a fake order ID."""
        order_id = _next_order_id()
        price = self.get_option_mid_price(occ_symbol) or 0.0
        key = occ_symbol.replace(" ", "")
        parsed = _parse_occ(occ_symbol)

        if key in self._positions:
            # Average in (shouldn't normally happen — engine is max 1 position)
            existing = self._positions[key]
            total_qty = existing["qty"] + qty
            avg = (existing["avg_entry_price"] * existing["qty"] + price * qty) / total_qty
            existing["qty"] = total_qty
            existing["avg_entry_price"] = avg
        else:
            self._positions[key] = {
                "qty":             qty,
                "avg_entry_price": price,
                "option_type":     parsed["option_type"],
                "strike":          parsed["strike"],
                "expiry":          parsed["expiry"],
                "raw_symbol":      occ_symbol,
            }

        logger.info(
            "SimulatedTrader BUY %dx %s @ %.4f | orderId=%s",
            qty, key, price, order_id,
        )
        return order_id

    def sell_option(self, occ_symbol: str, qty: int) -> str:
        """Record a simulated SELL (close). Returns a fake order ID."""
        order_id = _next_order_id()
        key = occ_symbol.replace(" ", "")
        price = self.get_option_mid_price(occ_symbol) or 0.0

        if key in self._positions:
            del self._positions[key]

        logger.info(
            "SimulatedTrader SELL %dx %s @ %.4f | orderId=%s",
            qty, key, price, order_id,
        )
        return order_id

    def get_order_status(self, order_id: str) -> str:
        """Simulated orders are always immediately filled."""
        return "filled"

    def get_option_positions(self, underlying: str = "SYMBOL") -> list[dict]:
        """Return open positions in the same format as IBKRTrader."""
        results = []
        for key, pos in self._positions.items():
            if not key.startswith(underlying):
                continue
            current = self.get_option_mid_price(pos["raw_symbol"]) or float("nan")
            results.append({
                "symbol":          key,
                "qty":             pos["qty"],
                "avg_entry_price": pos["avg_entry_price"],
                "current_price":   current,
                "side":            "long",
                "underlying":      underlying,
                "expiry":          pos["expiry"],
                "option_type":     pos["option_type"],
                "strike":          pos["strike"],
                "raw_symbol":      pos["raw_symbol"],
                "entry_iv":        None,
            })
        return results

    def cancel_all_orders(self) -> None:
        logger.info("SimulatedTrader: cancel_all_orders (no-op)")
