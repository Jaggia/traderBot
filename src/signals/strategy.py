"""Strategy pattern for signal generation.

Each signal system implements ``SignalStrategy``, providing
``compute_indicators`` and ``generate_signals``.  The engine receives
a strategy instance and calls through it — only one strategy runs per
backtest.

Use ``create_strategy(config)`` to instantiate the correct strategy
from YAML config (``strategy.signal_system``).
"""
import logging
from abc import ABC, abstractmethod

import pandas as pd

logger = logging.getLogger(__name__)


class SignalStrategy(ABC):
    """Abstract base class for signal generation strategies."""

    @abstractmethod
    def compute_indicators(self, df: pd.DataFrame, config: dict) -> pd.DataFrame:
        """Add indicator columns to the DataFrame."""
        ...

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame, config: dict) -> pd.Series:
        """Return a Series of +1 / -1 / 0 signals on the DataFrame's index."""
        ...


class IndicatorPairStrategy(SignalStrategy):
    """Generic non-EMA indicator-pair strategy.

    The primary config lives under ``config["signals"]``.
    """

    def compute_indicators(self, df: pd.DataFrame, config: dict) -> pd.DataFrame:
        from src.signals.indicator_pair_pipeline import compute_indicators
        return compute_indicators(df, config)

    def generate_signals(self, df: pd.DataFrame, config: dict) -> pd.Series:
        from src.signals.indicator_pair_pipeline import generate_signals
        return generate_signals(df, config)

class Ema233Strategy(SignalStrategy):
    """System 2: 233 EMA intrabar cross on internally resampled 15-min bars."""

    def compute_indicators(self, df: pd.DataFrame, config: dict) -> pd.DataFrame:
        from src.signals.indicator_pair_pipeline import compute_indicators
        return compute_indicators(df, config)

    def generate_signals(self, df: pd.DataFrame, config: dict) -> pd.Series:
        from src.signals.indicator_pair_pipeline import generate_signals
        return generate_signals(df, config)


class TriggerChainStrategy(IndicatorPairStrategy):
    """New canonical strategy: sequential trigger chain (1..N indicators)."""


_STRATEGY_MAP = {
    "indicator_pair": IndicatorPairStrategy,
    "ema_233": Ema233Strategy,
    "trigger_chain": TriggerChainStrategy,
}


def create_strategy(config: dict) -> SignalStrategy:
    """Instantiate the signal strategy specified in config.

    Reads ``config["strategy"]["signal_system"]`` (default ``"indicator_pair"``).

    Raises
    ------
    ValueError
        If the signal_system value is not recognized.
    """
    key = config.get("strategy", {}).get("signal_system", "indicator_pair")
    cls = _STRATEGY_MAP.get(key)
    if cls is None:
        raise ValueError(
            f"Unknown signal_system {key!r}. "
            f"Valid options: {', '.join(sorted(_STRATEGY_MAP))}"
        )
    logger.info("Signal strategy: %s (%s)", key, cls.__name__)
    return cls()
