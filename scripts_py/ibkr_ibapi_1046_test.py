"""
IBKR Market Data Test — ibapi 10.46
====================================
Minimal test script using the latest official IBKR API (ibapi 10.46.1)
to demonstrate Error 10089 ("subscription for API") on paid subscriptions.

Usage:
    python scripts_py/ibkr_ibapi_1046_test.py [--port 4002] [--client-id 50]

Requires: ibapi 10.46.1 (installed from TWS API zip, NOT PyPI)
    pip install /path/to/twsapi/source/pythonclient

Connects to IB Gateway on paper port 4002 by default.
Tests reqMktData with type=1 (LIVE) on SYMBOL equity.
"""

import time
import threading
import argparse
import sys

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract


class MarketDataTestApp(EClient, EWrapper):
    """Minimal test app for ibapi 10.46 market data subscription verification."""

    def __init__(self):
        EClient.__init__(self, self)
        self._connected = threading.Event()
        self._farms_ready = threading.Event()
        self._done = threading.Event()
        self._tick_count = 0
        self._error_codes = []
        self._farm_connections = 0

    def nextValidId(self, orderId: int):
        """Called when connection is established."""
        print(f"[CONNECTED] nextValidId={orderId} serverVersion={self.serverVersion()}")
        print(f"[INFO] ibapi version: 10.46.1")
        self._connected.set()

    def error(self, reqId: int, errorTime: int, errorCode: int, errorString: str,
              advancedOrderRejectJson=""):
        """Error handler — ibapi 10.46 signature includes errorTime."""
        error_time_str = ""
        if errorTime:
            try:
                error_time_str = time.strftime("%H:%M:%S", time.localtime(errorTime / 1000))
            except:
                error_time_str = str(errorTime)

        # Data farm connection OK messages
        if errorCode in (2104, 2106, 2158):
            self._farm_connections += 1
            print(f"  [FARM] code={errorCode} msg={errorString}")
            # Wait for at least usfarm + ushmds + secdefil (3 farm connections)
            if self._farm_connections >= 3:
                self._farms_ready.set()
            return

        # Subscription-related errors — what we're testing for
        if errorCode in (10089, 10090, 10091, 354, 420, 10167, 10168, 10186):
            print(f"  [ERROR] reqId={reqId} time={error_time_str} code={errorCode} msg={errorString}")
            self._error_codes.append(errorCode)
            return

        # Warning/info messages
        if errorCode in (2100, 2103, 2105, 2107, 2108, 2119, 2157):
            print(f"  [INFO] code={errorCode} msg={errorString}")
            return

        # Everything else
        print(f"  [MSG] reqId={reqId} time={error_time_str} code={errorCode} msg={errorString}")
        if errorCode >= 1000 and errorCode not in (1100, 1101, 1102, 1300, 2157):
            self._error_codes.append(errorCode)

    def tickPrice(self, reqId: int, tickType: int, price: float, attrib):
        tick_names = {
            1: "BID", 2: "ASK", 4: "LAST", 6: "HIGH", 7: "LOW",
            9: "CLOSE", 14: "OPEN"
        }
        name = tick_names.get(tickType, f"TYPE_{tickType}")
        self._tick_count += 1
        print(f"  [TICK] reqId={reqId} {name}={price}")

    def tickSize(self, reqId: int, tickType: int, size):
        self._tick_count += 1
        print(f"  [SIZE] reqId={reqId} type={tickType} size={size}")

    def tickGeneric(self, reqId: int, tickType: int, value: float):
        self._tick_count += 1

    def tickString(self, reqId: int, tickType: int, value: str):
        self._tick_count += 1


def wait_with_timeout(event, timeout, msg=""):
    """Wait for an event with a timeout and status dots."""
    for i in range(timeout):
        if event.is_set():
            return True
        if msg and i % 5 == 0:
            print(f"  ... waiting ({i}/{timeout}s) {msg}")
        time.sleep(1)
    return event.is_set()


def main():
    parser = argparse.ArgumentParser(description="IBKR ibapi 10.46 Market Data Test")
    parser.add_argument("--port", type=int, default=4002, help="IB Gateway port (default: 4002 paper)")
    parser.add_argument("--client-id", type=int, default=50, help="Client ID (default: 50)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host (default: 127.0.0.1)")
    args = parser.parse_args()

    print(f"IBKR Market Data Test — ibapi 10.46.1")
    print(f"Connecting to {args.host}:{args.port} (clientId={args.client_id})")
    print()

    app = MarketDataTestApp()
    app.connect(args.host, args.port, clientId=args.client_id)

    # Run the message loop in a background thread
    thread = threading.Thread(target=app.run, daemon=True)
    thread.start()

    # Wait for connection
    print("[STEP 1] Waiting for connection...")
    if not wait_with_timeout(app._connected, 15, "for nextValidId"):
        print("[FATAL] Timed out waiting for connection. Is IB Gateway running?")
        sys.exit(1)

    # Wait for data farms to connect
    print("\n[STEP 2] Waiting for data farm connections...")
    if not wait_with_timeout(app._farms_ready, 20, "for data farms"):
        print("[WARN] Data farms may not be fully connected. Proceeding anyway...")
    else:
        print("[OK] All data farms connected")

    # Small delay for stability
    time.sleep(2)

    # === TEST 1: LIVE ===
    print("\n" + "=" * 60)
    print("TEST 1: reqMarketDataType(1) — LIVE")
    print("=" * 60)
    app._tick_count = 0
    app._error_codes = []
    app.reqMarketDataType(1)

    qqq = Contract()
    qqq.symbol = "SYMBOL"
    qqq.secType = "STK"
    qqq.exchange = "SMART"
    qqq.primaryExchange = "NASDAQ"
    qqq.currency = "USD"

    print(f"[REQ] reqMktData(1): SYMBOL SMART/NASDAQ/USD")
    app.reqMktData(1, qqq, "", False, False, [])
    wait_with_timeout(threading.Event(), 15, "collecting type=1 data")
    app.cancelMktData(1)

    if app._tick_count > 0:
        print(f"[RESULT] ✅ Received {app._tick_count} ticks with type=1 (LIVE)")
    else:
        print(f"[RESULT] ❌ No ticks with type=1 (LIVE)")
        if app._error_codes:
            print(f"[ERRORS] {app._error_codes}")

    # === TEST 2: DELAYED ===
    print("\n" + "=" * 60)
    print("TEST 2: reqMarketDataType(3) — DELAYED")
    print("=" * 60)
    app._tick_count = 0
    app._error_codes = []
    app.reqMarketDataType(3)

    print(f"[REQ] reqMktData(2): SYMBOL SMART/NASDAQ/USD")
    app.reqMktData(2, qqq, "", False, False, [])
    wait_with_timeout(threading.Event(), 15, "collecting type=3 data")
    app.cancelMktData(2)

    if app._tick_count > 0:
        print(f"[RESULT] ✅ Received {app._tick_count} ticks with type=3 (DELAYED)")
    else:
        print(f"[RESULT] ❌ No ticks with type=3 (DELAYED)")
        if app._error_codes:
            print(f"[ERRORS] {app._error_codes}")

    # === TEST 3: FOREX (free, should always work) ===
    print("\n" + "=" * 60)
    print("TEST 3: EUR/USD Forex — FREE (no subscription needed)")
    print("=" * 60)
    app._tick_count = 0
    app._error_codes = []
    app.reqMarketDataType(1)  # Live — forex is free

    eur = Contract()
    eur.symbol = "EUR"
    eur.secType = "CASH"
    eur.exchange = "IDEALPRO"
    eur.currency = "USD"

    print(f"[REQ] reqMktData(3): EUR/USD IDEALPRO")
    app.reqMktData(3, eur, "", False, False, [])
    wait_with_timeout(threading.Event(), 10, "collecting forex data")
    app.cancelMktData(3)

    if app._tick_count > 0:
        print(f"[RESULT] ✅ Received {app._tick_count} forex ticks (free)")
    else:
        print(f"[RESULT] ❌ No forex ticks")

    # === SUMMARY ===
    print("\n" + "=" * 60)
    print("SUMMARY — ibapi 10.46.1")
    print("=" * 60)
    print("If SYMBOL type=1 shows Error 10089 but forex works,")
    print("the paid subscriptions are TWS-only, not API-enabled.")
    print()

    # Disconnect gracefully
    try:
        app.disconnect()
    except:
        pass
    time.sleep(1)
    print("[DISCONNECTED]")


if __name__ == "__main__":
    main()
