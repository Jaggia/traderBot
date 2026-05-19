"""
Tests for src/analysis/metrics.py — compute_metrics() and compute_buy_hold_benchmark().

compute_metrics() is a pure function: trade log + equity curve in, dict out.
No I/O, no external dependencies.

Coverage:
  - Empty trade log returns only total_trades key
  - win_rate, avg_win, avg_loss calculated correctly
  - profit_factor: gross_profit / gross_loss; inf when no losses
  - total_pnl and avg_pnl_pct
  - exit_reason breakdown when column present
  - max_drawdown_pct on equity curve with known drawdown
  - total_return_pct and final_equity
  - sharpe_ratio is non-zero for a trending equity curve
  - sortino_ratio is inf when there are no losing days
  - compute_buy_hold_benchmark: empty series, with first_trade_price, without
"""
import numpy as np
import pandas as pd
import pytest

from src.analysis.metrics import compute_metrics, compute_buy_hold_benchmark


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _trade_log(pnls: list[float], pnl_pcts: list[float] | None = None) -> pd.DataFrame:
    """Build a minimal trade log with pnl and pnl_pct columns."""
    if pnl_pcts is None:
        pnl_pcts = [p / 100.0 for p in pnls]
    return pd.DataFrame({"pnl": pnls, "pnl_pct": pnl_pcts})


def _equity_curve(values: list[float], start: str = "2025-01-02") -> pd.DataFrame:
    """Build an equity curve DataFrame indexed by daily timestamps."""
    idx = pd.date_range(start, periods=len(values), freq="D", tz="America/New_York")
    return pd.DataFrame({"equity": values}, index=idx)


# ---------------------------------------------------------------------------
# Empty trade log
# ---------------------------------------------------------------------------

class TestEmptyTradeLog:
    def test_returns_total_trades_only(self):
        metrics = compute_metrics(_trade_log([]), _equity_curve([100_000]))
        assert metrics == {"total_trades": 0}


# ---------------------------------------------------------------------------
# Trade statistics
# ---------------------------------------------------------------------------

class TestTradeStatistics:
    def setup_method(self):
        # 3 wins (+200, +100, +150), 2 losses (-50, -100)
        self.trades = _trade_log([200, 100, 150, -50, -100], [2.0, 1.0, 1.5, -0.5, -1.0])
        self.m = compute_metrics(self.trades, pd.DataFrame())

    def test_total_trades(self):
        assert self.m["total_trades"] == 5

    def test_winning_and_losing_counts(self):
        assert self.m["winning_trades"] == 3
        assert self.m["losing_trades"] == 2

    def test_win_rate(self):
        assert self.m["win_rate"] == pytest.approx(60.0)

    def test_avg_win(self):
        assert self.m["avg_win"] == pytest.approx(round((200 + 100 + 150) / 3, 2))

    def test_avg_loss(self):
        assert self.m["avg_loss"] == pytest.approx(round((-50 + -100) / 2, 2))

    def test_total_pnl(self):
        assert self.m["total_pnl"] == pytest.approx(round(200 + 100 + 150 - 50 - 100, 2))

    def test_avg_pnl_pct(self):
        expected = round((2.0 + 1.0 + 1.5 - 0.5 - 1.0) / 5, 2)
        assert self.m["avg_pnl_pct"] == pytest.approx(expected)

    def test_profit_factor(self):
        gross_profit = 200 + 100 + 150
        gross_loss = 50 + 100
        assert self.m["profit_factor"] == pytest.approx(round(gross_profit / gross_loss, 2))


class TestProfitFactorEdgeCases:
    def test_no_losses_capped(self):
        trades = _trade_log([100, 200, 50])
        m = compute_metrics(trades, pd.DataFrame())
        assert m["profit_factor"] == 999.99

    def test_all_losses_yields_zero_profit_factor(self):
        trades = _trade_log([-100, -200])
        m = compute_metrics(trades, pd.DataFrame())
        # gross_profit = 0, gross_loss > 0 → profit_factor = 0.0
        assert m["profit_factor"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Exit reason breakdown
# ---------------------------------------------------------------------------

class TestExitReasons:
    def test_exit_reason_counts(self):
        trades = _trade_log([100, -50, 200, -30, 150])
        trades["exit_reason"] = ["profit_target", "stop_loss", "profit_target", "stop_loss", "eod"]
        m = compute_metrics(trades, pd.DataFrame())
        assert m["exit_reasons"]["profit_target"] == 2
        assert m["exit_reasons"]["stop_loss"] == 2
        assert m["exit_reasons"]["eod"] == 1

    def test_no_exit_reason_column_ok(self):
        trades = _trade_log([100])
        m = compute_metrics(trades, pd.DataFrame())
        assert "exit_reasons" not in m


# ---------------------------------------------------------------------------
# Equity curve statistics
# ---------------------------------------------------------------------------

class TestEquityCurveStatistics:
    def test_final_equity_and_total_return(self):
        trades = _trade_log([100])
        equity = _equity_curve([100_000, 105_000])
        m = compute_metrics(trades, equity)
        assert m["final_equity"] == pytest.approx(105_000.0)
        assert m["total_return_pct"] == pytest.approx(5.0)

    def test_max_drawdown_flat_is_zero(self):
        trades = _trade_log([100])
        equity = _equity_curve([100_000, 100_000, 100_000])
        m = compute_metrics(trades, equity)
        assert m["max_drawdown_pct"] == pytest.approx(0.0)

    def test_max_drawdown_known_value(self):
        """Equity goes 100→120→90: drawdown from peak 120 to 90 = -25%."""
        trades = _trade_log([100])
        equity = _equity_curve([100, 120, 90])
        m = compute_metrics(trades, equity)
        expected = round((90 - 120) / 120 * 100, 2)
        assert m["max_drawdown_pct"] == pytest.approx(expected)

    def test_sharpe_nonzero_for_trending_equity(self):
        """A steadily rising equity curve should produce a positive Sharpe ratio."""
        trades = _trade_log([100])
        # 90 daily steps of 1% growth — spans 3 months so resample("ME") gives 2+ returns
        values = [100_000 * (1.01 ** i) for i in range(90)]
        equity = _equity_curve(values)
        m = compute_metrics(trades, equity)
        assert m["sharpe_ratio"] > 0

    def test_sharpe_none_when_insufficient_months(self):
        """Too few monthly returns → Sharpe = None (not enough data)."""
        trades = _trade_log([0])
        equity = _equity_curve([100_000, 100_000, 100_000, 100_000])
        m = compute_metrics(trades, equity)
        assert m["sharpe_ratio"] is None

    def test_sortino_inf_when_no_down_days(self):
        """No negative monthly returns → Sortino = inf (positive mean, no downside)."""
        trades = _trade_log([100])
        # 90 daily steps of steady growth — spans 3 months, all monthly returns positive
        values = [100_000 * (1.005 ** i) for i in range(90)]
        equity = _equity_curve(values)
        m = compute_metrics(trades, equity)
        assert m["sortino_ratio"] == float("inf")

    def test_empty_equity_curve_skips_equity_metrics(self):
        trades = _trade_log([100])
        m = compute_metrics(trades, pd.DataFrame())
        assert "sharpe_ratio" not in m
        assert "max_drawdown_pct" not in m
        assert "final_equity" not in m


# ---------------------------------------------------------------------------
# Sharpe / Sortino exact numeric values
# ---------------------------------------------------------------------------

class TestSharpeAndSortinoNumericValues:
    """Verify _sharpe() and _sortino() produce exact values against manual calculations."""

    def test_sharpe_exact_value_known_returns(self):
        """Monthly returns [+10%, +5%] → Sharpe ≈ 2.07.

        Equity spans Jan–Mar so resample("ME").last() gives 3 month-end values,
        pct_change().dropna() gives 2 returns: 0.10, 0.05.
        mean=0.075, std=0.03536 (ddof=1), rfr=0.02/12.
        Sharpe = (0.075 - 0.001667) / 0.03536 ≈ 2.07 (rounded to 2dp).
        """
        trades = _trade_log([100])

        # Build daily equity: Dec=100k, Jan=110k, Feb=115.5k
        dec_days = pd.date_range("2024-12-31", "2024-12-31", freq="D", tz="America/New_York")
        jan_days = pd.date_range("2025-01-01", "2025-01-31", freq="D", tz="America/New_York")
        feb_days = pd.date_range("2025-02-01", "2025-02-28", freq="D", tz="America/New_York")
        idx = dec_days.union(jan_days).union(feb_days)
        values = (
            [100_000.0] * len(dec_days)
            + [110_000.0] * len(jan_days)
            + [115_500.0] * len(feb_days)
        )
        equity = pd.DataFrame({"equity": values}, index=idx)

        m = compute_metrics(trades, equity)

        # Manual: (0.075 - 0.02/12) / std([0.10, 0.05]) = 0.07333 / 0.03536 = 2.07
        assert m["sharpe_ratio"] == pytest.approx(2.07, abs=0.01)

    def test_sortino_exact_value_mixed_returns(self):
        """Monthly returns [+10%, -5%, +3%] → Sortino ≈ 0.87.

        DD = sqrt(mean(min(0,r)^2)) over all 3 returns = sqrt(0.0025/3) ≈ 0.02887.
        mean=0.02667, Sortino = (0.02667 - 0.001667) / 0.02887 ≈ 0.87.
        """
        trades = _trade_log([100])

        dec_days = pd.date_range("2024-12-31", "2024-12-31", freq="D", tz="America/New_York")
        jan_days = pd.date_range("2025-01-01", "2025-01-31", freq="D", tz="America/New_York")
        feb_days = pd.date_range("2025-02-01", "2025-02-28", freq="D", tz="America/New_York")
        mar_days = pd.date_range("2025-03-01", "2025-03-31", freq="D", tz="America/New_York")
        idx = dec_days.union(jan_days).union(feb_days).union(mar_days)
        # Month-end values: 100k → 110k (+10%) → 104.5k (-5%) → 107635 (+3%)
        values = (
            [100_000.0] * len(dec_days)
            + [110_000.0] * len(jan_days)
            + [104_500.0] * len(feb_days)
            + [107_635.0] * len(mar_days)
        )
        equity = pd.DataFrame({"equity": values}, index=idx)

        m = compute_metrics(trades, equity)

        assert m["sortino_ratio"] == pytest.approx(0.87, abs=0.01)

    def test_sortino_finite_with_mixed_returns(self):
        """Monthly returns with ups and downs → finite positive Sortino."""
        trades = _trade_log([100])

        jan_days = pd.date_range("2025-01-02", "2025-01-31", freq="D", tz="America/New_York")
        feb_days = pd.date_range("2025-02-01", "2025-02-28", freq="D", tz="America/New_York")
        mar_days = pd.date_range("2025-03-01", "2025-03-31", freq="D", tz="America/New_York")
        apr_days = pd.date_range("2025-04-01", "2025-04-30", freq="D", tz="America/New_York")
        idx = jan_days.union(feb_days).union(mar_days).union(apr_days)
        values = (
            [100_000.0] * len(jan_days)
            + [108_000.0] * len(feb_days)
            + [102_000.0] * len(mar_days)
            + [110_000.0] * len(apr_days)
        )
        equity = pd.DataFrame({"equity": values}, index=idx)

        m = compute_metrics(trades, equity)

        assert 0 < m["sortino_ratio"] < float("inf")


# ---------------------------------------------------------------------------
# Buy & hold benchmark
# ---------------------------------------------------------------------------

class TestBuyHoldBenchmark:
    def _price_series(self, prices: list[float]) -> pd.Series:
        idx = pd.date_range("2025-01-02", periods=len(prices), freq="D")
        return pd.Series(prices, index=idx)

    def test_empty_series_returns_empty_dict(self):
        assert compute_buy_hold_benchmark(pd.Series([], dtype=float), 100_000, 105_000) == {}

    def test_single_bar_returns_empty_dict(self):
        assert compute_buy_hold_benchmark(self._price_series([500.0]), 100_000, 105_000) == {}

    def test_known_values_with_first_trade_price(self):
        """Buy at 400, sell at 500 on 100k capital.
        shares = 100000/400 = 250, pnl = 250*(500-400) = 25000, pct = 25%.
        """
        prices = self._price_series([400.0, 500.0])
        result = compute_buy_hold_benchmark(prices, 100_000, 130_000, first_trade_price=400.0)
        assert result["bh_return_usd"] == pytest.approx(25_000.0)
        assert result["bh_return_pct"] == pytest.approx(25.0)
        assert result["bh_outperformance_usd"] == pytest.approx(130_000 - (100_000 + 25_000))

    def test_fallback_uses_first_bar_when_no_first_trade_price(self):
        """Without first_trade_price, uses price_series.iloc[0] as entry."""
        prices = self._price_series([400.0, 500.0])
        result = compute_buy_hold_benchmark(prices, 100_000, 130_000)
        assert result["bh_return_usd"] == pytest.approx(25_000.0)
        assert result["bh_return_pct"] == pytest.approx(25.0)

    def test_negative_bh_return(self):
        """Price falls: strategy can outperform even with negative B&H."""
        prices = self._price_series([500.0, 400.0])
        result = compute_buy_hold_benchmark(prices, 100_000, 98_000, first_trade_price=500.0)
        assert result["bh_return_usd"] == pytest.approx(-20_000.0)
        assert result["bh_return_pct"] == pytest.approx(-20.0)
        assert result["bh_outperformance_usd"] == pytest.approx(98_000 - (100_000 - 20_000))
