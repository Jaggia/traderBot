"""Structural typing protocol for broker clients used by LiveEngine.

Both AlpacaTrader and IBKRTrader implement this interface. Using a Protocol
(structural subtyping) means neither class needs to inherit from it — duck
typing is enforced at static analysis time via mypy/pyright, and at runtime
via isinstance() checks thanks to @runtime_checkable.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class BrokerProtocol(Protocol):
    """Public interface required by LiveEngine from any broker client."""

    def get_option_mid_price(self, occ_symbol: str) -> float | None:
        """Return mid-price of an option, or None if unavailable."""
        ...

    def buy_option(self, occ_symbol: str, qty: int) -> str:
        """Place a market BUY for qty contracts. Return order ID string."""
        ...

    def sell_option(self, occ_symbol: str, qty: int) -> str:
        """Place a market SELL for qty contracts. Return order ID string."""
        ...

    def get_order_status(self, order_id: str) -> str:
        """Return lowercase fill status ('filled', 'unknown', etc.)."""
        ...

    def get_option_positions(self, underlying: str = "SYMBOL") -> list[dict]:
        """Return list of open option positions for the given underlying."""
        ...

    def buy_equity(self, symbol: str, qty: int, signal: int) -> str:
        """Place a market BUY for qty shares. Return order ID string."""
        ...

    def sell_equity(self, symbol: str, qty: int) -> str:
        """Place a market SELL for qty shares. Return order ID string."""
        ...

    def cancel_all_orders(self) -> None:
        """Cancel all pending orders (safety call at shutdown)."""
        ...
