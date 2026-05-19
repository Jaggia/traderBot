import logging
from datetime import datetime
from typing import List, Optional

import pandas as pd

from src.options.position import Position

logger = logging.getLogger(__name__)


class Portfolio:
    """Tracks cash, open positions, closed trades, and equity curve."""

    def __init__(self, initial_cash: float = 100_000.0, config: dict = None):
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.config = config or {}
        self.positions: List[Position] = []
        self.closed_trades: List[dict] = []
        self.equity_curve: List[dict] = []

    @staticmethod
    def _notional(price: float, contracts: int, trade_mode: str) -> float:
        """Compute notional value, applying 100x multiplier for options."""
        n = price * contracts
        if trade_mode == "options":
            n *= 100
        return n

    def _positions_value(self) -> float:
        """Sum marked-to-market value of all open positions."""
        value = 0.0
        for pos in self.positions:
            # Skip positions with stale/missing prices to avoid distorting equity curve
            if pos.current_price is None or pos.price_is_stale:
                continue
            if pos.trade_mode == "options":
                # Always long the option (call or put) — current value is always positive
                value += self._notional(pos.current_price, pos.contracts, pos.trade_mode)
            else:
                value += pos.direction * self._notional(pos.current_price, pos.contracts, pos.trade_mode)
        return value

    @property
    def costs(self):
        return self.config.get("costs", {})

    def _transaction_cost(self, price: float, contracts: int, mode: str) -> float:
        """Calculate commission + slippage for a trade.

        Two slippage parameters exist, each intended for a different trade mode:

        - ``slippage_pct`` (percentage of premium): models the bid-ask spread as a
          fraction of the share price.  Applied to **equities only**.
          Formula: price * (slippage_pct / 100) * contracts
          The 100× options multiplier is *not* applied here; equity notional is
          already price × contracts.

        - ``slippage_per_contract`` (flat dollar per contract): models the half
          bid-ask spread on an option (e.g. $0.10 per contract).  Applied to
          **options only**.  The 100× contract multiplier is already priced into
          this dollar figure, so no additional scaling is needed.
          Formula: slippage_per_contract * contracts

        The two parameters are **mode-specific alternatives**, not additive.
        Applying both to the same trade would double-count slippage.
        """
        commission = self.costs.get("commission_per_contract", 0.65) * contracts
        if mode == "options":
            # Options slippage: flat dollar per contract (half bid-ask spread).
            # slippage_pct is an equities-only concept — ignore it here.
            slippage = self.costs.get("slippage_per_contract", 0.0) * contracts
        else:
            # Equity slippage: percentage of share price × contracts.
            # slippage_per_contract is an options-only concept — ignore it here.
            slippage_pct = self.costs.get("slippage_pct", 0.0) / 100.0
            slippage = price * slippage_pct * contracts
        return commission + slippage

    def open_position(self, position: Position):
        """Open a new position, adjusting cash.

        All positions require sufficient cash (notional + costs) to open.
        No margin — shorts are cash-secured: you must have the capital to back
        the short even though the accounting credits proceeds to cash.

        Equities long:  cash -= notional + costs  (buy shares)
        Equities short: cash += notional - costs  (receive short-sale proceeds)
        Options:        cash -= notional + costs  (always pay premium)
        """
        notional = self._notional(position.entry_price, position.contracts, position.trade_mode)
        txn_cost = self._transaction_cost(position.entry_price, position.contracts, position.trade_mode)

        # All positions require cash >= notional + costs (no margin)
        required = notional + txn_cost
        if self.cash < required:
            mode_label = (
                f"{position.option_type} option"
                if position.trade_mode == "options"
                else ("long" if position.direction == 1 else "short") + " equity"
            )
            logger.warning(
                "Insufficient funds to open %s position: need $%.2f, have $%.2f",
                mode_label, required, self.cash,
            )
            raise ValueError(
                f"Insufficient funds: need ${required:.2f}, have ${self.cash:.2f}"
            )

        if position.trade_mode == "options":
            self.cash -= required
        else:
            if position.direction == 1:
                self.cash -= required
            else:
                # Short equity: receive sale proceeds minus costs
                self.cash += notional - txn_cost

        position.current_price = position.entry_price
        position.high_water = position.entry_price
        self.positions.append(position)

    def close_position(self, position: Position, exit_price: float, exit_time: datetime, reason: str):
        """Close a position, adjusting cash.

        Equities long:  cash += notional - costs  (sell shares)
        Equities short: cash -= notional + costs  (buy back shares)
        Options:        cash += notional - costs  (always sell the option back, receive premium)
        """
        position.update_price(exit_price)
        txn_cost = self._transaction_cost(exit_price, position.contracts, position.trade_mode)

        notional = self._notional(exit_price, position.contracts, position.trade_mode)

        if position.trade_mode == "options":
            # Always receive proceeds when closing a long option
            self.cash += notional - txn_cost
        else:
            # direction=1 (long): cash += notional; direction=-1 (short): cash -= notional
            self.cash += position.direction * notional - txn_cost

        # P&L = (exit - entry) * qty - total costs  (options always long, equities use direction)
        entry_notional = self._notional(position.entry_price, position.contracts, position.trade_mode)
        exit_notional = self._notional(exit_price, position.contracts, position.trade_mode)
        if position.trade_mode == "options":
            pnl = (exit_notional - entry_notional) - (
                self._transaction_cost(position.entry_price, position.contracts, position.trade_mode)
                + txn_cost
            )
        else:
            pnl = position.direction * (exit_notional - entry_notional) - (
                self._transaction_cost(position.entry_price, position.contracts, position.trade_mode)
                + txn_cost
            )

        # Cost-adjusted pnl_pct: same economic basis as dollar pnl
        entry_notional_for_pct = self._notional(position.entry_price, position.contracts, position.trade_mode)
        pnl_pct = round((pnl / entry_notional_for_pct) * 100.0, 2) if entry_notional_for_pct != 0 else 0.0

        self.closed_trades.append({
            "entry_time": position.entry_time,
            "exit_time": exit_time,
            "direction": "long" if position.direction == 1 else "short",
            "trade_mode": position.trade_mode,
            "entry_price": position.entry_price,
            "exit_price": exit_price,
            "contracts": position.contracts,
            "pnl": round(pnl, 2),
            "pnl_pct": pnl_pct,
            "exit_reason": reason,
            "strike": position.strike,
            "expiry": position.expiry,
            "option_type": position.option_type,
            "delta": position.delta,
            "gamma": position.gamma,
            "theta": position.theta,
            "vega": position.vega,
        })

        # Remove by identity (not ==) to avoid removing the wrong position
        # when two positions share identical field values (dataclass __eq__).
        for idx, p in enumerate(self.positions):
            if p is position:
                self.positions.pop(idx)
                break

    def mark_to_market(self, timestamp: datetime):
        """Record current portfolio equity.

        Long positions add value; short positions are liabilities.
        """
        equity = self.cash + self._positions_value()
        self.equity_curve.append({"timestamp": timestamp, "equity": equity, "cash": self.cash})

    def get_equity(self) -> float:
        """Compute live equity from current cash + open positions."""
        return self.cash + self._positions_value()

    def get_equity_df(self) -> pd.DataFrame:
        if not self.equity_curve:
            return pd.DataFrame(columns=["equity", "cash"])
        return pd.DataFrame(self.equity_curve).set_index("timestamp")

    def record_initial_equity(self, timestamp) -> None:
        """Record the starting equity as the first equity curve point (t=0 baseline)."""
        self.equity_curve.insert(0, {
            "timestamp": timestamp,
            "equity": self.initial_cash,
            "cash": self.initial_cash,
        })

    def get_trade_log(self) -> pd.DataFrame:
        return pd.DataFrame(self.closed_trades)

    def has_open_position(self, direction: Optional[int] = None) -> bool:
        if direction is None:
            return len(self.positions) > 0
        return any(p.direction == direction for p in self.positions)

    def can_open(self) -> bool:
        """Return True if there is capacity for one new position."""
        max_pos = self.config.get("position", {}).get("max_concurrent_positions", 1)
        return len(self.positions) < max_pos
