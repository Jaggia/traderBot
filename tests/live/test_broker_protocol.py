"""Verify that AlpacaTrader and IBKRTrader satisfy BrokerProtocol at runtime.

Uses @runtime_checkable Protocol + isinstance() to confirm the duck-typed
interface contract is met by both broker implementations.

Note: AlpacaTrader compliance is verified by checking for the required method
names directly, since the alpaca-py package imports conflict with the global
ib_insync mock in test isolation. The Protocol check is authoritative at
static analysis time (mypy/pyright).
"""
import sys
from unittest.mock import MagicMock, patch

import pytest

# Mock ib_insync so IBKRTrader imports work without a live connection
sys.modules.setdefault("ib_insync", MagicMock())

_PROTOCOL_METHODS = [
    "get_option_mid_price",
    "buy_option",
    "sell_option",
    "get_order_status",
    "get_option_positions",
    "cancel_all_orders",
]


def _make_ibkr_trader():
    mock_ib = MagicMock()
    with patch("src.live.ibkr_trader.IB", return_value=mock_ib), \
         patch("src.live.ibkr_trader.Option"), \
         patch("src.live.ibkr_trader.MarketOrder"):
        from src.live.ibkr_trader import IBKRTrader
        trader = IBKRTrader(host="127.0.0.1", port=4002, client_id=2)
    trader._ib = mock_ib
    return trader


class TestBrokerProtocolCompliance:
    def test_ibkr_trader_satisfies_protocol(self):
        """IBKRTrader must pass isinstance(trader, BrokerProtocol)."""
        from src.live.broker_protocol import BrokerProtocol
        trader = _make_ibkr_trader()
        assert isinstance(trader, BrokerProtocol)

    def test_ibkr_trader_has_all_protocol_methods(self):
        """IBKRTrader must implement every method declared in BrokerProtocol."""
        trader = _make_ibkr_trader()
        for method in _PROTOCOL_METHODS:
            assert callable(getattr(trader, method, None)), (
                f"IBKRTrader missing required BrokerProtocol method: {method}"
            )

    def test_alpaca_trader_has_all_protocol_methods(self):
        """AlpacaTrader must implement every method declared in BrokerProtocol.

        Uses attribute introspection rather than isinstance() to avoid alpaca-py
        import conflicts with the ib_insync module-level mock.
        """
        # Build a minimal mock of the alpaca modules before importing
        alpaca_mock = MagicMock()
        mocks = {
            "alpaca": alpaca_mock,
            "alpaca.trading": alpaca_mock.trading,
            "alpaca.trading.client": alpaca_mock.trading.client,
            "alpaca.trading.requests": alpaca_mock.trading.requests,
            "alpaca.trading.enums": alpaca_mock.trading.enums,
            "alpaca.data": alpaca_mock.data,
            "alpaca.data.historical": alpaca_mock.data.historical,
            "alpaca.data.historical.option": alpaca_mock.data.historical.option,
            "alpaca.data.requests": alpaca_mock.data.requests,
        }
        with patch.dict("sys.modules", mocks):
            import importlib
            if "src.live.alpaca_trader" in sys.modules:
                del sys.modules["src.live.alpaca_trader"]
            import src.live.alpaca_trader as _at_mod
            with patch.object(_at_mod, "TradingClient", MagicMock()), \
                 patch.dict("os.environ", {"ALPACA_API_KEY": "x", "ALPACA_SECRET_KEY": "y"}):
                trader = _at_mod.AlpacaTrader(api_key="x", secret_key="y")

        for method in _PROTOCOL_METHODS:
            assert callable(getattr(trader, method, None)), (
                f"AlpacaTrader missing required BrokerProtocol method: {method}"
            )
