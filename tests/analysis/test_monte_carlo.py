"""
Tests for src/analysis/monte_carlo.py — bootstrap resampling helpers.

These are pure-function tests: no I/O, no file system, no mocking of math.

Coverage:
  - _simulate_equity_curves: output shape, values bounded to input P&L range
  - _max_drawdown_pct: known drawdown, flat equity, all-gains equity
  - _profit_factor: normal case, no losses (inf), no wins (0.0)
  - _compute_mc_metrics: shape of returned arrays, value ranges
  - _scale_pnl: identity at 1, linear scaling at N
  - run_sizing_validation: output structure, file creation, edge cases
  - Edge cases: single trade, all-positive P&Ls, all-negative P&Ls
"""
import os

import numpy as np
import pandas as pd
import pytest

from src.analysis.monte_carlo import (
    _simulate_equity_curves,
    _compute_mc_metrics,
    _max_consecutive_losses,
    _percentile_rank,
    _scale_pnl,
    run_monte_carlo,
    run_sizing_validation,
)
from src.analysis.metrics import compute_drawdown_pct, compute_profit_factor


# Local adapters matching the old private function signatures used in these tests
def _max_drawdown_pct(equity: np.ndarray) -> float:
    return float((compute_drawdown_pct(equity) * 100).min())


def _profit_factor(pnl: np.ndarray) -> float:
    return compute_profit_factor(pnl)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pnl(*values: float) -> np.ndarray:
    """Convenience: build a float64 ndarray of P&Ls."""
    return np.array(values, dtype=float)


# ---------------------------------------------------------------------------
# _simulate_equity_curves
# ---------------------------------------------------------------------------

class TestSimulateEquityCurves:
    """Bootstrap sampling produces correctly shaped and bounded equity curves."""

    def _run(self, pnl_array, initial_capital=100_000, n_sim=200, seed=42):
        return _simulate_equity_curves(pnl_array, initial_capital, n_sim, seed)

    def test_output_shape_matches_n_sim_and_n_trades(self):
        pnl = _pnl(100, -50, 200, -30, 150)
        curves = self._run(pnl, n_sim=50)
        assert curves.shape == (50, len(pnl))

    def test_output_shape_large_simulation(self):
        pnl = _pnl(100, 200, -50, 300, -100, 150, 250, -75, 400, -200)
        curves = self._run(pnl, n_sim=500)
        assert curves.shape == (500, len(pnl))

    def test_pnl_bounded_to_input_range(self):
        """Bootstrap draws only from the input P&Ls, so per-trade increments
        (equity[i] - equity[i-1]) must be within [min(pnl), max(pnl)]."""
        pnl = _pnl(100, -50, 200, -30, 150)
        curves = self._run(pnl, n_sim=300)

        # Recover per-trade increments from the equity curves
        initial_capital = 100_000
        ec_with_start = np.hstack([np.full((300, 1), initial_capital), curves])
        increments = np.diff(ec_with_start, axis=1)

        assert increments.min() >= pnl.min() - 1e-9
        assert increments.max() <= pnl.max() + 1e-9

    def test_first_column_reflects_initial_capital_plus_one_trade(self):
        """After trade 1, every row must equal initial_capital + some P&L from input."""
        pnl = _pnl(100, -50, 200)
        curves = self._run(pnl, n_sim=100)
        col0 = curves[:, 0]
        valid_values = set(100_000 + p for p in pnl)
        for v in col0:
            assert any(abs(v - vv) < 1e-9 for vv in valid_values), (
                f"col0 value {v} not one of the expected equity levels"
            )

    def test_reproducibility_with_same_seed(self):
        pnl = _pnl(100, -50, 200, -30, 150)
        c1 = self._run(pnl, seed=7)
        c2 = self._run(pnl, seed=7)
        np.testing.assert_array_equal(c1, c2)

    def test_different_seeds_produce_different_results(self):
        pnl = _pnl(100, -50, 200, -30, 150)
        c1 = self._run(pnl, seed=1)
        c2 = self._run(pnl, seed=2)
        assert not np.array_equal(c1, c2)

    def test_single_trade_shape(self):
        pnl = _pnl(500.0)
        curves = self._run(pnl, n_sim=10)
        assert curves.shape == (10, 1)

    def test_single_trade_all_same_value(self):
        """With one trade and fixed P&L, every simulated path is identical."""
        pnl = _pnl(500.0)
        curves = self._run(pnl, n_sim=10, initial_capital=50_000)
        expected = 50_000 + 500.0
        np.testing.assert_allclose(curves, expected)

    def test_all_positive_pnls_equity_always_above_initial(self):
        pnl = _pnl(100, 200, 300, 150, 250)
        curves = self._run(pnl, n_sim=100, initial_capital=10_000)
        assert curves.min() > 10_000

    def test_all_negative_pnls_equity_always_below_initial(self):
        pnl = _pnl(-100, -200, -300, -150, -250)
        curves = self._run(pnl, n_sim=100, initial_capital=100_000)
        assert curves.max() < 100_000

    def test_initial_capital_propagates(self):
        pnl = _pnl(0.0, 0.0, 0.0)
        for cap in (10_000, 50_000, 1_000_000):
            curves = self._run(pnl, initial_capital=cap, n_sim=5)
            np.testing.assert_allclose(curves, cap)


# ---------------------------------------------------------------------------
# _max_drawdown_pct
# ---------------------------------------------------------------------------

class TestMaxDrawdownPct:
    def test_known_drawdown(self):
        """Equity goes 100 → 120 → 90: drawdown from 120 to 90 = -25%."""
        equity = np.array([100.0, 120.0, 90.0])
        result = _max_drawdown_pct(equity)
        expected = (90 - 120) / 120 * 100
        assert result == pytest.approx(expected)

    def test_flat_equity_zero_drawdown(self):
        equity = np.array([100_000.0, 100_000.0, 100_000.0])
        assert _max_drawdown_pct(equity) == pytest.approx(0.0)

    def test_monotonically_increasing_zero_drawdown(self):
        equity = np.array([100.0, 110.0, 120.0, 130.0])
        assert _max_drawdown_pct(equity) == pytest.approx(0.0)

    def test_single_element_zero_drawdown(self):
        equity = np.array([50_000.0])
        assert _max_drawdown_pct(equity) == pytest.approx(0.0)

    def test_all_declining_equity(self):
        """100 → 80 → 60 → 40: full drawdown from peak 100 to trough 40 = -60%."""
        equity = np.array([100.0, 80.0, 60.0, 40.0])
        result = _max_drawdown_pct(equity)
        expected = (40 - 100) / 100 * 100
        assert result == pytest.approx(expected)

    def test_drawdown_uses_running_max_not_first_value(self):
        """Peak is mid-series: 50 → 200 → 100. Drawdown from 200 to 100 = -50%."""
        equity = np.array([50.0, 200.0, 100.0])
        result = _max_drawdown_pct(equity)
        expected = (100 - 200) / 200 * 100
        assert result == pytest.approx(expected)

    def test_returns_float(self):
        equity = np.array([100.0, 90.0])
        assert isinstance(_max_drawdown_pct(equity), float)

    def test_multiple_drawdown_picks_worst(self):
        """Two drawdowns: 120→100 (-16.7%) and 150→50 (-66.7%). Should return -66.7%."""
        equity = np.array([100.0, 120.0, 100.0, 150.0, 50.0])
        result = _max_drawdown_pct(equity)
        expected = (50 - 150) / 150 * 100
        assert result == pytest.approx(expected, rel=1e-5)


# ---------------------------------------------------------------------------
# _profit_factor
# ---------------------------------------------------------------------------

class TestProfitFactor:
    def test_normal_case(self):
        pnl = _pnl(200, 100, -50, -100)
        result = _profit_factor(pnl)
        expected = 300 / 150
        assert result == pytest.approx(expected)

    def test_no_losses_returns_inf(self):
        pnl = _pnl(100, 200, 50)
        assert _profit_factor(pnl) == float("inf")

    def test_no_wins_returns_zero(self):
        pnl = _pnl(-100, -200, -50)
        assert _profit_factor(pnl) == pytest.approx(0.0)

    def test_zero_pnl_trades_yield_inf_profit_factor(self):
        """All-zero P&L trades → gross_profit=0 AND gross_loss=0 → profit_factor=inf (no losses)."""
        pnl = _pnl(0.0, 0.0, 0.0)
        # zero P&L is treated as neither win nor loss by the >= / <= conditions:
        # gross_profit sums pnl > 0 → 0; gross_loss sums abs(pnl[pnl<=0]) → 0
        # profit_factor = gross_profit / gross_loss → division by zero → inf
        assert _profit_factor(pnl) == float("inf")

    def test_returns_float(self):
        pnl = _pnl(100, -50)
        assert isinstance(_profit_factor(pnl), float)

    def test_single_winning_trade(self):
        pnl = _pnl(300.0)
        assert _profit_factor(pnl) == float("inf")

    def test_single_losing_trade(self):
        pnl = _pnl(-300.0)
        assert _profit_factor(pnl) == pytest.approx(0.0)

    def test_balanced_wins_and_losses(self):
        """Equal gross profit and gross loss → profit factor of 1.0."""
        pnl = _pnl(100, -100)
        assert _profit_factor(pnl) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# _compute_mc_metrics
# ---------------------------------------------------------------------------

class TestComputeMcMetrics:
    """_compute_mc_metrics produces correctly shaped and ranged metric arrays."""

    def _make_curves(self, pnl_array, n_sim=100, seed=42, initial_capital=100_000):
        return _simulate_equity_curves(pnl_array, initial_capital, n_sim, seed)

    def test_output_keys(self):
        pnl = _pnl(100, -50, 200, -30, 150)
        curves = self._make_curves(pnl)
        metrics = _compute_mc_metrics(curves, pnl, 100_000)
        assert set(metrics.keys()) == {
            "total_return_pct", "max_drawdown_pct", "win_rate", "profit_factor",
            "calmar_ratio", "max_consec_losses", "ruined",
        }

    def test_array_lengths_equal_n_sim(self):
        n_sim = 75
        pnl = _pnl(100, -50, 200, -30, 150)
        curves = self._make_curves(pnl, n_sim=n_sim)
        metrics = _compute_mc_metrics(curves, pnl, 100_000)
        for key, arr in metrics.items():
            assert len(arr) == n_sim, f"{key} has wrong length"

    def test_total_return_pct_range_all_positive_pnls(self):
        """All-positive P&Ls → every simulation ends with a gain → return > 0."""
        pnl = _pnl(100, 200, 300, 150, 250)
        curves = self._make_curves(pnl, n_sim=200)
        metrics = _compute_mc_metrics(curves, pnl, 100_000)
        assert (metrics["total_return_pct"] > 0).all()

    def test_total_return_pct_range_all_negative_pnls(self):
        """All-negative P&Ls → every simulation ends with a loss → return < 0."""
        pnl = _pnl(-100, -200, -300, -150, -250)
        curves = self._make_curves(pnl, n_sim=200)
        metrics = _compute_mc_metrics(curves, pnl, 100_000)
        assert (metrics["total_return_pct"] < 0).all()

    def test_max_drawdown_pct_always_non_positive(self):
        """Drawdown can only be zero or negative."""
        pnl = _pnl(100, -50, 200, -30, 150, -75, 300)
        curves = self._make_curves(pnl, n_sim=200)
        metrics = _compute_mc_metrics(curves, pnl, 100_000)
        assert (metrics["max_drawdown_pct"] <= 0.0 + 1e-9).all()

    def test_max_drawdown_pct_zero_for_all_positive_pnls(self):
        """Monotonically increasing equity → zero drawdown in every simulation."""
        pnl = _pnl(100, 200, 300, 150, 250)
        curves = self._make_curves(pnl, n_sim=100)
        metrics = _compute_mc_metrics(curves, pnl, 100_000)
        np.testing.assert_allclose(metrics["max_drawdown_pct"], 0.0, atol=1e-9)

    def test_win_rate_bounded_0_to_100(self):
        pnl = _pnl(100, -50, 200, -30, 150)
        curves = self._make_curves(pnl, n_sim=200)
        metrics = _compute_mc_metrics(curves, pnl, 100_000)
        assert (metrics["win_rate"] >= 0).all()
        assert (metrics["win_rate"] <= 100).all()

    def test_win_rate_is_100_for_all_positive_pnls(self):
        pnl = _pnl(100, 200, 300, 150, 250)
        curves = self._make_curves(pnl, n_sim=100)
        metrics = _compute_mc_metrics(curves, pnl, 100_000)
        np.testing.assert_allclose(metrics["win_rate"], 100.0)

    def test_win_rate_is_0_for_all_negative_pnls(self):
        pnl = _pnl(-100, -200, -300, -150, -250)
        curves = self._make_curves(pnl, n_sim=100)
        metrics = _compute_mc_metrics(curves, pnl, 100_000)
        np.testing.assert_allclose(metrics["win_rate"], 0.0)

    def test_profit_factor_inf_for_all_positive_pnls(self):
        pnl = _pnl(100, 200, 300, 150, 250)
        curves = self._make_curves(pnl, n_sim=100)
        metrics = _compute_mc_metrics(curves, pnl, 100_000)
        assert np.all(np.isinf(metrics["profit_factor"]))

    def test_profit_factor_zero_for_all_negative_pnls(self):
        pnl = _pnl(-100, -200, -300, -150, -250)
        curves = self._make_curves(pnl, n_sim=100)
        metrics = _compute_mc_metrics(curves, pnl, 100_000)
        np.testing.assert_allclose(metrics["profit_factor"], 0.0)

    def test_profit_factor_non_negative(self):
        pnl = _pnl(100, -50, 200, -30, 150)
        curves = self._make_curves(pnl, n_sim=200)
        metrics = _compute_mc_metrics(curves, pnl, 100_000)
        assert (metrics["profit_factor"] >= 0).all()

    def test_simulated_pnl_increments_bounded_to_input_range(self):
        """Per-trade P&L increments recovered from equity curves must lie within
        [min(input_pnl), max(input_pnl)] — the bootstrap guarantee."""
        pnl = _pnl(100, -50, 200, -30, 150)
        n_sim = 300
        initial_capital = 100_000
        curves = self._make_curves(pnl, n_sim=n_sim, initial_capital=initial_capital)
        metrics = _compute_mc_metrics(curves, pnl, initial_capital)

        # Recover increments from equity curves
        ec_with_start = np.hstack([np.full((n_sim, 1), initial_capital), curves])
        increments = np.diff(ec_with_start, axis=1)

        assert increments.min() >= pnl.min() - 1e-9
        assert increments.max() <= pnl.max() + 1e-9

    def test_single_trade_edge_case(self):
        """Single trade: equity curve has shape (n_sim, 1) — metrics still computed."""
        pnl = _pnl(500.0)
        curves = self._make_curves(pnl, n_sim=50)
        metrics = _compute_mc_metrics(curves, pnl, 100_000)
        assert len(metrics["total_return_pct"]) == 50
        # All total return should be identical: 500/100000 * 100 = 0.5%
        np.testing.assert_allclose(metrics["total_return_pct"], 0.5)

    def test_mixed_pnl_win_rate_matches_input_fraction(self):
        """With 3 wins and 2 losses bootstrap win rate should cluster around 60%."""
        pnl = _pnl(100, 200, 300, -50, -100)  # 3 out of 5 positive
        curves = self._make_curves(pnl, n_sim=2000, seed=0)
        metrics = _compute_mc_metrics(curves, pnl, 100_000)
        mean_win_rate = metrics["win_rate"].mean()
        # Expected win rate: 60% — allow ±5% tolerance
        assert mean_win_rate == pytest.approx(60.0, abs=5.0)

    def test_output_keys_include_new_metrics(self):
        pnl = _pnl(100, -50, 200, -30, 150)
        curves = self._make_curves(pnl)
        metrics = _compute_mc_metrics(curves, pnl, 100_000)
        assert "calmar_ratio" in metrics
        assert "max_consec_losses" in metrics
        assert "ruined" in metrics

    def test_calmar_inf_when_no_drawdown(self):
        """All-positive P&Ls → zero drawdown → calmar is inf."""
        pnl = _pnl(100, 200, 300, 150, 250)
        curves = self._make_curves(pnl, n_sim=50)
        metrics = _compute_mc_metrics(curves, pnl, 100_000)
        assert np.all(np.isinf(metrics["calmar_ratio"]))

    def test_calmar_positive_when_positive_return_and_drawdown(self):
        """Mixed P&Ls → calmar should be finite and positive for profitable sims."""
        pnl = _pnl(500, -50, 500, -50, 500)
        curves = self._make_curves(pnl, n_sim=100)
        metrics = _compute_mc_metrics(curves, pnl, 100_000)
        finite_calmar = metrics["calmar_ratio"][np.isfinite(metrics["calmar_ratio"])]
        if len(finite_calmar) > 0:
            assert (finite_calmar > 0).all()

    def test_max_consec_losses_non_negative_integers(self):
        pnl = _pnl(100, -50, 200, -30, 150)
        curves = self._make_curves(pnl, n_sim=100)
        metrics = _compute_mc_metrics(curves, pnl, 100_000)
        assert (metrics["max_consec_losses"] >= 0).all()

    def test_max_consec_losses_zero_for_all_wins(self):
        """All-positive P&Ls → no consecutive losses in any simulation."""
        pnl = _pnl(100, 200, 300, 150, 250)
        curves = self._make_curves(pnl, n_sim=50)
        metrics = _compute_mc_metrics(curves, pnl, 100_000)
        np.testing.assert_array_equal(metrics["max_consec_losses"], 0)

    def test_ruined_zero_when_equity_never_approaches_floor(self):
        """Large all-positive P&Ls → equity never drops below 50% floor."""
        pnl = _pnl(10_000, 20_000, 30_000, 15_000, 25_000)
        curves = self._make_curves(pnl, n_sim=100, initial_capital=100_000)
        metrics = _compute_mc_metrics(curves, pnl, 100_000, ruin_floor_pct=0.5)
        np.testing.assert_array_equal(metrics["ruined"], 0.0)

    def test_ruined_one_when_equity_always_below_floor(self):
        """All-negative P&Ls → equity always falls below any reasonable floor."""
        pnl = _pnl(-10_000, -20_000, -30_000, -15_000, -25_000)
        curves = self._make_curves(pnl, n_sim=50, initial_capital=100_000)
        metrics = _compute_mc_metrics(curves, pnl, 100_000, ruin_floor_pct=0.5)
        np.testing.assert_array_equal(metrics["ruined"], 1.0)


# ---------------------------------------------------------------------------
# _max_consecutive_losses
# ---------------------------------------------------------------------------

class TestMaxConsecutiveLosses:
    def test_all_losses(self):
        sim_pnls = np.array([[-100.0, -200.0, -50.0]])
        assert _max_consecutive_losses(sim_pnls)[0] == 3

    def test_no_losses(self):
        sim_pnls = np.array([[100.0, 200.0, 50.0]])
        assert _max_consecutive_losses(sim_pnls)[0] == 0

    def test_alternating(self):
        sim_pnls = np.array([[100.0, -50.0, 100.0, -50.0, 100.0]])
        assert _max_consecutive_losses(sim_pnls)[0] == 1

    def test_streak_of_three(self):
        sim_pnls = np.array([[100.0, -50.0, -50.0, -50.0, 100.0]])
        assert _max_consecutive_losses(sim_pnls)[0] == 3

    def test_multiple_sims(self):
        sim_pnls = np.array([
            [100.0, -50.0, -50.0, 100.0],   # streak of 2
            [-50.0, -50.0, -50.0, -50.0],   # streak of 4
            [100.0, 100.0, 100.0, 100.0],   # streak of 0
        ])
        result = _max_consecutive_losses(sim_pnls)
        assert result[0] == 2
        assert result[1] == 4
        assert result[2] == 0


# ---------------------------------------------------------------------------
# _percentile_rank
# ---------------------------------------------------------------------------

class TestPercentileRank:
    def test_median_value_near_50th(self):
        data = np.arange(1.0, 101.0)  # 1..100
        rank = _percentile_rank(data, 50.0)
        assert rank == pytest.approx(50.0)

    def test_min_value_near_0th(self):
        data = np.arange(1.0, 101.0)
        rank = _percentile_rank(data, 1.0)
        assert rank == pytest.approx(1.0)

    def test_max_value_100th(self):
        data = np.arange(1.0, 101.0)
        rank = _percentile_rank(data, 100.0)
        assert rank == pytest.approx(100.0)

    def test_value_above_all_data_is_100(self):
        data = np.array([1.0, 2.0, 3.0])
        assert _percentile_rank(data, 999.0) == pytest.approx(100.0)

    def test_value_below_all_data_is_0(self):
        data = np.array([10.0, 20.0, 30.0])
        assert _percentile_rank(data, -1.0) == pytest.approx(0.0)

    def test_infinite_value_returns_nan(self):
        data = np.array([1.0, 2.0, 3.0])
        assert np.isnan(_percentile_rank(data, float("inf")))

    def test_empty_finite_data_returns_nan(self):
        data = np.array([float("inf"), float("nan")])
        assert np.isnan(_percentile_rank(data, 1.0))


# ---------------------------------------------------------------------------
# _scale_pnl
# ---------------------------------------------------------------------------

class TestScalePnl:
    def test_scale_by_one_is_identity(self):
        pnl = _pnl(100, -50, 200, -30, 150)
        np.testing.assert_array_equal(_scale_pnl(pnl, 1), pnl)

    def test_scale_by_n_multiplies_all_elements(self):
        pnl = _pnl(100, -50, 200, -30, 150)
        result = _scale_pnl(pnl, 3)
        np.testing.assert_array_equal(result, pnl * 3)


# ---------------------------------------------------------------------------
# run_sizing_validation
# ---------------------------------------------------------------------------

def _write_backtest_csv(path: str, pnl_values: list) -> None:
    """Write a minimal backtest.csv with only the pnl column."""
    df = pd.DataFrame({"pnl": pnl_values})
    df.to_csv(path, index=False)


_MINIMAL_CONFIG = {"strategy": {"initial_capital": 100_000}}


class TestRunSizingValidation:
    """Integration tests for run_sizing_validation — reads/writes files via tmp_path."""

    def _run(self, tmp_path, pnl_values, **kwargs):
        _write_backtest_csv(str(tmp_path / "backtest.csv"), pnl_values)
        defaults = dict(n_simulations=100, seed=42, max_contracts=5)
        defaults.update(kwargs)
        return run_sizing_validation(str(tmp_path), _MINIMAL_CONFIG, **defaults)

    def test_returns_dict_with_required_keys(self, tmp_path):
        result = self._run(tmp_path, [100, -50, 200, -30, 150, 80, -40])
        assert isinstance(result, dict)
        assert "recommended_n" in result
        assert "rows" in result

    def test_rows_length_equals_max_contracts(self, tmp_path):
        result = self._run(tmp_path, [100, -50, 200, -30, 150, 80, -40], max_contracts=4)
        assert len(result["rows"]) == 4

    def test_recommended_n_within_tolerance_all_positive(self, tmp_path):
        """All-positive P&L → no drawdown → every contract level passes → recommended_n == max_contracts."""
        result = self._run(tmp_path, [500, 300, 400, 200, 600, 250, 350],
                           sizing_tolerance_pct=10.0, max_contracts=5)
        assert result["recommended_n"] == 5

    def test_zero_tolerance_recommended_n_is_zero(self, tmp_path):
        """Tolerance of 0% → any drawdown fails → recommended_n == 0."""
        result = self._run(tmp_path, [100, -50, 200, -30, 150, 80, -40],
                           sizing_tolerance_pct=0.0, max_contracts=5)
        assert result["recommended_n"] == 0

    def test_recommended_n_increases_with_tolerance(self, tmp_path):
        """A wider tolerance allows more contracts — recommended_n with 50% tolerance
        should be >= recommended_n with 5% tolerance on the same P&L."""
        pnl = [200, -60, 300, -80, 150, -70, 250, -90, 180, -55]
        _write_backtest_csv(str(tmp_path / "backtest.csv"), pnl)
        result_tight = run_sizing_validation(str(tmp_path), _MINIMAL_CONFIG,
                                             n_simulations=100, seed=0,
                                             sizing_tolerance_pct=2.0, max_contracts=5)
        result_wide = run_sizing_validation(str(tmp_path), _MINIMAL_CONFIG,
                                            n_simulations=100, seed=0,
                                            sizing_tolerance_pct=50.0, max_contracts=5)
        assert result_wide["recommended_n"] >= result_tight["recommended_n"]

    def test_p50_return_scales_linearly_with_n(self, tmp_path):
        """All-positive P&L: P50 return at N=4 should be ≈ 4× the P50 return at N=1."""
        result = self._run(tmp_path, [500, 300, 400, 600, 250, 350, 450],
                           n_simulations=500, seed=0,
                           sizing_tolerance_pct=100.0, max_contracts=4)
        ret_n1 = result["rows"][0]["p50_ret"]
        ret_n4 = result["rows"][3]["p50_ret"]
        assert ret_n4 == pytest.approx(ret_n1 * 4, rel=0.05)

    def test_output_files_created(self, tmp_path):
        self._run(tmp_path, [100, -50, 200, -30, 150, 80, -40])
        mc_dir = tmp_path / "monte_carlo"
        assert (mc_dir / "mc_sizing.md").exists()
        assert (mc_dir / "mc_sizing.png").exists()

    def test_fewer_than_5_trades_returns_empty(self, tmp_path):
        """3 trades → guard fires → returns {} and writes no files."""
        result = self._run(tmp_path, [100, -50, 200])
        assert result == {}
        mc_dir = tmp_path / "monte_carlo"
        assert not (mc_dir / "mc_sizing.md").exists()


# ---------------------------------------------------------------------------
# run_monte_carlo — insufficient trade guard
# ---------------------------------------------------------------------------

class TestRunMonteCarloInsufficientTrades:
    """run_monte_carlo must raise ValueError (not silently return) when
    trade count < 5, so callers cannot miss a failed MC run."""

    def _write_backtest_csv(self, path: str, pnl_values: list) -> None:
        df = pd.DataFrame({"pnl": pnl_values})
        df.to_csv(path, index=False)

    def test_raises_value_error_with_zero_trades(self, tmp_path):
        """Empty trade log → ValueError raised."""
        self._write_backtest_csv(str(tmp_path / "backtest.csv"), [])
        with pytest.raises(ValueError, match="5 trades"):
            run_monte_carlo(str(tmp_path), _MINIMAL_CONFIG, n_simulations=10)

    def test_raises_value_error_with_one_trade(self, tmp_path):
        """1 trade → ValueError raised."""
        self._write_backtest_csv(str(tmp_path / "backtest.csv"), [100.0])
        with pytest.raises(ValueError, match="5 trades"):
            run_monte_carlo(str(tmp_path), _MINIMAL_CONFIG, n_simulations=10)

    def test_raises_value_error_with_four_trades(self, tmp_path):
        """4 trades (one below threshold) → ValueError raised."""
        self._write_backtest_csv(str(tmp_path / "backtest.csv"), [100, -50, 200, -30])
        with pytest.raises(ValueError, match="5 trades"):
            run_monte_carlo(str(tmp_path), _MINIMAL_CONFIG, n_simulations=10)

    def test_error_message_includes_actual_count(self, tmp_path):
        """ValueError message must include the actual trade count for diagnostics."""
        pnl_values = [100, -50, 200]
        self._write_backtest_csv(str(tmp_path / "backtest.csv"), pnl_values)
        with pytest.raises(ValueError, match="3"):
            run_monte_carlo(str(tmp_path), _MINIMAL_CONFIG, n_simulations=10)

    def test_no_output_files_created_when_raises(self, tmp_path):
        """No monte_carlo/ directory or files created when the guard fires."""
        self._write_backtest_csv(str(tmp_path / "backtest.csv"), [100, -50])
        with pytest.raises(ValueError):
            run_monte_carlo(str(tmp_path), _MINIMAL_CONFIG, n_simulations=10)
        mc_dir = tmp_path / "monte_carlo"
        assert not mc_dir.exists()

    def test_exactly_five_trades_does_not_raise(self, tmp_path):
        """Exactly 5 trades meets the threshold — no ValueError should be raised."""
        self._write_backtest_csv(str(tmp_path / "backtest.csv"),
                                 [100, -50, 200, -30, 150])
        # Should complete without raising; we don't assert output files here,
        # just that no ValueError is thrown.
        run_monte_carlo(str(tmp_path), _MINIMAL_CONFIG, n_simulations=10, seed=0)
