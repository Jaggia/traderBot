"""Tests for Deflated Sharpe Ratio (DSR) implementation in metrics.py.

RG-TDD RED phase: these tests define expected behavior for the new DSR functions
(_norm_ppf, _expected_max_sharpe, _dsr, count_trials) and will fail until
implemented.

Coverage:
  - _norm_ppf: inverse normal CDF accuracy and symmetry
  - _expected_max_sharpe: zero for N=1, monotonically increasing
  - _dsr: degenerates to PSR for N=1, strictly less than PSR for N>1, edge cases
  - count_trials: reads run_key.yaml, defaults to 1
"""

import math
import os

import numpy as np
import pytest
import yaml

from src.analysis.metrics import (
    _dsr,
    _expected_max_sharpe,
    _norm_cdf,
    _norm_ppf,
    _psr,
    compute_metrics,
    count_trials,
)


# ---------------------------------------------------------------------------
# _norm_ppf — inverse normal CDF
# ---------------------------------------------------------------------------

class TestNormPpf:
    def test_median_is_zero(self):
        assert _norm_ppf(0.5) == pytest.approx(0.0, abs=1e-9)

    def test_cdf_of_one(self):
        """Phi(1) ≈ 0.84134, so Phi^{-1}(0.84134) ≈ 1.0."""
        assert _norm_ppf(0.8413447460685429) == pytest.approx(1.0, abs=1e-4)

    def test_95th_percentile(self):
        """Phi^{-1}(0.975) ≈ 1.96."""
        assert _norm_ppf(0.975) == pytest.approx(1.96, abs=0.01)

    def test_symmetry(self):
        """Phi^{-1}(1 - p) = -Phi^{-1}(p)."""
        assert _norm_ppf(0.025) == pytest.approx(-_norm_ppf(0.975), abs=1e-9)

    def test_roundtrip_cdf_ppf(self):
        """Phi(Phi^{-1}(p)) = p for several values."""
        for p in [0.01, 0.1, 0.25, 0.5, 0.75, 0.9, 0.99]:
            assert _norm_cdf(_norm_ppf(p)) == pytest.approx(p, abs=1e-7)

    def test_roundtrip_ppf_cdf(self):
        """Phi^{-1}(Phi(x)) = x for several values."""
        for x in [-3.0, -1.0, 0.0, 1.0, 3.0]:
            assert _norm_ppf(_norm_cdf(x)) == pytest.approx(x, abs=1e-6)


# ---------------------------------------------------------------------------
# _expected_max_sharpe
# ---------------------------------------------------------------------------

class TestExpectedMaxSharpe:
    def test_single_trial_is_zero(self):
        """With N=1 there is no multiple testing adjustment."""
        assert _expected_max_sharpe(1) == pytest.approx(0.0, abs=1e-12)

    def test_two_trials_positive(self):
        """With N=2 the expected max Sharpe under the null is positive."""
        assert _expected_max_sharpe(2) > 0.0

    def test_monotonically_increasing(self):
        """E[max SR] grows with more trials."""
        vals = [_expected_max_sharpe(n) for n in [1, 2, 5, 10, 50, 100, 500]]
        for i in range(len(vals) - 1):
            assert vals[i] < vals[i + 1], (
                f"E[max SR] not monotonic: N={[1,2,5,10,50,100,500][i]} → {vals[i]}, "
                f"N={[1,2,5,10,50,100,500][i+1]} → {vals[i+1]}"
            )

    def test_known_value_n100(self):
        """Hand-computed check: E[max SR] for N=100 should be around 2.5.

        Using the Bailey & López de Prado (2014) formula with EM constant:
        E[max SR] ≈ (1 - γ) * Phi^{-1}(1 - 1/N) + γ * Phi^{-1}(1 - 1/(N*e))
        """
        val = _expected_max_sharpe(100)
        assert 2.0 < val < 3.5, f"Expected ~2.5 for N=100, got {val}"


# ---------------------------------------------------------------------------
# _dsr — Deflated Sharpe Ratio
# ---------------------------------------------------------------------------

class TestDsr:
    def test_n1_equals_psr(self):
        """With N=1, DSR degenerates to PSR (no multiple testing correction)."""
        sharpe = 2.0
        n_obs = 100
        sk = 0.1
        kt = 3.5
        assert _dsr(sharpe, n_trials=1, n_observations=n_obs, skewness=sk, kurtosis=kt) == pytest.approx(
            _psr(sharpe, n_obs, sk, kt), abs=1e-12
        )

    def test_n100_less_than_psr(self):
        """More trials must reduce DSR below PSR (stricter benchmark)."""
        sharpe = 0.4
        n_obs = 100
        sk = 0.1
        kt = 3.5
        psr_val = _psr(sharpe, n_obs, sk, kt)
        dsr_val = _dsr(sharpe, n_trials=100, n_observations=n_obs, skewness=sk, kurtosis=kt)
        assert dsr_val < psr_val

    def test_zero_sharpe_high_n_is_low(self):
        """A zero Sharpe with many trials is almost certainly noise."""
        result = _dsr(0.0, n_trials=100, n_observations=50, skewness=0.0, kurtosis=3.0)
        assert result < 0.05

    def test_none_sharpe_returns_none(self):
        assert _dsr(None, n_trials=10, n_observations=100, skewness=0.0, kurtosis=3.0) is None

    def test_too_few_observations_returns_none(self):
        """Fewer than 3 observations → None (matching PSR behavior)."""
        assert _dsr(2.0, n_trials=10, n_observations=2, skewness=0.0, kurtosis=3.0) is None

    def test_high_sharpe_high_n_still_significant(self):
        """Even with 1000 trials, a Sharpe of 5 with 200 observations should be significant."""
        result = _dsr(5.0, n_trials=1000, n_observations=200, skewness=0.0, kurtosis=3.0)
        assert result > 0.95

    def test_negative_denom_returns_one(self):
        """When denom <= 0 (extreme skew), _dsr delegates to _psr which returns 1.0.

        With skewness=10, sharpe=5: denom = 1 - 50 + 0.5*25 = -36.5 < 0.
        sharpe (5.0) >> benchmark (expected max for 10 trials ≈ 2.0), so _psr returns 1.0.
        """
        result = _dsr(5.0, n_trials=10, n_observations=50, skewness=10.0, kurtosis=3.0)
        assert result == 1.0


# ---------------------------------------------------------------------------
# count_trials
# ---------------------------------------------------------------------------

class TestCountTrials:
    def test_missing_file_returns_one(self, tmp_path):
        assert count_trials(str(tmp_path / "nonexistent.yaml")) == 1

    def test_empty_yaml_returns_one(self, tmp_path):
        path = tmp_path / "run_key.yaml"
        path.write_text("")
        assert count_trials(str(path)) == 1

    def test_counts_keys(self, tmp_path):
        path = tmp_path / "run_key.yaml"
        data = {"variant_a": {"signal_system": "indicator_pair"}, "variant_b": {"signal_system": "ema_233"}}
        path.write_text(yaml.dump(data))
        assert count_trials(str(path)) == 2

    def test_five_variants(self, tmp_path):
        path = tmp_path / "run_key.yaml"
        data = {f"v{i}": {"signal_system": "indicator_pair"} for i in range(5)}
        path.write_text(yaml.dump(data))
        assert count_trials(str(path)) == 5

    def test_current_tag_is_counted_before_run_key_update(self, tmp_path):
        path = tmp_path / "run_key.yaml"
        data = {"variant_a": {"signal_system": "indicator_pair"}}
        path.write_text(yaml.dump(data))
        assert count_trials(str(path), current_tag="variant_b") == 2

    def test_existing_current_tag_is_not_double_counted(self, tmp_path):
        path = tmp_path / "run_key.yaml"
        data = {"variant_a": {"signal_system": "indicator_pair"}}
        path.write_text(yaml.dump(data))
        assert count_trials(str(path), current_tag="variant_a") == 1


# ---------------------------------------------------------------------------
# Integration: compute_metrics with n_trials
# ---------------------------------------------------------------------------

class TestComputeMetricsDsr:
    def test_default_n_trials_no_dsr(self):
        """Without n_trials, DSR should not appear in output."""
        import pandas as pd
        trades = pd.DataFrame({"pnl": [100, -50, 200], "pnl_pct": [1.0, -0.5, 2.0]})
        equity = pd.DataFrame(
            {"equity": [100_000, 110_000, 105_000, 125_000]},
            index=pd.date_range("2026-01-02", periods=4, freq="D", tz="America/New_York"),
        )
        m = compute_metrics(trades, equity)
        assert "dsr" not in m

    def test_n_trials_1_no_dsr(self):
        """n_trials=1 should also not produce DSR (only PSR)."""
        import pandas as pd
        trades = pd.DataFrame({"pnl": [100, -50, 200], "pnl_pct": [1.0, -0.5, 2.0]})
        equity = pd.DataFrame(
            {"equity": [100_000, 110_000, 105_000, 125_000]},
            index=pd.date_range("2026-01-02", periods=4, freq="D", tz="America/New_York"),
        )
        m = compute_metrics(trades, equity, n_trials=1)
        assert "dsr" not in m

    def test_n_trials_gt1_produces_dsr(self):
        """n_trials > 1 should produce a DSR value."""
        import pandas as pd
        trades = pd.DataFrame({"pnl": [100, -50, 200], "pnl_pct": [1.0, -0.5, 2.0]})
        equity = pd.DataFrame(
            {"equity": [100_000, 110_000, 105_000, 125_000]},
            index=pd.date_range("2026-01-02", periods=4, freq="D", tz="America/New_York"),
        )
        m = compute_metrics(trades, equity, n_trials=10)
        assert "dsr" in m
        assert 0.0 <= m["dsr"] <= 1.0

    def test_dsr_less_than_psr_when_n_gt1(self):
        """DSR must be <= PSR for the same data."""
        import pandas as pd
        trades = pd.DataFrame({"pnl": [100, -50, 200], "pnl_pct": [1.0, -0.5, 2.0]})
        equity = pd.DataFrame(
            {"equity": [100_000, 110_000, 105_000, 125_000]},
            index=pd.date_range("2026-01-02", periods=4, freq="D", tz="America/New_York"),
        )
        m = compute_metrics(trades, equity, n_trials=100)
        assert "dsr" in m
        assert "psr" in m
        assert m["dsr"] <= m["psr"] + 1e-12
