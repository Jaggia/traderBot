"""IBKR paper trading client for options orders.

Wraps ib_insync pointed at IB Gateway (paper account, port 4002).
Presents the same public interface as AlpacaTrader so LiveEngine can use
either broker without changes.

OCC symbol handling:
  Padded form (internal): 'SYMBOL   260228C00450000'
  Stripped form:          'SYMBOL260228C00450000'
  IBKR Option contract:   symbol='SYMBOL', lastTradeDateOrContractMonth='20260228',
                           strike=450.0, right='C', exchange='SMART'

Note: IBKR's avgCost for options is reported per share (×100 = per contract).
"""

import logging
import re
import time
from datetime import datetime

from ib_insync import IB, Option, MarketOrder

from src.constants import OCC_STRIKE_MULTIPLIER
from src.live.ibkr_model_pricer import IbkrModelPricer

logger = logging.getLogger(__name__)

_SNAPSHOT_WAIT_S = 0.5  # seconds to wait for snapshot market data from IBKR


# ---------------------------------------------------------------------------
# OCC symbol utilities
# ---------------------------------------------------------------------------

def _strip_occ(occ_symbol: str) -> str:
    """'SYMBOL   260228C00450000' → 'SYMBOL260228C00450000'"""
    return occ_symbol.replace(" ", "")


def _parse_occ(occ_symbol: str) -> dict:
    """Parse an OCC option symbol into its components.

    Accepts both padded ('SYMBOL   260228C00450000') and stripped
    ('SYMBOL260228C00450000') forms.

    Returns dict with keys:
        underlying  : str         e.g. 'SYMBOL'
        expiry_yymmdd : str       e.g. '260228'
        expiry_yyyymmdd : str     e.g. '20260228'  (IBKR format)
        expiry      : datetime
        option_type : str         'C' or 'P'
        strike      : float       e.g. 450.0
        raw_symbol  : str         padded OCC form
    """
    stripped = _strip_occ(occ_symbol)
    m = re.match(r'^([A-Z]+)(\d{6})([CP])(\d{8})$', stripped)
    if not m:
        raise ValueError(f"Cannot parse OCC symbol: {occ_symbol!r}")

    underlying = m.group(1)
    yymmdd = m.group(2)
    option_type = m.group(3)
    strike = int(m.group(4)) / OCC_STRIKE_MULTIPLIER
    expiry = datetime.strptime(yymmdd, "%y%m%d")
    # underlying is always ≤6 chars after regex match; ljust(6) pads to standard OCC root width
    raw_symbol = f"{underlying.ljust(6)}{yymmdd}{option_type}{m.group(4)}"

    return {
        "underlying":      underlying,
        "expiry_yymmdd":   yymmdd,
        "expiry_yyyymmdd": "20" + yymmdd,
        "expiry":          expiry,
        "option_type":     option_type,
        "strike":          strike,
        "raw_symbol":      raw_symbol,
    }


# ---------------------------------------------------------------------------
# IBKRTrader
# ---------------------------------------------------------------------------

class IBKRTrader:
    """IBKR paper trading client.

    Parameters
    ----------
    host : str
        IB Gateway / TWS hostname.
    port : int
        4002 = IB Gateway paper, 7497 = TWS paper.
    client_id : int
        Must differ from IBKRStreamer's client_id.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 4002,
                 client_id: int = 2):
        self._ib = IB()
        try:
            self._ib.connect(host, port, clientId=client_id)
        except Exception as exc:
            logger.error(
                "IBKRTrader: failed to connect to IB Gateway at %s:%s — %s",
                host, port, exc,
            )
            raise
        # Use live market data (type 1) — paid subscriptions should cover API access.
        # Fall back to type 3 (delayed) only if live data errors occur.
        self._ib.reqMarketDataType(3)  # DELAYED — Error 10089 with type=1
        # Model pricer uses raw ibapi (bypasses Error 10091) for option pricing
        self._model_pricer = IbkrModelPricer(host, port, client_id=client_id + 48)

        # Cache market prices from updatePortfolio events (pushed by IBKR every ~3 min).
        # Keyed by OCC stripped symbol (e.g. 'SYMBOL260515C00711000').
        self._portfolio_market_prices: dict[str, float] = {}
        self._ib.updatePortfolioEvent += self._on_portfolio_update

        logger.info(
            "IBKRTrader connected at %s:%s (clientId=%s, mktDataType=3/DELAYED)",
            host, port, client_id,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_contract(self, occ_symbol: str) -> Option:
        """Build and qualify an IBKR Option contract from an OCC symbol."""
        parsed = _parse_occ(occ_symbol)
        contract = Option(
            symbol=parsed["underlying"],
            lastTradeDateOrContractMonth=parsed["expiry_yyyymmdd"],
            strike=parsed["strike"],
            right=parsed["option_type"],
            exchange="SMART",
            currency="USD",
            multiplier="100",
            tradingClass=parsed["underlying"],
        )
        self._ib.qualifyContracts(contract)
        return contract

    # ------------------------------------------------------------------
    # Public interface (matches AlpacaTrader)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Portfolio price cache (updatePortfolio fallback)
    # ------------------------------------------------------------------

    def _on_portfolio_update(self, item):
        """Cache marketPrice from IBKR's periodic updatePortfolio pushes.

        IBKR sends these every ~3 minutes for positions in the account.
        The cached price serves as a reliable fallback when the model pricer
        and ib_insync snapshot both fail to return a price.
        """
        local_sym = getattr(item.contract, 'localSymbol', None)
        if local_sym and item.marketPrice is not None:
            stripped = _strip_occ(local_sym)
            self._portfolio_market_prices[stripped] = item.marketPrice
            logger.debug(
                "Cached portfolio marketPrice for %s: %.4f", stripped, item.marketPrice
            )

    # ------------------------------------------------------------------
    # Option pricing
    # ------------------------------------------------------------------

    def get_option_mid_price(self, occ_symbol: str) -> float | None:
        """Fetch the mid-price of an option.

        Attempts (in order):
        1. ibapi modelGreeks (thread-safe, no ib.sleep needed).
        2. ib_insync snapshot (fallback, requires ib.sleep).
        3. Cached marketPrice from updatePortfolio events.

        Returns mid-price or None.
        """
        parsed = _parse_occ(occ_symbol)
        stripped = _strip_occ(occ_symbol)

        # --- Attempt 1: raw ibapi modelGreeks (thread-safe, no ib.sleep) ---
        # This MUST go first because get_option_mid_price() is called from
        # inside the pendingTickersEvent callback chain. Using ib.sleep()
        # inside that chain causes RuntimeError: cannot enter context
        # (contextvars.Context.run() is not patched by nest_asyncio).
        # The ibapi model pricer uses threading.Event.wait() instead,
        # which is safe from any asyncio callback context.
        try:
            result = self._model_pricer.get_option_price(
                symbol=parsed["underlying"],
                expiry_yyyymmdd=parsed["expiry_yyyymmdd"],
                strike=parsed["strike"],
                right=parsed["option_type"],
            )
            # Retry once if model pricer returned None (transient ibapi timeout)
            if result is None:
                time.sleep(0.5)
                result = self._model_pricer.get_option_price(
                    symbol=parsed["underlying"],
                    expiry_yyyymmdd=parsed["expiry_yyyymmdd"],
                    strike=parsed["strike"],
                    right=parsed["option_type"],
                )
            if result and result["mid"] is not None and result["mid"] > 0:
                logger.info(
                    "Option price for %s via modelGreeks: mid=%.4f (source=%s, "
                    "bid=%s, ask=%s, model=%.4f, iv=%.4f, delta=%.4f)",
                    _strip_occ(occ_symbol), result["mid"], result["source"],
                    result["bid"], result["ask"], result["model"] or 0,
                    result["iv"] or 0, result["delta"] or 0,
                )
                return result["mid"]
        except Exception as exc:
            logger.warning("Model pricer failed for %s: %s", occ_symbol, exc)

        # --- Attempt 2: ib_insync snapshot (fallback, requires ib.sleep) ---
        # Only used if model pricer failed. Safe when called outside the
        # pendingTickersEvent callback (e.g. from reconcile or manual calls).
        # If called from inside the callback, ib.sleep() may trigger the
        # contextvars crash — but this is a last resort anyway.
        contract = None
        try:
            contract = self._get_contract(occ_symbol)
            ticker = self._ib.reqMktData(contract, "", snapshot=True)
            self._ib.sleep(_SNAPSHOT_WAIT_S)
            if ticker.bid is not None and ticker.ask is not None:
                if ticker.bid > 0 and ticker.ask > 0:
                    return (ticker.bid + ticker.ask) / 2.0
            if ticker.last is not None and ticker.last > 0:
                return ticker.last
        except Exception as exc:
            logger.debug("ib_insync quote failed for %s: %s", occ_symbol, exc)
        finally:
            if contract is not None:
                try:
                    self._ib.cancelMktData(contract)
                except Exception:
                    pass

        # --- Attempt 3: Cached marketPrice from updatePortfolio events ---
        # IBKR pushes these every ~3 min for open positions. This is the
        # last resort when both model pricer and ib_insync snapshot fail.
        cached = self._portfolio_market_prices.get(stripped)
        if cached is not None and cached > 0:
            logger.info(
                "Option price for %s via portfolio cache: mid=%.4f",
                stripped, cached,
            )
            return cached

        logger.warning(
            "All pricing attempts failed for %s (modelGreeks=None, snapshot=None, cache=%s)",
            stripped, "empty" if not cached else f"{cached:.4f}",
        )
        return None

    def buy_option(self, occ_symbol: str, qty: int) -> str:
        """Place a market BUY for qty contracts. Returns IBKR order ID string."""
        contract = self._get_contract(occ_symbol)
        order = MarketOrder("BUY", qty)
        trade = self._ib.placeOrder(contract, order)
        self._ib.sleep(1)
        logger.info(
            "BUY %dx %s — orderId=%s", qty, _strip_occ(occ_symbol), trade.order.orderId
        )
        return str(trade.order.orderId)

    def sell_option(self, occ_symbol: str, qty: int) -> str:
        """Place a market SELL to close qty contracts. Returns IBKR order ID string."""
        contract = self._get_contract(occ_symbol)
        order = MarketOrder("SELL", qty)
        trade = self._ib.placeOrder(contract, order)
        self._ib.sleep(1)
        logger.info(
            "SELL %dx %s — orderId=%s", qty, _strip_occ(occ_symbol), trade.order.orderId
        )
        return str(trade.order.orderId)

    def buy_equity(self, symbol: str, qty: int, signal: int) -> str:
        """Place a market BUY for qty shares. Returns IBKR order ID string.

        The *signal* parameter indicates direction (+1 long, -1 short).
        For the initial implementation only long (BUY) is supported.
        """
        from ib_insync import Stock
        contract = Stock(symbol, "SMART", "USD")
        self._ib.qualifyContracts(contract)
        action = "BUY" if signal >= 0 else "SELL"
        order = MarketOrder(action, qty)
        trade = self._ib.placeOrder(contract, order)
        self._ib.sleep(1)
        logger.info("BUY EQUITY %dx %s — orderId=%s", qty, symbol, trade.order.orderId)
        return str(trade.order.orderId)

    def sell_equity(self, symbol: str, qty: int) -> str:
        """Place a market SELL to close qty shares. Returns IBKR order ID string."""
        from ib_insync import Stock
        contract = Stock(symbol, "SMART", "USD")
        self._ib.qualifyContracts(contract)
        order = MarketOrder("SELL", qty)
        trade = self._ib.placeOrder(contract, order)
        self._ib.sleep(1)
        logger.info("SELL EQUITY %dx %s — orderId=%s", qty, symbol, trade.order.orderId)
        return str(trade.order.orderId)

    def get_order_status(self, order_id: str) -> str:
        """Return the lowercase fill status for a previously placed order.

        Returns 'filled', 'submitted', 'presubmitted', etc., or 'unknown'
        if the order cannot be found.
        """
        try:
            for trade in self._ib.trades():
                if str(trade.order.orderId) == str(order_id):
                    return trade.orderStatus.status.lower()
        except Exception as exc:
            logger.warning("get_order_status: error querying order %s: %s", order_id, exc)
        return "unknown"

    def get_option_positions(self, underlying: str = "SYMBOL") -> list[dict]:
        """Return open SYMBOL option positions from the IBKR paper account.

        Each returned dict has the same keys as AlpacaTrader.get_option_positions:
        symbol, qty, avg_entry_price, current_price, side, underlying, expiry,
        option_type, strike, raw_symbol, entry_iv.

        Note: IBKR avgCost is reported per share (÷100 for per-contract price).
              current_price is not available from the positions snapshot;
              set to float("nan") as a sentinel — engine re-fetches via get_option_mid_price.
              entry_iv is not stored by IBKR; set to None.
        """
        positions = self._ib.positions()
        results = []
        for pos in positions:
            c = pos.contract
            if c.secType != "OPT" or c.symbol != underlying:
                continue
            # lastTradeDateOrContractMonth is YYYYMMDD (8 chars)
            exp_str = c.lastTradeDateOrContractMonth  # e.g. '20260228'
            yymmdd = exp_str[2:]                      # strip leading '20' → '260228'
            strike_int = int(c.strike * OCC_STRIKE_MULTIPLIER)
            raw_symbol = f"{underlying.ljust(6)}{yymmdd}{c.right}{strike_int:08d}"

            results.append({
                "symbol":          raw_symbol.replace(" ", ""),
                "qty":             int(pos.position),
                "avg_entry_price": float(pos.avgCost) / 100.0,  # per-share → per-contract
                "current_price":   float("nan"),
                "side":            "long" if pos.position > 0 else "short",
                "underlying":      underlying,
                "expiry":          datetime.strptime(exp_str, "%Y%m%d"),
                "option_type":     c.right,
                "strike":          float(c.strike),
                "raw_symbol":      raw_symbol,
                "entry_iv":        None,
            })
        return results

    def cancel_all_orders(self):
        """Cancel all pending orders (safety call at shutdown)."""
        self._ib.reqGlobalCancel()
        self._model_pricer.disconnect()
        logger.info("All pending IBKR orders cancelled")
