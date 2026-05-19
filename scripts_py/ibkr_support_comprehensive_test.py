"""
IBKR Comprehensive Market Data Test — ibapi 10.46.1
====================================================
Tests ALL data types (equity + options) across ALL market data modes.

Run during market hours:
    python scripts_py/ibkr_support_comprehensive_test.py
    python scripts_py/ibkr_support_comprehensive_test.py --port 4001  # live

This script uses ONLY the official ibapi 10.46.1 library.
No ib_insync, no third-party wrappers.

Expected results with paid subscriptions:
  - SYMBOL equity type=1 (LIVE):  ticks received
  - SYMBOL equity type=3 (DELAYED): ticks received
  - SYMBOL option type=1 (LIVE): option bid/ask + greeks
  - SYMBOL option type=3 (DELAYED): option bid/ask + greeks

If Error 10089/10091 appears, subscriptions are TWS-only, not API-enabled.
"""

import time
import threading
import argparse
import sys
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract


class ComprehensiveTestApp(EClient, EWrapper):
    """ibapi 10.46 test — all market data types in one run."""

    def __init__(self):
        EClient.__init__(self, self)
        self._connected = threading.Event()
        self._farms_ready = threading.Event()
        self._farm_count = 0
        # Per-test state
        self._tick_count = 0
        self._errors = []
        self._option_data = {}
        self._option_done = threading.Event()

    def nextValidId(self, orderId: int):
        print(f"  [CONNECTED] nextValidId={orderId} serverVersion={self.serverVersion()}")
        self._connected.set()

    def error(self, reqId, errorTime, errorCode, errorString, advancedOrderRejectJson=''):
        if errorCode in (2104, 2106, 2158):
            self._farm_count += 1
            print(f"  [FARM] {errorString}")
            if self._farm_count >= 3:
                self._farms_ready.set()
            return
        if errorCode in (10089, 10090, 10091):
            print(f"  ❌ [ERROR {errorCode}] {errorString}")
            self._errors.append(errorCode)
            return
        if errorCode in (10167, 10168):
            print(f"  [INFO] {errorString}")
            return
        if errorCode in (2100, 2103, 2105, 2107, 2108, 2119, 2157):
            print(f"  [INFO] {errorString}")
            return
        print(f"  [MSG] code={errorCode} msg={errorString}")

    def tickPrice(self, reqId, tickType, price, attrib):
        names = {1: "BID", 2: "ASK", 4: "LAST", 6: "HIGH", 7: "LOW", 9: "CLOSE", 14: "OPEN"}
        name = names.get(tickType, f"TYPE_{tickType}")
        if price > 0:
            self._tick_count += 1
            print(f"    [PRICE] {name} = {price}")

    def tickSize(self, reqId, tickType, size):
        if size > 0:
            self._tick_count += 1

    def tickGeneric(self, reqId, tickType, value):
        self._tick_count += 1

    def tickString(self, reqId, tickType, value):
        self._tick_count += 1

    def tickOptionComputation(self, reqId, tickType, tickAttrib,
                              impliedVol, delta, optPrice, pvDividend,
                              gamma, vega, theta, undPrice):
        labels = {80: "BID_GREEKS", 81: "ASK_GREEKS", 82: "LAST_GREEKS", 83: "MODEL_GREEKS"}
        label = labels.get(tickType, f"tickType_{tickType}")
        if optPrice is not None and optPrice > 0:
            self._option_data[label] = {
                "price": optPrice,
                "iv": impliedVol,
                "delta": delta,
                "gamma": gamma,
                "vega": vega,
                "theta": theta,
                "undPrice": undPrice,
            }
            print(f"    [GREEKS] {label}: price={optPrice:.4f} iv={impliedVol:.4f} "
                  f"delta={delta:.4f} gamma={gamma:.4f} und={undPrice:.2f}")
        if tickType == 83:  # model greeks always arrives last
            self._option_done.set()


def wait(seconds, msg=""):
    for i in range(seconds):
        if msg and i % 5 == 0:
            print(f"    ... {msg} ({i}/{seconds}s)")
        time.sleep(1)


def make_qqq():
    c = Contract()
    c.symbol = "SYMBOL"
    c.secType = "STK"
    c.exchange = "SMART"
    c.primaryExchange = "NASDAQ"
    c.currency = "USD"
    return c


def make_qqq_option(expiry="20260515", strike=714.0, right="P"):
    c = Contract()
    c.symbol = "SYMBOL"
    c.secType = "OPT"
    c.lastTradeDateOrContractMonth = expiry
    c.strike = strike
    c.right = right
    c.multiplier = "100"
    c.exchange = "SMART"
    c.currency = "USD"
    return c


def make_eurusd():
    c = Contract()
    c.symbol = "EUR"
    c.secType = "CASH"
    c.exchange = "IDEALPRO"
    c.currency = "USD"
    return c


def reset(app):
    app._tick_count = 0
    app._errors = []
    app._option_data = {}
    app._option_done.clear()


def print_result(test_name, app):
    ticks = app._tick_count
    errors = app._errors
    opt = app._option_data
    print(f"\n  ── {test_name} RESULT ──")
    print(f"  Ticks: {ticks}")
    if errors:
        print(f"  Errors: {errors}")
    if opt:
        for label, data in opt.items():
            print(f"  {label}: price={data['price']:.4f} delta={data['delta']:.4f}")
    if ticks > 0:
        print(f"  ✅ WORKING")
    elif opt:
        print(f"  ⚠️  Model data only (no live bid/ask)")
    else:
        print(f"  ❌ NO DATA")
    print()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4002, help="Gateway port (default: 4002 paper)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--client-id", type=int, default=99)
    args = parser.parse_args()

    print("=" * 70)
    print("IBKR Comprehensive Market Data Test — ibapi 10.46.1")
    print(f"Target: {args.host}:{args.port} (clientId={args.client_id})")
    print("=" * 70)

    app = ComprehensiveTestApp()
    app.connect(args.host, args.port, clientId=args.client_id)
    t = threading.Thread(target=app.run, daemon=True)
    t.start()

    print("\n[1/7] Connecting...")
    if not app._connected.wait(15):
        print("FATAL: connection timeout. Is IB Gateway running?")
        sys.exit(1)

    print("\n[2/7] Waiting for data farms...")
    if not app._farms_ready.wait(20):
        print("WARN: farms may not be ready, proceeding...")
    else:
        print("  All data farms connected")

    time.sleep(2)

    # ================================================================
    # TEST 1: SYMBOL Equity — type=1 LIVE
    # ================================================================
    print("\n" + "=" * 70)
    print("TEST 1: SYMBOL Equity — reqMarketDataType(1) LIVE")
    print("=" * 70)
    reset(app)
    app.reqMarketDataType(1)
    app.reqMktData(1, make_qqq(), "", False, False, [])
    wait(12, "collecting SYMBOL live ticks")
    app.cancelMktData(1)
    print_result("SYMBOL type=1", app)

    # ================================================================
    # TEST 2: SYMBOL Equity — type=3 DELAYED
    # ================================================================
    print("=" * 70)
    print("TEST 2: SYMBOL Equity — reqMarketDataType(3) DELAYED")
    print("=" * 70)
    reset(app)
    app.reqMarketDataType(3)
    app.reqMktData(2, make_qqq(), "", False, False, [])
    wait(12, "collecting SYMBOL delayed ticks")
    app.cancelMktData(2)
    print_result("SYMBOL type=3", app)

    # ================================================================
    # TEST 3: EUR/USD Forex — type=1 LIVE (free data, should always work)
    # ================================================================
    print("=" * 70)
    print("TEST 3: EUR/USD Forex — type=1 LIVE (free, no subscription)")
    print("=" * 70)
    reset(app)
    app.reqMarketDataType(1)
    app.reqMktData(3, make_eurusd(), "", False, False, [])
    wait(8, "collecting EUR/USD ticks")
    app.cancelMktData(3)
    print_result("EUR/USD type=1", app)

    # ================================================================
    # TEST 4: SYMBOL Option — type=1 LIVE
    # ================================================================
    print("=" * 70)
    print("TEST 4: SYMBOL Option (714P) — type=1 LIVE")
    print("=" * 70)
    reset(app)
    app.reqMarketDataType(1)
    app.reqMktData(4, make_qqq_option(strike=714.0, right="P"), "106", False, False, [])
    wait(10, "collecting SYMBOL option live data")
    app.cancelMktData(4)
    print_result("SYMBOL 714P type=1", app)

    # ================================================================
    # TEST 5: SYMBOL Option — type=3 DELAYED
    # ================================================================
    print("=" * 70)
    print("TEST 5: SYMBOL Option (714P) — type=3 DELAYED")
    print("=" * 70)
    reset(app)
    app.reqMarketDataType(3)
    app.reqMktData(5, make_qqq_option(strike=714.0, right="P"), "106", False, False, [])
    wait(10, "collecting SYMBOL option delayed data")
    app.cancelMktData(5)
    print_result("SYMBOL 714P type=3", app)

    # ================================================================
    # TEST 6: SYMBOL Option — type=3, NO genericTickList (plain bid/ask)
    # ================================================================
    print("=" * 70)
    print("TEST 6: SYMBOL Option (714P) — type=3, plain bid/ask (no genericTickList)")
    print("=" * 70)
    reset(app)
    app.reqMarketDataType(3)
    app.reqMktData(6, make_qqq_option(strike=714.0, right="P"), "", False, False, [])
    wait(10, "collecting SYMBOL option plain data")
    app.cancelMktData(6)
    print_result("SYMBOL 714P type=3 plain", app)

    # ================================================================
    # TEST 7: SYMBOL Option — type=3, different strike (CALL)
    # ================================================================
    print("=" * 70)
    print("TEST 7: SYMBOL Option (715C) — type=3 DELAYED with genericTickList=106")
    print("=" * 70)
    reset(app)
    app.reqMarketDataType(3)
    app.reqMktData(7, make_qqq_option(strike=715.0, right="C"), "106", False, False, [])
    wait(10, "collecting SYMBOL 715C data")
    app.cancelMktData(7)
    print_result("SYMBOL 715C type=3", app)

    # ================================================================
    # SUMMARY
    # ================================================================
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print("""
Expected with WORKING API subscriptions:
  TEST 1 (SYMBOL equity, live):    ticks + no errors
  TEST 2 (SYMBOL equity, delayed): ticks + no errors
  TEST 3 (EUR/USD forex):       ticks (always works — free data)
  TEST 4 (SYMBOL option, live):    option bid/ask + greeks
  TEST 5 (SYMBOL option, delayed): option bid/ask + greeks
  TEST 6 (SYMBOL option, plain):   option bid/ask
  TEST 7 (SYMBOL 715C, delayed):   option bid/ask + greeks

If TEST 3 works but TEST 1/4 show Error 10089/10091:
  → Paid subscriptions are TWS-only, NOT enabled for API access.
  → Forex is free (no subscription needed) so it always works.

If TEST 5/7 show Error 10091 but still return MODEL_GREEKS:
  → Subscription blocks live bid/ask but model data bypasses it.
  → This is the current workaround.
""")

    try:
        app.disconnect()
    except:
        pass
    time.sleep(1)
    print("[DISCONNECTED]")


if __name__ == "__main__":
    main()
