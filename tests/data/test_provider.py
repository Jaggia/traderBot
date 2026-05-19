"""Tests for src/data/provider.py — DataProviderProtocol and create_provider() factory.

RG-TDD RED phase: these tests define the expected interface and will fail until
provider.py is implemented.

Coverage:
  - Protocol compliance (isinstance checks)
  - Factory dispatch for all three data sources
  - Factory raises ValueError on unknown source
  - Each provider delegates to the correct loader (mocked)
  - ensure_data() behavior (Databento calls ensure_equity_data, others no-op)
  - should_trim_end() (TV=False, others=True)
  - get_source_name() returns correct string
"""

import pytest

import pandas as pd

# These imports will fail until provider.py is created — that's the RED phase.
from src.data.provider import DataProviderProtocol, create_provider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(source: str, **overrides) -> dict:
    """Build a minimal config dict with the given data source."""
    cfg = {"data": {"data_source": source}}
    cfg["data"].update(overrides)
    return cfg


def _mock_dataframe() -> pd.DataFrame:
    """Return a minimal OHLCV DataFrame matching the loader schema."""
    idx = pd.date_range("2026-01-02 09:30", periods=3, freq="5min", tz="America/New_York")
    return pd.DataFrame(
        {"open": [1.0, 2.0, 3.0], "high": [1.5, 2.5, 3.5], "low": [0.5, 1.5, 2.5],
         "close": [1.2, 2.2, 3.2], "volume": [100, 200, 300]},
        index=idx,
    )


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------

class TestProtocolCompliance:
    """All concrete providers must satisfy DataProviderProtocol."""

    def test_databento_is_protocol(self):
        provider = create_provider(_make_config("databento"))
        assert isinstance(provider, DataProviderProtocol)

    def test_alpaca_is_protocol(self):
        provider = create_provider(_make_config("alpaca"))
        assert isinstance(provider, DataProviderProtocol)

    def test_tv_is_protocol(self):
        provider = create_provider(_make_config("tv"))
        assert isinstance(provider, DataProviderProtocol)

    def test_tradingview_alias(self):
        """'tradingview' should also work as a source alias."""
        provider = create_provider(_make_config("tradingview"))
        assert isinstance(provider, DataProviderProtocol)

    def test_db_alias(self):
        """'db' should also work as a source alias."""
        provider = create_provider(_make_config("db"))
        assert isinstance(provider, DataProviderProtocol)


# ---------------------------------------------------------------------------
# Factory dispatch
# ---------------------------------------------------------------------------

class TestFactory:
    def test_unknown_source_raises(self):
        with pytest.raises(ValueError, match="Unknown data source"):
            create_provider(_make_config("nonexistent"))

    def test_missing_source_key_raises(self):
        with pytest.raises((KeyError, ValueError)):
            create_provider({})


# ---------------------------------------------------------------------------
# Source name
# ---------------------------------------------------------------------------

class TestSourceName:
    def test_databento_name(self):
        provider = create_provider(_make_config("databento"))
        assert provider.get_source_name() == "databento"

    def test_alpaca_name(self):
        provider = create_provider(_make_config("alpaca"))
        assert provider.get_source_name() == "alpaca"

    def test_tv_name(self):
        provider = create_provider(_make_config("tv"))
        assert provider.get_source_name() == "tv"


# ---------------------------------------------------------------------------
# should_trim_end
# ---------------------------------------------------------------------------

class TestShouldTrimEnd:
    def test_databento_trims(self):
        provider = create_provider(_make_config("databento"))
        assert provider.should_trim_end() is True

    def test_alpaca_trims(self):
        provider = create_provider(_make_config("alpaca"))
        assert provider.should_trim_end() is True

    def test_tv_does_not_trim(self):
        """TV loader already filters end dates — runner should skip trim."""
        provider = create_provider(_make_config("tv"))
        assert provider.should_trim_end() is False


# ---------------------------------------------------------------------------
# load_equity_data delegation (mocked loaders)
# ---------------------------------------------------------------------------

class TestLoadEquityData:
    def test_databento_delegates_to_load_databento_equities(self, monkeypatch):
        df = _mock_dataframe()
        monkeypatch.setattr("src.data.provider.load_databento_equities", lambda *a, **k: df)
        provider = create_provider(_make_config("databento", databento_equities_dir="/tmp/db5m"))
        result = provider.load_equity_data("2026-01-02", "2026-02-01")
        assert result.equals(df)

    def test_alpaca_delegates_to_load_cached_csvs(self, monkeypatch):
        df = _mock_dataframe()
        monkeypatch.setattr("src.data.provider.load_cached_csvs", lambda *a, **k: df)
        provider = create_provider(_make_config("alpaca", equities_dir="/tmp/alp5m"))
        result = provider.load_equity_data("2026-01-02", "2026-02-01")
        assert result.equals(df)

    def test_tv_delegates_to_load_tradingview_csv(self, monkeypatch):
        df = _mock_dataframe()
        monkeypatch.setattr("src.data.provider.load_tradingview_csv", lambda *a, **k: df)
        provider = create_provider(_make_config("tv", tv_equities_dir="/tmp/tv5m"))
        result = provider.load_equity_data("2026-01-02", "2026-02-01")
        assert result.equals(df)


# ---------------------------------------------------------------------------
# ensure_data
# ---------------------------------------------------------------------------

class TestEnsureData:
    def test_databento_calls_ensure_equity_data(self, monkeypatch):
        called = {}
        def fake_ensure(output_dir, start, end, warmup_months):
            called.update(output_dir=output_dir, start=start, end=end, warmup_months=warmup_months)

        monkeypatch.setattr("src.data.provider.ensure_equity_data", fake_ensure)
        provider = create_provider(_make_config("databento", databento_equities_dir="/tmp/db5m"))
        provider.ensure_data("2026-01-02", "2026-02-01", warmup_months=3)
        assert called["start"] == "2026-01-02"
        assert called["end"] == "2026-02-01"
        assert called["warmup_months"] == 3

    def test_alpaca_ensure_is_noop(self):
        """Alpaca has no download step — ensure_data does nothing."""
        provider = create_provider(_make_config("alpaca"))
        # Should not raise
        provider.ensure_data("2026-01-02", "2026-02-01", warmup_months=3)

    def test_tv_ensure_is_noop(self):
        """TradingView has no download step — ensure_data does nothing."""
        provider = create_provider(_make_config("tv"))
        provider.ensure_data("2026-01-02", "2026-02-01", warmup_months=3)
