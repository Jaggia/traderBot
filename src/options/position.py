from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from src.constants import OPTIONS_MULTIPLIER


@dataclass
class Position:
    """Tracks an open equity or options position."""

    direction: int              # +1 long, -1 short
    entry_price: float
    entry_time: datetime
    contracts: float
    trade_mode: str             # "equities" or "options"

    # Options-specific fields
    option_type: Optional[str] = None   # "C" or "P"
    strike: Optional[float] = None
    expiry: Optional[datetime] = None
    raw_symbol: Optional[str] = None

    # Greeks snapshot at entry
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None

    # Implied volatility back-solved from market price at entry (used for intrabar BS checks)
    entry_iv: Optional[float] = None

    # Fixed stop/limit price levels (set at entry, like TV's strategy.exit)
    stop_price: Optional[float] = None
    limit_price: Optional[float] = None

    # Live tracking
    current_price: float = 0.0
    high_water: float = 0.0
    price_is_stale: bool = False

    def __post_init__(self):
        if self.high_water == 0.0:
            self.high_water = self.entry_price

    def unrealized_pnl(self) -> float:
        """Dollar P&L for the position (options prices are per-share, x100 multiplier).

        Options are always long (long call or long put): direction does not flip the sign.
        """
        diff = self.current_price - self.entry_price
        if self.trade_mode == "options":
            return diff * self.contracts * OPTIONS_MULTIPLIER
        return diff * self.contracts * self.direction

    def pnl_pct(self) -> float:
        """Percent P&L relative to entry cost.

        Options are always long (long call or long put): appreciation is always a gain.
        Equities use direction: +1 long profits when price rises, -1 short profits when price falls.
        """
        if self.entry_price == 0:
            return 0.0
        if self.trade_mode == "options":
            return ((self.current_price - self.entry_price) / self.entry_price) * 100.0
        return ((self.current_price - self.entry_price) / self.entry_price) * 100.0 * self.direction

    def update_price(self, price: float):
        self.current_price = price
        if self.trade_mode == "options":
            # Options are always long — high_water tracks maximum price reached (best case)
            if price > self.high_water:
                self.high_water = price
        elif self.direction == 1:
            # Long equity: best price is the highest
            if price > self.high_water:
                self.high_water = price
        else:
            # Short equity: best price is the lowest (high_water tracks the low)
            if self.high_water == 0.0 or price < self.high_water:
                self.high_water = price
