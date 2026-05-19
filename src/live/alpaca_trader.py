"""Alpaca paper trading client for options orders.

Wraps alpaca-py TradingClient pointed at the paper endpoint.
OCC symbols from build_occ_symbol() have spaces in the root padding
("SYMBOL   260228C00450000") — Alpaca expects them stripped
("SYMBOL260228C00450000").
"""

import logging
import re
from datetime import datetime

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionLatestQuoteRequest

from src.constants import OCC_STRIKE_MULTIPLIER

logger = logging.getLogger(__name__)


def _strip_occ(occ_symbol: str) -> str:
    """'SYMBOL   260228C00450000' → 'SYMBOL260228C00450000'"""
    return occ_symbol.replace(" ", "")


def parse_occ_symbol(occ_symbol: str) -> dict:
    """Parse an OCC option symbol into its components.

    Accepts both padded ('SYMBOL   260221C00451000') and stripped
    ('SYMBOL260221C00451000') forms.

    Returns dict with keys: underlying, expiry (datetime), option_type, strike, raw_symbol.
    """
    stripped = _strip_occ(occ_symbol)
    m = re.match(r'^([A-Z]+)(\d{6})([CP])(\d{8})$', stripped)
    if not m:
        raise ValueError(f"Cannot parse OCC symbol: {occ_symbol!r}")

    underlying = m.group(1)
    expiry = datetime.strptime(m.group(2), "%y%m%d")
    option_type = m.group(3)
    strike = int(m.group(4)) / OCC_STRIKE_MULTIPLIER

    # Reconstruct the padded raw_symbol (6-char root)
    raw_symbol = f"{underlying.ljust(6)}{m.group(2)}{option_type}{m.group(4)}"

    return {
        "underlying": underlying,
        "expiry": expiry,
        "option_type": option_type,
        "strike": strike,
        "raw_symbol": raw_symbol,
    }


class AlpacaTrader:
    def __init__(self, api_key: str, secret_key: str):
        self._client = TradingClient(api_key, secret_key, paper=True)
        self._data_client = OptionHistoricalDataClient(api_key, secret_key)
        logger.info("Alpaca paper trading client initialised")

    def get_option_mid_price(self, occ_symbol: str) -> float | None:
        """Fetch the mid-price of an option from Alpaca live quotes.

        Returns mid-price (bid + ask) / 2 if both are valid, else None.
        LiveEngine treats None as a runtime error in live trading.
        """
        symbol = _strip_occ(occ_symbol)
        resp = self._data_client.get_option_latest_quote(
            OptionLatestQuoteRequest(symbol_or_symbols=symbol)
        )
        try:
            quote = resp[symbol]
        except KeyError:
            logger.debug("Option quote unavailable for %s", symbol)
            return None
        if quote.bid_price > 0 and quote.ask_price > 0:
            return (quote.bid_price + quote.ask_price) / 2.0
        return None

    def buy_option(self, occ_symbol: str, qty: int) -> str:
        """Place a market buy for `qty` contracts. Returns Alpaca order ID."""
        symbol = _strip_occ(occ_symbol)
        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
        order = self._client.submit_order(req)
        logger.info("BUY %dx %s — order_id=%s", qty, symbol, order.id)
        return str(order.id)

    def sell_option(self, occ_symbol: str, qty: int) -> str:
        """Place a market sell to close `qty` contracts. Returns Alpaca order ID."""
        symbol = _strip_occ(occ_symbol)
        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        order = self._client.submit_order(req)
        logger.info("SELL %dx %s — order_id=%s", qty, symbol, order.id)
        return str(order.id)

    def buy_equity(self, symbol: str, qty: int, signal: int) -> str:
        """Place a market buy for `qty` shares. Returns Alpaca order ID.

        The *signal* parameter indicates direction (+1 long, -1 short) and
        is used to determine order side. For the initial implementation only
        long (BUY) is supported — short selling would require a locate.
        """
        side = OrderSide.BUY if signal >= 0 else OrderSide.SELL
        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=side,
            time_in_force=TimeInForce.DAY,
        )
        order = self._client.submit_order(req)
        logger.info("BUY EQUITY %dx %s — order_id=%s", qty, symbol, order.id)
        return str(order.id)

    def sell_equity(self, symbol: str, qty: int) -> str:
        """Place a market sell to close `qty` shares. Returns Alpaca order ID."""
        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        order = self._client.submit_order(req)
        logger.info("SELL EQUITY %dx %s — order_id=%s", qty, symbol, order.id)
        return str(order.id)

    def get_positions(self) -> list:
        """Return all open positions on the paper account."""
        return self._client.get_all_positions()

    def get_option_positions(self, underlying: str = "SYMBOL") -> list[dict]:
        """Return open option positions for the given underlying.

        Each returned dict has: symbol, qty, avg_entry_price, current_price,
        side, and the parsed OCC fields (underlying, expiry, option_type,
        strike, raw_symbol).
        """
        all_positions = self._client.get_all_positions()
        results = []
        for pos in all_positions:
            sym = str(pos.symbol)
            # Option symbols contain the underlying + date + C/P + strike.
            # Equity symbols are just the ticker (e.g. "SYMBOL").
            # Filter: must start with underlying and be longer (has date/strike).
            if not sym.startswith(underlying) or len(sym) <= len(underlying):
                continue
            try:
                parsed = parse_occ_symbol(sym)
            except ValueError:
                logger.debug("Skipping non-option position: %s", sym)
                continue

            results.append({
                "symbol": sym,
                "qty": int(pos.qty),
                "avg_entry_price": float(pos.avg_entry_price),
                "current_price": float(pos.current_price),
                "side": str(pos.side),
                **parsed,
            })
        return results

    def get_order_status(self, order_id: str) -> str:
        """Return lowercase fill status for a previously placed order.

        Returns 'filled', 'partially_filled', 'pending_new', etc.
        Returns 'unknown' on any error.
        """
        try:
            order = self._client.get_order_by_id(order_id)
            return str(getattr(order, "status", "unknown")).lower()
        except Exception:
            logger.debug("get_order_status: error querying order %s", order_id)
            return "unknown"

    def cancel_all_orders(self):
        """Cancel any pending orders (safety call at shutdown)."""
        self._client.cancel_orders()
        logger.info("All pending orders cancelled")
