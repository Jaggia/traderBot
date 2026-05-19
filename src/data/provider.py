"""Unified data provider interface (Ports & Adapters pattern).

Defines a Protocol that all equity data loaders must satisfy, and a factory
function that picks the right provider from config. Concrete classes are
private — callers use the Protocol or the factory.

Pattern mirrors ``src/live/broker_protocol.py``: structural typing via
``@runtime_checkable`` Protocol, no inheritance required.
"""
from __future__ import annotations

import logging
from typing import Optional, Protocol, runtime_checkable

import pandas as pd

from src.data.alpaca_loader import load_cached_csvs
from src.data.databento_loader import ensure_equity_data, load_databento_equities
from src.data.tradingview_loader import load_tradingview_csv

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class DataProviderProtocol(Protocol):
    """Public interface required by BaseBacktestRunner from any data source."""

    def load_equity_data(
        self,
        start: Optional[str],
        end: Optional[str],
    ) -> pd.DataFrame:
        """Load OHLCV equity bars for the given date range."""
        ...

    def ensure_data(
        self,
        start: Optional[str],
        end: Optional[str],
        warmup_months: int,
    ) -> None:
        """Ensure data is available (download/cache if needed). No-op if not applicable."""
        ...

    def get_source_name(self) -> str:
        """Return a short identifier for this data source (e.g. 'databento')."""
        ...

    def should_trim_end(self) -> bool:
        """Whether the runner should trim data past ``end`` after loading.

        TradingView returns False because its loader already handles end-date
        filtering internally. All others return True.
        """
        ...


# ---------------------------------------------------------------------------
# Concrete providers (private)
# ---------------------------------------------------------------------------

class _DatabentoProvider:
    """Provider for Databento equity data (XNAS.ITCH 5-min bars)."""

    def __init__(self, config: dict):
        data_cfg = config.get("data", {})
        self._dir = data_cfg.get("databento_equities_dir", "data/DataBento/equities/SYMBOL/5min")

    def load_equity_data(self, start: Optional[str], end: Optional[str]) -> pd.DataFrame:
        logger.info("Loading Databento equity data...")
        return load_databento_equities(self._dir, start=start, end=end)

    def ensure_data(self, start: Optional[str], end: Optional[str], warmup_months: int) -> None:
        if start and end:
            ensure_equity_data(
                output_dir=self._dir,
                start=start,
                end=end,
                warmup_months=warmup_months,
            )
        else:
            logger.warning(
                "ensure_equity_data skipped: both start and end dates are required "
                "(got start=%r, end=%r)",
                start, end,
            )

    def get_source_name(self) -> str:
        return "databento"

    def should_trim_end(self) -> bool:
        return True


class _AlpacaProvider:
    """Provider for Alpaca CSV equity data."""

    def __init__(self, config: dict):
        data_cfg = config.get("data", {})
        self._dir = data_cfg.get("equities_dir", "data/Alpaca/equities/SYMBOL/5min")

    def load_equity_data(self, start: Optional[str], end: Optional[str]) -> pd.DataFrame:
        logger.info("Loading equity data...")
        return load_cached_csvs(self._dir, start=start, end=end)

    def ensure_data(self, start: Optional[str], end: Optional[str], warmup_months: int) -> None:
        pass  # Alpaca has no download step

    def get_source_name(self) -> str:
        return "alpaca"

    def should_trim_end(self) -> bool:
        return True


class _TradingViewProvider:
    """Provider for TradingView CSV equity data (PST -> EST conversion)."""

    def __init__(self, config: dict):
        data_cfg = config.get("data", {})
        self._dir = data_cfg.get("tv_equities_dir", "data/TV/equities/SYMBOL/5min")

    def load_equity_data(self, start: Optional[str], end: Optional[str]) -> pd.DataFrame:
        logger.info("Loading TradingView data (PST -> EST conversion)...")
        return load_tradingview_csv(self._dir, start=start, end=end)

    def ensure_data(self, start: Optional[str], end: Optional[str], warmup_months: int) -> None:
        pass  # TradingView has no download step

    def get_source_name(self) -> str:
        return "tv"

    def should_trim_end(self) -> bool:
        return False  # TV loader already handles end-date filtering


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_SOURCE_ALIASES: dict[str, str] = {
    "databento": "databento",
    "db": "databento",
    "alpaca": "alpaca",
    "tv": "tv",
    "tradingview": "tv",
}


def create_provider(config: dict) -> DataProviderProtocol:
    """Create a data provider from the given config.

    Reads ``config["data"]["data_source"]`` to select the provider.
    Accepted values: "databento", "db", "alpaca", "tv", "tradingview".

    Raises ``ValueError`` for unknown sources.
    """
    source = config.get("data", {}).get("data_source")
    if not source:
        raise ValueError("Config missing data.data_source")
    key = _SOURCE_ALIASES.get(source)
    if key is None:
        raise ValueError(f"Unknown data source: {source!r}")
    providers = {
        "databento": _DatabentoProvider,
        "alpaca": _AlpacaProvider,
        "tv": _TradingViewProvider,
    }
    return providers[key](config)
