"""IBKR option pricing via raw ibapi — bypasses Error 10091.

Uses ibapi 10.46.1 directly (not ib_insync) to request modelGreeks
and delayed bid/ask (tickTypes 80/81/83) which are available WITHOUT
a live market data API subscription.

This module is a standalone helper — it opens its own ibapi socket
connection with a unique clientId so it doesn't conflict with the
ib_insync connections used by IBKRStreamer and IBKRTrader.
"""

import logging
import threading
import time
from ibapi.client import EClient
from ibapi.contract import Contract as IBContract
from ibapi.wrapper import EWrapper

logger = logging.getLogger(__name__)

# tickType constants from IBKR docs
TICK_BID_GREEKS = 80       # Bid-side option computation
TICK_ASK_GREEKS = 81       # Ask-side option computation
TICK_LAST_GREEKS = 82      # Last-side option computation
TICK_MODEL_GREEKS = 83     # Model option computation (theoretical)


class _ModelPricerApp(EWrapper, EClient):
    """Raw ibapi app that fetches option model prices via reqMktData."""

    def __init__(self):
        EClient.__init__(self, self)
        self._lock = threading.Lock()
        self._bid_price = None
        self._ask_price = None
        self._model_price = None
        self._model_iv = None
        self._model_delta = None
        self._und_price = None
        self._done = threading.Event()
        self._ready = threading.Event()
        self._connected = False

    # --- EWrapper callbacks ---

    def error(self, reqId, errorTime, errorCode, errorString, advancedOrderRejectJson=''):
        # 10091 = subscription required (expected, we still get model data)
        # 10167 = showing delayed data (expected for type=3)
        # 2104/2106/2158 = connection OK messages
        if errorCode in (10167, 2104, 2106, 2158):
            return
        if errorCode == 10091:
            # Expected — we still get tickTypes 80/81/83 with delayed data
            logger.debug("Error 10091 (expected): %s", errorString)
            return
        logger.warning("ibapi error %d: %s (reqId=%s)", errorCode, errorString, reqId)

    def tickOptionComputation(self, reqId, tickType, tickAttrib,
                              impliedVol, delta, optPrice, pvDividend,
                              gamma, vega, theta, undPrice):
        with self._lock:
            if tickType == TICK_BID_GREEKS and optPrice is not None and optPrice > 0:
                self._bid_price = optPrice
            elif tickType == TICK_ASK_GREEKS and optPrice is not None and optPrice > 0:
                self._ask_price = optPrice
            elif tickType == TICK_MODEL_GREEKS and optPrice is not None and optPrice > 0:
                self._model_price = optPrice
                self._model_iv = impliedVol
                self._model_delta = delta
                self._und_price = undPrice

        # Signal done once we have model data (always arrives) + at least one of bid/ask
        if self._model_price is not None:
            self._done.set()

    def connectionClosed(self):
        self._connected = False


class IbkrModelPricer:
    """Fetch option prices via ibapi modelGreeks — works without subscription.

    Opens a single persistent connection to IB Gateway and reuses it
    for all pricing requests. Thread-safe.

    Usage:
        pricer = IbkrModelPricer(host='127.0.0.1', port=4002, client_id=50)
        try:
            price = pricer.get_option_price('SYMBOL', '20260514', 714.0, 'P')
            # price = {'mid': 3.72, 'bid': 4.02, 'ask': 4.04,
            #          'model': 4.70, 'iv': 0.238, 'delta': -0.40}
        finally:
            pricer.disconnect()
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 4002,
                 client_id: int = 50):
        self._host = host
        self._port = port
        self._client_id = client_id
        self._app: _ModelPricerApp | None = None
        self._lock = threading.Lock()

    def _ensure_connected(self):
        """Lazily connect if not already connected."""
        if self._app is not None and self._app._connected:
            return
        with self._lock:
            if self._app is not None and self._app._connected:
                return
            # Clean up old connection
            if self._app is not None:
                try:
                    self._app.disconnect()
                except Exception:
                    pass

            app = _ModelPricerApp()
            app.connect(self._host, self._port, clientId=self._client_id)
            t = threading.Thread(target=app.run, daemon=True)
            t.start()
            time.sleep(1)  # let connection establish
            app._connected = True
            app.reqMarketDataType(3)  # delayed (works for modelGreeks)
            self._app = app
            logger.info("IbkrModelPricer connected to %s:%s (clientId=%d, type=3)",
                        self._host, self._port, self._client_id)

    def get_option_price(self, symbol: str, expiry_yyyymmdd: str,
                         strike: float, right: str,
                         timeout: float = 4.0) -> dict | None:
        """Get option price via modelGreeks.

        Returns dict with keys: mid, bid, ask, model, iv, delta, und_price
        or None if no data available within timeout.
        """
        self._ensure_connected()
        app = self._app
        if app is None:
            return None

        # Build contract
        opt = IBContract()
        opt.symbol = symbol
        opt.secType = "OPT"
        opt.lastTradeDateOrContractMonth = expiry_yyyymmdd
        opt.strike = strike
        opt.right = right
        opt.multiplier = "100"
        opt.exchange = "SMART"
        opt.currency = "USD"

        # Reset state
        with app._lock:
            app._bid_price = None
            app._ask_price = None
            app._model_price = None
            app._model_iv = None
            app._model_delta = None
            app._und_price = None
            app._done.clear()

        # Request with genericTickList='106' for model option computation
        app.reqMktData(1, opt, "106", False, False, [])

        # Wait for data
        got_data = app._done.wait(timeout=timeout)

        # Cancel subscription
        try:
            app.cancelMktData(1)
        except Exception:
            pass

        with app._lock:
            bid = app._bid_price
            ask = app._ask_price
            model = app._model_price
            iv = app._model_iv
            delta = app._model_delta
            und = app._und_price

        if not got_data and model is None:
            logger.warning("No model price for %s %s %s%.0f within %.1fs",
                           symbol, expiry_yyyymmdd, right, strike, timeout)
            return None

        # Calculate mid: prefer delayed bid/ask, fall back to model price
        if bid is not None and ask is not None and bid > 0 and ask > 0:
            mid = (bid + ask) / 2.0
            source = "delayed_bid_ask"
        else:
            mid = model
            source = "model_theoretical"

        logger.debug(
            "Option price %s %s %s%.0f: mid=%.4f (%s) bid=%s ask=%s model=%.4f iv=%.4f delta=%.4f",
            symbol, expiry_yyyymmdd, right, strike,
            mid, source, bid, ask, model, iv or 0, delta or 0,
        )

        return {
            "mid": mid,
            "bid": bid,
            "ask": ask,
            "model": model,
            "iv": iv,
            "delta": delta,
            "und_price": und,
            "source": source,
        }

    def get_option_mid(self, symbol: str, expiry_yyyymmdd: str,
                       strike: float, right: str) -> float | None:
        """Convenience: return just the mid price, or None."""
        result = self.get_option_price(symbol, expiry_yyyymmdd, strike, right)
        if result:
            return result["mid"]
        return None

    def disconnect(self):
        """Clean up the ibapi connection."""
        if self._app is not None:
            try:
                self._app.disconnect()
            except Exception:
                pass
            self._app = None
