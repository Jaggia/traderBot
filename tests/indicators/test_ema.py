"""Tests for src/indicators/ema.py — compute_ema()."""
import numpy as np
import pandas as pd
import pytest

from src.indicators.ema import compute_ema


@pytest.fixture
def sample_ohlcv():
    """100 bars of synthetic OHLCV data."""
    np.random.seed(42)
    n = 100
    dates = pd.date_range("2023-01-03 09:30", periods=n, freq="5min", tz="America/New_York")
    close = 400 + np.cumsum(np.random.randn(n) * 0.5)
    return pd.DataFrame({
        "open": close + np.random.randn(n) * 0.1,
        "high": close + np.abs(np.random.randn(n) * 0.3),
        "low": close - np.abs(np.random.randn(n) * 0.3),
        "close": close,
        "volume": np.random.randint(100000, 2000000, n).astype(float),
    }, index=dates)


class TestComputeEma:
    def test_output_length(self, sample_ohlcv):
        """EMA output has the same length as the input."""
        ema = compute_ema(sample_ohlcv, period=10)
        assert len(ema) == len(sample_ohlcv)

    def test_no_nan_after_warmup(self, sample_ohlcv):
        """EWM with adjust=False produces values from bar 0 (no NaN)."""
        ema = compute_ema(sample_ohlcv, period=10)
        assert ema.isna().sum() == 0

    def test_flat_price_equals_price(self):
        """When all closes are identical, EMA equals that price."""
        n = 50
        df = pd.DataFrame({"close": [100.0] * n})
        ema = compute_ema(df, period=20)
        np.testing.assert_allclose(ema.values, 100.0)

    def test_different_periods_differ(self, sample_ohlcv):
        """Two different periods produce different EMA series."""
        ema_10 = compute_ema(sample_ohlcv, period=10)
        ema_50 = compute_ema(sample_ohlcv, period=50)
        assert not np.allclose(ema_10.values, ema_50.values)

    def test_custom_column(self, sample_ohlcv):
        """EMA can be computed on a column other than 'close'."""
        ema = compute_ema(sample_ohlcv, period=10, column="open")
        assert len(ema) == len(sample_ohlcv)

    def test_missing_column_raises(self, sample_ohlcv):
        """KeyError raised when the requested column doesn't exist."""
        with pytest.raises(KeyError):
            compute_ema(sample_ohlcv, period=10, column="nonexistent")

    def test_ema_233_length(self, sample_ohlcv):
        """Default period=233 produces output of correct length."""
        ema = compute_ema(sample_ohlcv)
        assert len(ema) == len(sample_ohlcv)
