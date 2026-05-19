"""
IBKR Support's own sample code — modified to test type=1 (LIVE) vs type=3 (DELAYED)
Source: https://github.com/awiseib/Python-testers/blob/main/Live%20Data/LiveData-top.py
"""
from decimal import Decimal
from ibapi.client import *
from ibapi.common import TickAttrib, TickerId
from ibapi.wrapper import *
from ibapi.ticktype import TickTypeEnum
import time, threading
from ibapi.contract import ComboLeg

class TestApp(EClient, EWrapper):
    def __init__(self):
        EClient.__init__(self, self)
        self._phase = 0  # 0=type1, 1=type3
        self._tick_count = 0

    def tickPrice(self, reqId: TickerId, tickType: TickerId, price: float, attrib: TickAttrib):
        self._tick_count += 1
        print(f"tickPrice. reqId: {reqId}, tickType: {TickTypeEnum.toStr(tickType)}, price: {price}, attrib: {attrib}")

    def tickSize(self, reqId: TickerId, tickType: TickType, size: Decimal):
        self._tick_count += 1
        print(f"tickSize. reqId:{reqId}, tickType:{TickTypeEnum.toStr(tickType)}, size:{size}")

    def tickReqParams(self, tickerId: TickerId, minTick: float, bboExchange: str, snapshotPermissions: TickerId):
        print(tickerId, minTick, bboExchange, snapshotPermissions)

    def tickGeneric(self, reqId: TickerId, tickType: TickType, value: float):
        self._tick_count += 1
        print(f"tickGeneric:  reqId: {reqId}, tickType: {TickTypeEnum.toStr(tickType)}, value: {value}")

    def tickString(self, reqId: TickerId, tickType: TickType, value: str):
        self._tick_count += 1
        print("tickString: ", reqId, TickTypeEnum.toStr(tickType), value)

    def tickSnapshotEnd(self, reqId: int):
        print(f"tickSnapshotEnd. reqId:{reqId}")

    def error(self, reqId: TickerId, errorTime: int, errorCode: int, errorString: str, advancedOrderRejectJson=""):
        if errorCode in (2104, 2106, 2158):
            print(f"[FARM OK] {errorString}")
        elif errorCode in (10089, 10090, 10091, 354, 10167, 10168):
            print(f"[SUBSCRIPTION ERROR] Error Code: {errorCode}, Message: {errorString}")
        else:
            print(f"Error. Time: {errorTime}, Code: {errorCode}, Message: {errorString}")


app = TestApp()
# Using paper trading Gateway port 4002 (their sample uses 7496=TWS live)
app.connect("127.0.0.1", 4002, 10)
time.sleep(1)
threading.Thread(target=app.run, daemon=True).start()
time.sleep(2)

# === TEST 1: Their sample uses type=3 (DELAYED). Let's verify it works. ===
print("\n" + "=" * 60)
print("TEST 1: Support's sample — reqMarketDataType(3) DELAYED")
print("  Contract: conId=265598 (SYMBOL) via SMART, genericTickList=236")
print("=" * 60)
app._tick_count = 0
app.reqMarketDataType(3)

contract = Contract()
contract.conId = 265598
contract.exchange = "SMART"

app.reqMktData(
    reqId=101,
    contract=contract,
    genericTickList="236",
    snapshot=0,
    regulatorySnapshot=0,
    mktDataOptions=[]
)
time.sleep(10)
app.cancelMktData(101)
print(f"[TYPE=3 RESULT] Ticks received: {app._tick_count}")

# === TEST 2: Now try type=1 (LIVE) — this is what we NEED ===
print("\n" + "=" * 60)
print("TEST 2: Same contract — reqMarketDataType(1) LIVE")
print("=" * 60)
app._tick_count = 0
app.reqMarketDataType(1)

app.reqMktData(
    reqId=102,
    contract=contract,
    genericTickList="236",
    snapshot=0,
    regulatorySnapshot=0,
    mktDataOptions=[]
)
time.sleep(10)
app.cancelMktData(102)
print(f"[TYPE=1 RESULT] Ticks received: {app._tick_count}")

print("\n" + "=" * 60)
print("CONCLUSION")
print("=" * 60)
print("If type=3 works but type=1 gives Error 10089,")
print("the paid subscriptions do NOT include API access.")

app.disconnect()
