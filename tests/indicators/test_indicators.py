import logging

import pandas as pd
import numpy as np
import pytest

from src.indicators.smi import compute_smi
from src.indicators.williams_r import compute_williams_r
from src.indicators.vwap import compute_vwap
from src.indicators.tsi import compute_tsi
from src.indicators.stoch_rsi import compute_stoch_rsi
from src.signals.indicator_pair_pipeline import compute_indicators


@pytest.fixture
def sample_ohlcv():
    """Generate 100 bars of synthetic OHLCV data."""
    np.random.seed(42)
    n = 100
    dates = pd.date_range("2023-01-03 09:30", periods=n, freq="5min", tz="America/New_York")
    close = 400 + np.cumsum(np.random.randn(n) * 0.5)
    high = close + np.abs(np.random.randn(n) * 0.3)
    low = close - np.abs(np.random.randn(n) * 0.3)
    open_ = close + np.random.randn(n) * 0.1
    volume = np.random.randint(100000, 2000000, n).astype(float)
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close, "volume": volume
    }, index=dates)


class TestSMI:
    def test_output_shape(self, sample_ohlcv):
        smi = compute_smi(sample_ohlcv, period=5, smooth1=8, smooth2=8)
        assert len(smi) == len(sample_ohlcv)

    def test_bounded_after_warmup(self, sample_ohlcv):
        smi = compute_smi(sample_ohlcv, period=5, smooth1=8, smooth2=8)
        valid = smi.dropna()
        assert valid.max() <= 100  # SMI is bounded to [-100, +100]
        assert valid.min() >= -100

    def test_flat_price_yields_nan_not_inf(self):
        """When all prices are identical (zero range), SMI result is NaN not inf."""
        df = pd.DataFrame({
            "open": [100.0] * 30,
            "high": [100.0] * 30,
            "low": [100.0] * 30,
            "close": [100.0] * 30,
            "volume": [1e6] * 30,
        })
        smi = compute_smi(df, period=5, smooth1=3, smooth2=3)
        valid = smi.dropna()
        assert not np.isinf(valid).any(), "SMI produced inf for flat price input"

    def test_different_periods_differ(self, sample_ohlcv):
        fast = compute_smi(sample_ohlcv, period=5, smooth1=8, smooth2=8)
        slow = compute_smi(sample_ohlcv, period=13, smooth1=8, smooth2=8)
        # Align on the same index (both non-NaN) and verify they differ
        combined = pd.concat([fast.rename("fast"), slow.rename("slow")], axis=1).dropna()
        assert not np.allclose(combined["fast"].values, combined["slow"].values)


class TestWilliamsR:
    def test_output_shape(self, sample_ohlcv):
        wr = compute_williams_r(sample_ohlcv, period=13)
        assert len(wr) == len(sample_ohlcv)

    def test_range(self, sample_ohlcv):
        wr = compute_williams_r(sample_ohlcv, period=13)
        valid = wr.dropna()
        assert valid.max() <= 0.0   # W%R is bounded to [-100, 0]
        assert valid.min() >= -100.0

    def test_at_highest_high(self):
        """When close equals the rolling high, W%R should be 0."""
        df = pd.DataFrame({
            "high": [10, 11, 12, 13, 14],
            "low": [5, 6, 7, 8, 9],
            "close": [10, 11, 12, 13, 14],
        })
        wr = compute_williams_r(df, period=3)
        assert wr.iloc[-1] == pytest.approx(0.0)

    def test_at_lowest_low(self):
        """When close equals the rolling low, W%R should be -100."""
        # With period=3, the last bar's window covers bars 2-4.
        # low=[7,8,4] → rolling min=4; high=[12,13,14] → rolling max=14; close=4
        # WR = -100 * (14-4)/(14-4) = -100
        df = pd.DataFrame({
            "high": [10, 11, 12, 13, 14],
            "low": [5, 6, 7, 8, 4],
            "close": [5, 6, 7, 8, 4],
        })
        wr = compute_williams_r(df, period=3)
        assert wr.iloc[-1] == pytest.approx(-100.0)

    def test_zero_range_bars_yield_nan(self):
        """When high == low for entire window (flat price), result is NaN not inf."""
        df = pd.DataFrame({
            "high": [100.0] * 10,
            "low": [100.0] * 10,
            "close": [100.0] * 10,
        })
        wr = compute_williams_r(df, period=5)
        # All bars in a full window are flat → denominator=0 → should give NaN not inf
        assert not np.isinf(wr.dropna()).any()
        assert np.isnan(wr.iloc[-1])


class TestVWAP:
    def test_output_shape(self, sample_ohlcv):
        vwap = compute_vwap(sample_ohlcv)
        assert len(vwap) == len(sample_ohlcv)

    def test_daily_reset(self):
        """VWAP should reset each day."""
        dates = pd.to_datetime([
            "2023-01-03 09:30", "2023-01-03 09:35",
            "2023-01-04 09:30", "2023-01-04 09:35",
        ]).tz_localize("America/New_York")
        df = pd.DataFrame({
            "high": [101, 102, 201, 202],
            "low": [99, 100, 199, 200],
            "close": [100, 101, 200, 201],
            "volume": [1000, 1000, 1000, 1000],
        }, index=dates)
        vwap = compute_vwap(df)
        # First bar of each day: VWAP = typical price
        assert vwap.iloc[0] == pytest.approx((101 + 99 + 100) / 3)
        assert vwap.iloc[2] == pytest.approx((201 + 199 + 200) / 3)

    def test_premarketbars_are_included_in_cumsum(self):
        """Pre-market bars (e.g. 09:25) are included in cumsum — document this behaviour.

        The VWAP implementation does not filter out pre-market bars; callers are
        responsible for passing only regular-hours data.  This test asserts (and
        documents) the current behaviour so any future change is visible.
        """
        dates = pd.to_datetime(["2023-01-03 09:25", "2023-01-03 09:30"]).tz_localize("America/New_York")
        df = pd.DataFrame({
            "high": [105, 101],
            "low": [95, 99],
            "close": [100, 100],
            "volume": [500, 1000],
        }, index=dates)
        vwap = compute_vwap(df)
        # First bar (09:25 pre-market) is the only bar in the cumsum at that point
        premarket_tp = (105 + 95 + 100) / 3.0
        assert vwap.iloc[0] == pytest.approx(premarket_tp)
        # Second bar includes both bars — VWAP is biased by the pre-market bar
        tp_09_30 = (101 + 99 + 100) / 3.0
        expected_vwap = (premarket_tp * 500 + tp_09_30 * 1000) / (500 + 1000)
        assert vwap.iloc[1] == pytest.approx(expected_vwap)


class TestVwapZeroVolume:
    def _make_df_with_zero_vol_first_bar(self):
        """Two-day DataFrame where the first bar of day 2 has volume=0."""
        dates = pd.to_datetime([
            "2023-01-03 09:30", "2023-01-03 09:35",
            "2023-01-04 09:30", "2023-01-04 09:35",
        ]).tz_localize("America/New_York")
        return pd.DataFrame({
            "high":   [101.0, 102.0, 201.0, 202.0],
            "low":    [99.0,  100.0, 199.0, 200.0],
            "close":  [100.0, 101.0, 200.0, 201.0],
            "volume": [1000.0, 1000.0, 0.0, 1000.0],
        }, index=dates)

    def test_zero_volume_bar_produces_no_inf(self):
        """A zero-volume bar must not produce inf in the VWAP series."""
        df = self._make_df_with_zero_vol_first_bar()
        result = compute_vwap(df)
        assert not result.isin([float("inf"), float("-inf")]).any(), (
            "compute_vwap returned inf for a zero-volume bar"
        )

    def test_zero_volume_bar_is_forward_filled(self):
        """A zero-volume bar that is the first bar of the day may be NaN (no prior
        value to fill from), but subsequent bars in the same day must not be NaN/inf."""
        df = self._make_df_with_zero_vol_first_bar()
        result = compute_vwap(df)

        # Day 1 bars (indices 0–1) must be fully valid
        assert not result.iloc[0:2].isna().any(), "Day-1 VWAP has unexpected NaN"
        assert not result.iloc[0:2].isin([float("inf"), float("-inf")]).any()

        # Day 2, bar 1 (index 3) has volume > 0 and follows the zero-volume bar —
        # it must have a valid, finite VWAP value.
        assert not np.isnan(result.iloc[3]), "Day-2 second bar VWAP is NaN after forward-fill"
        assert np.isfinite(result.iloc[3]), "Day-2 second bar VWAP is not finite"


class TestComputeIndicatorsInfDiagnostic:
    def _minimal_config(self, vwap_filter: bool = True) -> dict:
        """Return a minimal config dict accepted by compute_indicators."""
        return {
            "signals": {
                "triggers": [{"indicator": "smi"}, {"indicator": "williams_r"}],
                "smi_fast":    {"period": 5,  "smooth1": 3, "smooth2": 3},
                "smi_slow":    {"period": 13, "smooth1": 3, "smooth2": 3},
                "williams_r":  {"period": 13},
                "vwap_filter": vwap_filter,
            }
        }

    def _make_df_with_zero_vol_first_bar(self, n_bars: int = 60) -> pd.DataFrame:
        """Single-day DataFrame where bar 0 has volume=0 (triggers inf in raw VWAP)."""
        np.random.seed(0)
        dates = pd.date_range("2023-01-03 09:30", periods=n_bars, freq="5min", tz="America/New_York")
        close = 400.0 + np.cumsum(np.random.randn(n_bars) * 0.5)
        high  = close + np.abs(np.random.randn(n_bars) * 0.3)
        low   = close - np.abs(np.random.randn(n_bars) * 0.3)
        open_ = close + np.random.randn(n_bars) * 0.1
        volume = np.random.randint(100_000, 2_000_000, n_bars).astype(float)
        volume[0] = 0.0  # force zero-volume first bar → raw VWAP would be inf
        return pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
            index=dates,
        )

    def test_inf_in_vwap_triggers_warning(self, caplog):
        """compute_indicators should log a warning mentioning 'inf' or 'vwap_indicator'
        when vwap_filter=True and the VWAP column has inf/NaN from a zero-volume bar."""
        config = self._minimal_config(vwap_filter=True)
        df = self._make_df_with_zero_vol_first_bar()

        with caplog.at_level(logging.WARNING):
            compute_indicators(df, config)

        warning_texts = " ".join(r.message for r in caplog.records if r.levelno >= logging.WARNING)
        assert "inf" in warning_texts.lower() or "vwap" in warning_texts.lower(), (
            f"Expected a warning about inf/vwap, got: {warning_texts!r}"
        )

class TestTSI:
    def test_output_shape(self, sample_ohlcv):
        df = compute_tsi(sample_ohlcv, long_period=25, short_period=13, signal_period=7)
        assert len(df) == len(sample_ohlcv)
        assert "tsi" in df.columns
        assert "tsi_signal" in df.columns

    def test_flat_price_yields_nan_not_inf(self):
        """When all prices are identical, TSI result is NaN not inf."""
        df = pd.DataFrame({
            "close": [100.0] * 30,
        })
        res = compute_tsi(df, long_period=5, short_period=3, signal_period=3)
        valid_tsi = res["tsi"].dropna()
        assert not np.isinf(valid_tsi).any(), "TSI produced inf for flat price input"

    def test_different_from_signal(self, sample_ohlcv):
        df = compute_tsi(sample_ohlcv, long_period=25, short_period=13, signal_period=7)
        valid = df.dropna()
        assert not np.allclose(valid["tsi"].values, valid["tsi_signal"].values)


class TestStochRSI:
    def test_output_shape(self, sample_ohlcv):
        df = compute_stoch_rsi(sample_ohlcv, length=14, smooth_k=3, smooth_d=3, rsi_period=14)
        assert len(df) == len(sample_ohlcv)
        assert "stoch_rsi_k" in df.columns
        assert "stoch_rsi_d" in df.columns

    def test_range(self, sample_ohlcv):
        df = compute_stoch_rsi(sample_ohlcv, length=14, smooth_k=3, smooth_d=3, rsi_period=14)
        valid_k = df["stoch_rsi_k"].dropna()
        valid_d = df["stoch_rsi_d"].dropna()
        assert valid_k.max() <= 100.001
        assert valid_k.min() >= -0.001
        assert valid_d.max() <= 100.001
        assert valid_d.min() >= -0.001

    def test_flat_price_yields_nan_not_inf(self):
        """When all prices are identical, StochRSI result is NaN not inf."""
        df = pd.DataFrame({
            "close": [100.0] * 30,
        })
        res = compute_stoch_rsi(df, length=5, smooth_k=3, smooth_d=3, rsi_period=5)
        valid_k = res["stoch_rsi_k"].dropna()
        assert not np.isinf(valid_k).any(), "StochRSI produced inf for flat price input"
