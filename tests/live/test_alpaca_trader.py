"""
Tests for src/live/alpaca_trader.py.

Mocks the Alpaca SDK at the module level so no real API calls are made.
Covers:
  - _strip_occ():           symbol cleaning (strip spaces from OCC root padding)
  - get_option_mid_price(): returns midpoint of bid/ask; falls back on exception
  - buy_option():           order placed with correct symbol, qty, and side
  - sell_option():          order placed with correct symbol, qty, and side (SELL)
  - get_positions():        delegates to TradingClient.get_all_positions()
  - cancel_all_orders():    delegates to TradingClient.cancel_orders()
"""

from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Module-level patch helpers
# ---------------------------------------------------------------------------

def _make_trader(trading_client=None, data_client=None):
    """Instantiate AlpacaTrader with fully mocked SDK clients."""
    mock_trading = trading_client or MagicMock()
    mock_data    = data_client    or MagicMock()

    with (
        patch("src.live.alpaca_trader.TradingClient",             return_value=mock_trading),
        patch("src.live.alpaca_trader.OptionHistoricalDataClient", return_value=mock_data),
    ):
        from src.live.alpaca_trader import AlpacaTrader
        trader = AlpacaTrader(api_key="test_key", secret_key="test_secret")

    # Expose the mocks for assertions
    trader._client      = mock_trading
    trader._data_client = mock_data
    return trader


# ---------------------------------------------------------------------------
# _strip_occ
# ---------------------------------------------------------------------------

class TestStripOcc:
    def test_strips_spaces_from_root_padding(self):
        from src.live.alpaca_trader import _strip_occ
        assert _strip_occ("SYMBOL   260228C00450000") == "SYMBOL260228C00450000"

    def test_already_clean_symbol_unchanged(self):
        from src.live.alpaca_trader import _strip_occ
        assert _strip_occ("SYMBOL260228C00450000") == "SYMBOL260228C00450000"

    def test_strips_all_internal_spaces(self):
        from src.live.alpaca_trader import _strip_occ
        assert _strip_occ("Q Q Q 2 6 0 2 2 8 C 0 0 4 5 0 0 0 0") == "SYMBOL260228C00450000"


# ---------------------------------------------------------------------------
# get_option_mid_price
# ---------------------------------------------------------------------------

class TestGetOptionMidPrice:
    def test_returns_midpoint_when_bid_and_ask_positive(self):
        """(bid + ask) / 2 is returned when both are positive."""
        quote = MagicMock()
        quote.bid_price = 1.20
        quote.ask_price = 1.40

        mock_data = MagicMock()
        mock_data.get_option_latest_quote.return_value = {"SYMBOL260228C00450000": quote}

        trader = _make_trader(data_client=mock_data)
        price = trader.get_option_mid_price("SYMBOL   260228C00450000")
        assert price == pytest.approx(1.30)

    def test_returns_none_when_bid_is_zero(self):
        """A zero bid indicates no market — should return None."""
        quote = MagicMock()
        quote.bid_price = 0.0
        quote.ask_price = 1.50

        mock_data = MagicMock()
        mock_data.get_option_latest_quote.return_value = {"SYMBOL260228C00450000": quote}

        trader = _make_trader(data_client=mock_data)
        assert trader.get_option_mid_price("SYMBOL   260228C00450000") is None

    def test_returns_none_when_ask_is_zero(self):
        """A zero ask indicates no market — should return None."""
        quote = MagicMock()
        quote.bid_price = 1.20
        quote.ask_price = 0.0

        mock_data = MagicMock()
        mock_data.get_option_latest_quote.return_value = {"SYMBOL260228C00450000": quote}

        trader = _make_trader(data_client=mock_data)
        assert trader.get_option_mid_price("SYMBOL   260228C00450000") is None

    def test_unexpected_exception_propagates_from_quote_fetch(self):
        """Unexpected exceptions from the data client must not be swallowed."""
        mock_data = MagicMock()
        mock_data.get_option_latest_quote.side_effect = RuntimeError("API error")

        trader = _make_trader(data_client=mock_data)
        with pytest.raises(RuntimeError, match="API error"):
            trader.get_option_mid_price("SYMBOL   260228C00450000")

    def test_strips_occ_symbol_before_lookup(self):
        """The spaced OCC symbol must be stripped before querying the data client."""
        quote = MagicMock()
        quote.bid_price = 2.00
        quote.ask_price = 2.10

        mock_data = MagicMock()
        # Return the stripped symbol as the key
        mock_data.get_option_latest_quote.return_value = {"SYMBOL260228C00450000": quote}

        trader = _make_trader(data_client=mock_data)
        price = trader.get_option_mid_price("SYMBOL   260228C00450000")
        assert price is not None

        # Verify that the request was made with the stripped symbol
        call_args = mock_data.get_option_latest_quote.call_args
        request_obj = call_args[0][0]  # positional arg
        # OptionLatestQuoteRequest stores the symbol — just check the str representation
        assert "SYMBOL260228C00450000" in str(request_obj.symbol_or_symbols)


# ---------------------------------------------------------------------------
# buy_option
# ---------------------------------------------------------------------------

class TestBuyOption:
    def test_returns_order_id_as_string(self):
        order = MagicMock()
        order.id = "order-abc-123"

        mock_trading = MagicMock()
        mock_trading.submit_order.return_value = order

        trader = _make_trader(trading_client=mock_trading)
        result = trader.buy_option("SYMBOL   260228C00450000", qty=2)
        assert result == "order-abc-123"

    def test_strips_occ_symbol_in_request(self):
        order = MagicMock()
        order.id = "x"
        mock_trading = MagicMock()
        mock_trading.submit_order.return_value = order

        trader = _make_trader(trading_client=mock_trading)
        trader.buy_option("SYMBOL   260228C00450000", qty=1)

        submitted = mock_trading.submit_order.call_args[0][0]
        assert submitted.symbol == "SYMBOL260228C00450000"

    def test_buy_side_is_set_correctly(self):
        from alpaca.trading.enums import OrderSide

        order = MagicMock()
        order.id = "y"
        mock_trading = MagicMock()
        mock_trading.submit_order.return_value = order

        trader = _make_trader(trading_client=mock_trading)
        trader.buy_option("SYMBOL260228C00450000", qty=3)

        submitted = mock_trading.submit_order.call_args[0][0]
        assert submitted.side == OrderSide.BUY

    def test_qty_is_forwarded(self):
        order = MagicMock()
        order.id = "z"
        mock_trading = MagicMock()
        mock_trading.submit_order.return_value = order

        trader = _make_trader(trading_client=mock_trading)
        trader.buy_option("SYMBOL260228C00450000", qty=5)

        submitted = mock_trading.submit_order.call_args[0][0]
        assert submitted.qty == 5


# ---------------------------------------------------------------------------
# sell_option
# ---------------------------------------------------------------------------

class TestSellOption:
    def test_returns_order_id_as_string(self):
        order = MagicMock()
        order.id = "sell-order-789"

        mock_trading = MagicMock()
        mock_trading.submit_order.return_value = order

        trader = _make_trader(trading_client=mock_trading)
        result = trader.sell_option("SYMBOL260228C00450000", qty=2)
        assert result == "sell-order-789"

    def test_sell_side_is_set_correctly(self):
        from alpaca.trading.enums import OrderSide

        order = MagicMock()
        order.id = "s"
        mock_trading = MagicMock()
        mock_trading.submit_order.return_value = order

        trader = _make_trader(trading_client=mock_trading)
        trader.sell_option("SYMBOL260228C00450000", qty=1)

        submitted = mock_trading.submit_order.call_args[0][0]
        assert submitted.side == OrderSide.SELL

    def test_strips_occ_symbol_in_sell_request(self):
        order = MagicMock()
        order.id = "t"
        mock_trading = MagicMock()
        mock_trading.submit_order.return_value = order

        trader = _make_trader(trading_client=mock_trading)
        trader.sell_option("SYMBOL   260228C00450000", qty=2)

        submitted = mock_trading.submit_order.call_args[0][0]
        assert submitted.symbol == "SYMBOL260228C00450000"


# ---------------------------------------------------------------------------
# get_positions
# ---------------------------------------------------------------------------

class TestGetPositions:
    def test_delegates_to_get_all_positions(self):
        """get_positions() must return whatever get_all_positions() returns."""
        mock_pos = [MagicMock(), MagicMock()]
        mock_trading = MagicMock()
        mock_trading.get_all_positions.return_value = mock_pos

        trader = _make_trader(trading_client=mock_trading)
        result = trader.get_positions()
        assert result is mock_pos
        mock_trading.get_all_positions.assert_called_once()


# ---------------------------------------------------------------------------
# cancel_all_orders
# ---------------------------------------------------------------------------

class TestCancelAllOrders:
    def test_delegates_to_cancel_orders(self):
        mock_trading = MagicMock()
        trader = _make_trader(trading_client=mock_trading)
        trader.cancel_all_orders()
        mock_trading.cancel_orders.assert_called_once()


# ---------------------------------------------------------------------------
# parse_occ_symbol
# ---------------------------------------------------------------------------

class TestParseOccSymbol:
    def test_padded_call(self):
        from src.live.alpaca_trader import parse_occ_symbol
        result = parse_occ_symbol("SYMBOL   260221C00451000")
        assert result["underlying"] == "SYMBOL"
        assert result["expiry"].year == 2026
        assert result["expiry"].month == 2
        assert result["expiry"].day == 21
        assert result["option_type"] == "C"
        assert result["strike"] == 451.0
        assert result["raw_symbol"] == "SYMBOL   260221C00451000"

    def test_stripped_put(self):
        from src.live.alpaca_trader import parse_occ_symbol
        result = parse_occ_symbol("SYMBOL260221P00400000")
        assert result["underlying"] == "SYMBOL"
        assert result["option_type"] == "P"
        assert result["strike"] == 400.0

    def test_fractional_strike(self):
        from src.live.alpaca_trader import parse_occ_symbol
        result = parse_occ_symbol("SYMBOL260221C00451500")
        assert result["strike"] == 451.5

    def test_invalid_symbol_raises(self):
        from src.live.alpaca_trader import parse_occ_symbol
        with pytest.raises(ValueError, match="Cannot parse"):
            parse_occ_symbol("BADFORMAT")


# ---------------------------------------------------------------------------
# get_option_positions
# ---------------------------------------------------------------------------

class TestGetOptionPositions:
    def test_returns_parsed_option_positions(self):
        mock_pos = MagicMock()
        mock_pos.symbol = "SYMBOL260221C00451000"
        mock_pos.qty = "2"
        mock_pos.avg_entry_price = "3.50"
        mock_pos.current_price = "4.10"
        mock_pos.side = "long"

        mock_trading = MagicMock()
        mock_trading.get_all_positions.return_value = [mock_pos]

        trader = _make_trader(trading_client=mock_trading)
        result = trader.get_option_positions("SYMBOL")

        assert len(result) == 1
        assert result[0]["qty"] == 2
        assert result[0]["avg_entry_price"] == pytest.approx(3.50)
        assert result[0]["strike"] == 451.0
        assert result[0]["option_type"] == "C"

    def test_filters_out_equity_positions(self):
        equity_pos = MagicMock()
        equity_pos.symbol = "SYMBOL"  # plain equity, not option

        mock_trading = MagicMock()
        mock_trading.get_all_positions.return_value = [equity_pos]

        trader = _make_trader(trading_client=mock_trading)
        assert trader.get_option_positions("SYMBOL") == []

    def test_filters_out_other_underlying(self):
        spy_pos = MagicMock()
        spy_pos.symbol = "SYMBOL260221C00450000"

        mock_trading = MagicMock()
        mock_trading.get_all_positions.return_value = [spy_pos]

        trader = _make_trader(trading_client=mock_trading)
        assert trader.get_option_positions("SYMBOL") == []

    def test_empty_positions(self):
        mock_trading = MagicMock()
        mock_trading.get_all_positions.return_value = []

        trader = _make_trader(trading_client=mock_trading)
        assert trader.get_option_positions("SYMBOL") == []


# ---------------------------------------------------------------------------
# get_option_mid_price — error handling
# ---------------------------------------------------------------------------

class TestGetOptionMidPriceErrorHandling:
    def test_key_error_returns_none(self):
        """KeyError from the response dict (symbol not in result) returns None silently."""
        mock_data = MagicMock()
        mock_data.get_option_latest_quote.return_value = {}  # symbol absent → KeyError on lookup

        trader = _make_trader(data_client=mock_data)
        result = trader.get_option_mid_price("SYMBOL   260228C00450000")
        assert result is None

    def test_unexpected_exception_propagates(self):
        """Non-KeyError exceptions (auth failures, network errors, etc.) must propagate."""
        mock_data = MagicMock()
        mock_data.get_option_latest_quote.side_effect = RuntimeError("auth failed")

        trader = _make_trader(data_client=mock_data)
        with pytest.raises(RuntimeError, match="auth failed"):
            trader.get_option_mid_price("SYMBOL   260228C00450000")
