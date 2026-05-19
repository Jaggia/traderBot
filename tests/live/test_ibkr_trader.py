"""
Tests for src/live/ibkr_trader.py.

Mocks ib_insync so no real IB Gateway connection is made.
Covers:
  - IBKRTrader.__init__:      connection failure propagates with logged error
  - _parse_occ():             OCC symbol parsing → IBKR contract fields
  - get_option_mid_price():   returns (bid+ask)/2 when both > 0; None otherwise;
                              cancelMktData called in finally (even on exception)
  - buy_option():             placeOrder called with MarketOrder("BUY", qty)
  - sell_option():            placeOrder called with MarketOrder("SELL", qty)
  - get_order_status():       scans ib.trades() for matching orderId
  - get_option_positions():   filters to OPT+SYMBOL, divides avgCost by 100,
                              current_price=NaN sentinel, entry_iv=None
  - cancel_all_orders():      calls ib.reqGlobalCancel()
"""

import sys
from datetime import datetime
from unittest.mock import MagicMock, patch, call

import pytest

# Mock ib_insync at module level so the source file's top-level
# `from ib_insync import IB, Option, MarketOrder` never hits the real package.
sys.modules.setdefault("ib_insync", MagicMock())


# ---------------------------------------------------------------------------
# Module-level mock helper
# ---------------------------------------------------------------------------

def _make_trader(ib_instance=None):
    """Instantiate IBKRTrader with a fully mocked ib_insync.IB."""
    mock_ib = ib_instance or MagicMock()

    with patch.dict("sys.modules", {
        "ib_insync": MagicMock(IB=lambda: mock_ib, Option=MagicMock, MarketOrder=MagicMock),
    }):
        # Also patch IB at the module import level
        with patch("src.live.ibkr_trader.IB", return_value=mock_ib), \
             patch("src.live.ibkr_trader.Option"), \
             patch("src.live.ibkr_trader.MarketOrder"):
            from src.live.ibkr_trader import IBKRTrader
            trader = IBKRTrader(host="127.0.0.1", port=4002, client_id=2)

    trader._ib = mock_ib
    return trader


# ---------------------------------------------------------------------------
# IBKRTrader.__init__
# ---------------------------------------------------------------------------

class TestIBKRTraderInit:
    def test_connect_failure_propagates(self):
        """IBKRTrader.__init__ must log an error and re-raise on connection failure."""
        mock_ib = MagicMock()
        mock_ib.connect.side_effect = ConnectionRefusedError("IB Gateway not running")

        with patch("src.live.ibkr_trader.IB", return_value=mock_ib), \
             patch("src.live.ibkr_trader.Option"), \
             patch("src.live.ibkr_trader.MarketOrder"):
            from src.live.ibkr_trader import IBKRTrader
            with pytest.raises(ConnectionRefusedError):
                IBKRTrader(host="127.0.0.1", port=4002, client_id=2)


# ---------------------------------------------------------------------------
# _parse_occ
# ---------------------------------------------------------------------------

class TestParseOcc:
    def test_call_option_padded(self):
        from src.live.ibkr_trader import _parse_occ
        result = _parse_occ("SYMBOL   260228C00450000")
        assert result["underlying"] == "SYMBOL"
        assert result["expiry_yyyymmdd"] == "20260228"
        assert result["option_type"] == "C"
        assert result["strike"] == pytest.approx(450.0)
        assert result["expiry"] == datetime(2026, 2, 28)

    def test_put_option_stripped(self):
        from src.live.ibkr_trader import _parse_occ
        result = _parse_occ("SYMBOL260221P00400000")
        assert result["underlying"] == "SYMBOL"
        assert result["option_type"] == "P"
        assert result["strike"] == pytest.approx(400.0)
        assert result["expiry_yyyymmdd"] == "20260221"

    def test_fractional_strike(self):
        from src.live.ibkr_trader import _parse_occ
        result = _parse_occ("SYMBOL260221C00451500")
        assert result["strike"] == pytest.approx(451.5)

    def test_invalid_symbol_raises(self):
        from src.live.ibkr_trader import _parse_occ
        with pytest.raises(ValueError, match="Cannot parse"):
            _parse_occ("BADFORMAT")

    def test_expiry_yymmdd_field(self):
        from src.live.ibkr_trader import _parse_occ
        result = _parse_occ("SYMBOL260301C00500000")
        assert result["expiry_yymmdd"] == "260301"
        assert result["expiry_yyyymmdd"] == "20260301"

    def test_raw_symbol_padded(self):
        """raw_symbol should be reconstructed in padded (6-char root) OCC format."""
        from src.live.ibkr_trader import _parse_occ
        result = _parse_occ("SYMBOL260228C00450000")
        assert result["raw_symbol"] == "SYMBOL   260228C00450000"


# ---------------------------------------------------------------------------
# get_option_mid_price
# ---------------------------------------------------------------------------

class TestGetOptionMidPrice:
    def test_returns_midpoint_when_bid_and_ask_positive(self):
        mock_ib = MagicMock()
        ticker = MagicMock()
        ticker.bid = 1.20
        ticker.ask = 1.40
        mock_ib.reqMktData.return_value = ticker

        trader = _make_trader(mock_ib)
        price = trader.get_option_mid_price("SYMBOL   260228C00450000")
        assert price == pytest.approx(1.30)

    def test_returns_none_when_bid_zero(self):
        mock_ib = MagicMock()
        ticker = MagicMock()
        ticker.bid = 0.0
        ticker.ask = 1.50
        ticker.last = None
        mock_ib.reqMktData.return_value = ticker

        trader = _make_trader(mock_ib)
        assert trader.get_option_mid_price("SYMBOL   260228C00450000") is None

    def test_returns_none_when_ask_zero(self):
        mock_ib = MagicMock()
        ticker = MagicMock()
        ticker.bid = 1.20
        ticker.ask = 0.0
        ticker.last = None
        mock_ib.reqMktData.return_value = ticker

        trader = _make_trader(mock_ib)
        assert trader.get_option_mid_price("SYMBOL   260228C00450000") is None

    def test_falls_back_to_last_when_bid_ask_unavailable(self):
        mock_ib = MagicMock()
        ticker = MagicMock()
        ticker.bid = 0.0
        ticker.ask = 0.0
        ticker.last = 1.35
        mock_ib.reqMktData.return_value = ticker

        trader = _make_trader(mock_ib)
        price = trader.get_option_mid_price("SYMBOL   260228C00450000")
        assert price == pytest.approx(1.35)

    def test_returns_none_on_exception(self):
        mock_ib = MagicMock()
        mock_ib.reqMktData.side_effect = RuntimeError("IB error")

        trader = _make_trader(mock_ib)
        assert trader.get_option_mid_price("SYMBOL   260228C00450000") is None

    def test_cancel_mkt_data_called_after_fetch(self):
        mock_ib = MagicMock()
        ticker = MagicMock()
        ticker.bid = 1.0
        ticker.ask = 1.2
        mock_ib.reqMktData.return_value = ticker

        trader = _make_trader(mock_ib)
        trader.get_option_mid_price("SYMBOL   260228C00450000")
        mock_ib.cancelMktData.assert_called_once()

    def test_cancel_mkt_data_called_even_on_exception(self):
        """cancelMktData must be called in finally even when reqMktData raises."""
        mock_ib = MagicMock()
        mock_ib.reqMktData.side_effect = RuntimeError("IB error")

        trader = _make_trader(mock_ib)
        result = trader.get_option_mid_price("SYMBOL   260228C00450000")

        assert result is None
        mock_ib.cancelMktData.assert_called_once()


# ---------------------------------------------------------------------------
# buy_option
# ---------------------------------------------------------------------------

class TestBuyOption:
    def test_returns_order_id_as_string(self):
        mock_ib = MagicMock()
        trade = MagicMock()
        trade.order.orderId = 42
        mock_ib.placeOrder.return_value = trade

        trader = _make_trader(mock_ib)
        result = trader.buy_option("SYMBOL   260228C00450000", qty=2)
        assert result == "42"

    def test_place_order_called_with_buy(self):
        from src.live.ibkr_trader import IBKRTrader
        mock_ib = MagicMock()
        trade = MagicMock()
        trade.order.orderId = 1
        mock_ib.placeOrder.return_value = trade

        trader = _make_trader(mock_ib)

        with patch("src.live.ibkr_trader.MarketOrder") as mock_mo:
            trader.buy_option("SYMBOL260228C00450000", qty=3)
            mock_mo.assert_called_once_with("BUY", 3)

    def test_qty_forwarded(self):
        mock_ib = MagicMock()
        trade = MagicMock()
        trade.order.orderId = 5
        mock_ib.placeOrder.return_value = trade

        trader = _make_trader(mock_ib)
        with patch("src.live.ibkr_trader.MarketOrder") as mock_mo:
            trader.buy_option("SYMBOL260228C00450000", qty=7)
            mock_mo.assert_called_once_with("BUY", 7)


# ---------------------------------------------------------------------------
# sell_option
# ---------------------------------------------------------------------------

class TestSellOption:
    def test_returns_order_id_as_string(self):
        mock_ib = MagicMock()
        trade = MagicMock()
        trade.order.orderId = 99
        mock_ib.placeOrder.return_value = trade

        trader = _make_trader(mock_ib)
        result = trader.sell_option("SYMBOL260228C00450000", qty=1)
        assert result == "99"

    def test_place_order_called_with_sell(self):
        mock_ib = MagicMock()
        trade = MagicMock()
        trade.order.orderId = 10
        mock_ib.placeOrder.return_value = trade

        trader = _make_trader(mock_ib)
        with patch("src.live.ibkr_trader.MarketOrder") as mock_mo:
            trader.sell_option("SYMBOL260228C00450000", qty=2)
            mock_mo.assert_called_once_with("SELL", 2)


# ---------------------------------------------------------------------------
# get_order_status
# ---------------------------------------------------------------------------

class TestGetOrderStatus:
    def test_returns_filled_for_matching_order(self):
        mock_ib = MagicMock()
        trade = MagicMock()
        trade.order.orderId = 42
        trade.orderStatus.status = "Filled"
        mock_ib.trades.return_value = [trade]

        trader = _make_trader(mock_ib)
        assert trader.get_order_status("42") == "filled"

    def test_returns_unknown_when_order_not_found(self):
        mock_ib = MagicMock()
        mock_ib.trades.return_value = []

        trader = _make_trader(mock_ib)
        assert trader.get_order_status("999") == "unknown"

    def test_returns_unknown_on_exception(self):
        mock_ib = MagicMock()
        mock_ib.trades.side_effect = RuntimeError("IB error")

        trader = _make_trader(mock_ib)
        assert trader.get_order_status("1") == "unknown"

    def test_status_is_lowercased(self):
        mock_ib = MagicMock()
        trade = MagicMock()
        trade.order.orderId = 7
        trade.orderStatus.status = "PreSubmitted"
        mock_ib.trades.return_value = [trade]

        trader = _make_trader(mock_ib)
        assert trader.get_order_status("7") == "presubmitted"


# ---------------------------------------------------------------------------
# get_option_positions
# ---------------------------------------------------------------------------

class TestGetOptionPositions:
    def _make_pos(self, secType="OPT", symbol="SYMBOL",
                  lastTradeDate="20260228", strike=450.0,
                  right="C", position=2, avgCost=130.0):
        pos = MagicMock()
        pos.contract.secType = secType
        pos.contract.symbol = symbol
        pos.contract.lastTradeDateOrContractMonth = lastTradeDate
        pos.contract.strike = strike
        pos.contract.right = right
        pos.position = position
        pos.avgCost = avgCost
        return pos

    def test_filters_to_opt_and_underlying(self):
        mock_ib = MagicMock()
        mock_ib.positions.return_value = [
            self._make_pos(secType="OPT", symbol="SYMBOL"),
            self._make_pos(secType="STK", symbol="SYMBOL"),  # equity — filtered out
            self._make_pos(secType="OPT", symbol="SYMBOL"),  # wrong underlying
        ]
        trader = _make_trader(mock_ib)
        result = trader.get_option_positions("SYMBOL")
        assert len(result) == 1

    def test_avg_cost_divided_by_100(self):
        """IBKR avgCost is per-share; divide by 100 for per-contract price."""
        mock_ib = MagicMock()
        mock_ib.positions.return_value = [self._make_pos(avgCost=130.0)]
        trader = _make_trader(mock_ib)
        result = trader.get_option_positions("SYMBOL")
        assert result[0]["avg_entry_price"] == pytest.approx(1.30)

    def test_entry_iv_is_none(self):
        """entry_iv is not stored by IBKR — must always be None."""
        mock_ib = MagicMock()
        mock_ib.positions.return_value = [self._make_pos()]
        trader = _make_trader(mock_ib)
        result = trader.get_option_positions("SYMBOL")
        assert result[0]["entry_iv"] is None

    def test_option_type_and_strike_parsed(self):
        mock_ib = MagicMock()
        mock_ib.positions.return_value = [
            self._make_pos(lastTradeDate="20260221", strike=451.0, right="P")
        ]
        trader = _make_trader(mock_ib)
        result = trader.get_option_positions("SYMBOL")
        assert result[0]["option_type"] == "P"
        assert result[0]["strike"] == pytest.approx(451.0)
        assert result[0]["expiry"] == datetime(2026, 2, 21)

    def test_empty_positions_returns_empty_list(self):
        mock_ib = MagicMock()
        mock_ib.positions.return_value = []
        trader = _make_trader(mock_ib)
        assert trader.get_option_positions("SYMBOL") == []

    def test_raw_symbol_in_padded_occ_format(self):
        """raw_symbol must be padded OCC format: root(6) + YYMMDD + right + strike*1000(8d)."""
        mock_ib = MagicMock()
        mock_ib.positions.return_value = [
            self._make_pos(lastTradeDate="20260228", strike=450.0, right="C")
        ]
        trader = _make_trader(mock_ib)
        result = trader.get_option_positions("SYMBOL")
        assert result[0]["raw_symbol"] == "SYMBOL   260228C00450000"

    def test_current_price_is_nan(self):
        """current_price must be NaN sentinel (not fetched from positions snapshot)."""
        import math
        mock_ib = MagicMock()
        mock_ib.positions.return_value = [self._make_pos()]
        trader = _make_trader(mock_ib)
        result = trader.get_option_positions("SYMBOL")
        assert math.isnan(result[0]["current_price"])


# ---------------------------------------------------------------------------
# cancel_all_orders
# ---------------------------------------------------------------------------

class TestCancelAllOrders:
    def test_calls_req_global_cancel(self):
        mock_ib = MagicMock()
        trader = _make_trader(mock_ib)
        trader.cancel_all_orders()
        mock_ib.reqGlobalCancel.assert_called_once()
