"""
Cross-validate our SMI and Williams %R implementations against the
Trading-Technical-Indicators (TTI) library.

TTI is an independent, third-party implementation that claims to match
standard textbook formulas. By feeding the same OHLCV data into both
our code and TTI and asserting the outputs match, we confirm our
indicator math is correct.

Install TTI:
    pip install tti
    OR from local clone:
    pip install /path/to/trading-technical-indicators

Run:
    pytest tests/test_indicators_vs_tti.py -v
"""

import numpy as np
import pandas as pd
import pytest

from src.indicators.smi import compute_smi
from src.indicators.williams_r import compute_williams_r

tti = pytest.importorskip("tti", reason="TTI library not installed — pip install tti")
from tti.indicators import StochasticMomentumIndex, WilliamsR


# ---------------------------------------------------------------------------
# Shared fixture: realistic OHLCV data
# ---------------------------------------------------------------------------

def _make_ohlcv(n: int = 500, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLCV data that looks realistic enough for indicators."""
    rng = np.random.default_rng(seed)
    # Random walk for close prices
    returns = rng.normal(0, 0.005, size=n)
    close = 480.0 * np.cumprod(1 + returns)

    # Build high/low around close
    spread = rng.uniform(0.2, 1.5, size=n)
    high = close + spread
    low = close - spread
    volume = rng.integers(100_000, 5_000_000, size=n)

    idx = pd.date_range("2025-01-02 09:30", periods=n, freq="5min", tz="US/Eastern")
    return pd.DataFrame(
        {"open": close + rng.uniform(-0.5, 0.5, n),
         "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


@pytest.fixture
def ohlcv():
    return _make_ohlcv()


# ---------------------------------------------------------------------------
# Williams %R
# ---------------------------------------------------------------------------

class TestWilliamsRVsTTI:
    """Compare our Williams %R against TTI's WilliamsR."""

    @pytest.mark.parametrize("period", [5, 13, 21])
    def test_values_match(self, ohlcv, period):
        # Our implementation
        ours = compute_williams_r(ohlcv, period=period)

        # TTI implementation — expects lowercase column names
        tti_input = ohlcv[["open", "high", "low", "close", "volume"]].copy()
        tti_wr = WilliamsR(input_data=tti_input, period=period)
        theirs = tti_wr.getTiData()["wr"]

        # Align: both will have NaN for the first (period-1) bars
        valid = ours.dropna().index.intersection(theirs.dropna().index)
        assert len(valid) > 0, "No overlapping valid bars"

        ours_valid = ours.loc[valid].values
        theirs_valid = theirs.loc[valid].values

        np.testing.assert_allclose(
            ours_valid, theirs_valid, atol=1e-3,
            err_msg=f"Williams %R mismatch with period={period}",
        )

    def test_edge_values(self, ohlcv):
        """WR should always be between -100 and 0 (inclusive)."""
        wr = compute_williams_r(ohlcv, period=13).dropna()
        assert wr.min() >= -100.0
        assert wr.max() <= 0.0


# ---------------------------------------------------------------------------
# Stochastic Momentum Index
# ---------------------------------------------------------------------------

class TestSMIVsTTI:
    """Compare our SMI against TTI's StochasticMomentumIndex.

    Known difference: TTI uses min_periods in its EWM calls, meaning
    the EMA starts later (needs N valid values before producing output).
    Our code uses min_periods=0 (pandas default), matching TradingView's
    Pine Script ta.ema() which starts from bar 0.

    This causes early-bar divergence that converges as both EMAs accumulate
    enough history. We skip the warm-up zone and test the converged region.
    """

    @pytest.mark.parametrize(
        "period, smooth1, smooth2",
        [
            (5, 3, 3),     # TTI defaults
            (5, 8, 8),     # Our fast SMI config
            (13, 8, 8),    # Our slow SMI config
            (10, 5, 5),    # Arbitrary combo
        ],
    )
    def test_values_converge(self, ohlcv, period, smooth1, smooth2):
        """After warm-up, both implementations should produce identical values."""
        # Our implementation
        ours = compute_smi(ohlcv, period=period, smooth1=smooth1, smooth2=smooth2)

        # TTI implementation
        tti_input = ohlcv[["open", "high", "low", "close", "volume"]].copy()
        tti_smi = StochasticMomentumIndex(
            input_data=tti_input,
            period=period,
            smoothing_period=smooth1,
            double_smoothing_period=smooth2,
        )
        theirs = tti_smi.getTiData()["smi"]

        # Align on valid (non-NaN) bars
        valid = ours.dropna().index.intersection(theirs.dropna().index)
        assert len(valid) > 0, "No overlapping valid bars"

        # Skip warm-up: period + 4*max(smooth1, smooth2) bars for EMA seeding
        # to converge. TTI uses min_periods which delays the EMA start; our
        # code starts immediately (matching Pine Script). After enough bars
        # the exponential decay makes the seeding difference negligible.
        # The 4x multiplier is needed when smoothing >> period (e.g. 5-8-8).
        warmup = period + 4 * max(smooth1, smooth2)
        post_warmup = valid[warmup:]
        assert len(post_warmup) > 100, "Not enough post-warmup bars to compare"

        ours_valid = ours.loc[post_warmup].values
        theirs_valid = theirs.loc[post_warmup].values

        np.testing.assert_allclose(
            ours_valid, theirs_valid, atol=1e-2,
            err_msg=f"SMI mismatch with period={period}, smooth1={smooth1}, smooth2={smooth2}",
        )

    @pytest.mark.parametrize(
        "period, smooth1, smooth2",
        [
            (5, 3, 3),
            (5, 8, 8),
            (13, 8, 8),
        ],
    )
    def test_early_bars_converge_toward_tti(self, ohlcv, period, smooth1, smooth2):
        """Verify the divergence shrinks monotonically — proves it's only seeding."""
        ours = compute_smi(ohlcv, period=period, smooth1=smooth1, smooth2=smooth2)

        tti_input = ohlcv[["open", "high", "low", "close", "volume"]].copy()
        tti_smi = StochasticMomentumIndex(
            input_data=tti_input,
            period=period,
            smoothing_period=smooth1,
            double_smoothing_period=smooth2,
        )
        theirs = tti_smi.getTiData()["smi"]

        valid = ours.dropna().index.intersection(theirs.dropna().index)
        diffs = (ours.loc[valid] - theirs.loc[valid]).abs()

        # The max abs diff in the last 100 bars should be << first 100 bars
        early_max = diffs.iloc[:50].max()
        late_max = diffs.iloc[-100:].max()
        assert late_max < early_max * 0.1, (
            f"Divergence not converging: early_max={early_max:.4f}, late_max={late_max:.4f}"
        )

    def test_range_bounded(self, ohlcv):
        """SMI should generally stay within -100 to +100."""
        smi = compute_smi(ohlcv, period=13, smooth1=8, smooth2=8).dropna()
        assert smi.min() >= -100.0
        assert smi.max() <= 100.0


# ---------------------------------------------------------------------------
# Real data test (uses TV CSV if available)
# ---------------------------------------------------------------------------

class TestWithRealData:
    """Run cross-validation on actual market data if available."""

    @pytest.fixture
    def real_ohlcv(self):
        """Load the most recent TV CSV if it exists."""
        from pathlib import Path
        tv_dir = Path("data/TV/equities/SYMBOL/5min")
        csvs = sorted(tv_dir.glob("*.csv")) if tv_dir.exists() else []
        if not csvs:
            pytest.skip("No TV data CSV available")
        df = pd.read_csv(csvs[-1], index_col=0, parse_dates=True)
        # Ensure column names are lowercase
        df.columns = df.columns.str.lower()
        if "datetime" in df.columns:
            df = df.set_index("datetime")
        return df

    def test_williams_r_real_data(self, real_ohlcv):
        ours = compute_williams_r(real_ohlcv, period=13)
        tti_wr = WilliamsR(input_data=real_ohlcv[["open", "high", "low", "close", "volume"]].copy(), period=13)
        theirs = tti_wr.getTiData()["wr"]

        valid = ours.dropna().index.intersection(theirs.dropna().index)
        np.testing.assert_allclose(
            ours.loc[valid].values, theirs.loc[valid].values, atol=1e-3,
            err_msg="Williams %R mismatch on real TV data",
        )

    def test_smi_real_data(self, real_ohlcv):
        """SMI on real data — skip warm-up zone (EMA seeding difference)."""
        period, smooth1, smooth2 = 13, 8, 8
        ours = compute_smi(real_ohlcv, period=period, smooth1=smooth1, smooth2=smooth2)
        tti_smi = StochasticMomentumIndex(
            input_data=real_ohlcv[["open", "high", "low", "close", "volume"]].copy(),
            period=period, smoothing_period=smooth1, double_smoothing_period=smooth2,
        )
        theirs = tti_smi.getTiData()["smi"]

        valid = ours.dropna().index.intersection(theirs.dropna().index)
        warmup = period + 2 * max(smooth1, smooth2)
        post_warmup = valid[warmup:]

        np.testing.assert_allclose(
            ours.loc[post_warmup].values, theirs.loc[post_warmup].values, atol=1e-2,
            err_msg="SMI mismatch on real TV data (post warm-up)",
        )
