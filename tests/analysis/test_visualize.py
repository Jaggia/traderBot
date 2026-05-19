"""
Smoke tests for src/analysis/visualize.py.

Each test calls a chart function with synthetic data and asserts that the
expected .png file is written to disk. No pixel-level assertions are made.

Functions under test:
  - plot_equity_curve(equity_df, title, save_path)
  - plot_drawdown(equity_df, save_path)
  - plot_signals_on_price(price_df, trade_log, save_path)
"""
import matplotlib
matplotlib.use("Agg")  # Must be set before any other matplotlib import

import matplotlib.pyplot as plt
import pandas as pd
import pytest

from src.analysis.visualize import plot_drawdown, plot_equity_curve, plot_signals_on_price


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _equity_df(values: list[float] | None = None, start: str = "2025-01-02") -> pd.DataFrame:
    """Build an equity DataFrame indexed by intraday timestamps."""
    if values is None:
        values = [100_000, 101_000, 102_500, 101_800, 103_000,
                  102_000, 104_000, 103_500, 105_000, 104_500]
    idx = pd.date_range(start, periods=len(values), freq="h", tz="America/New_York")
    return pd.DataFrame({"equity": values}, index=idx)


def _price_df(n: int = 15, start: str = "2025-01-02") -> pd.DataFrame:
    """Build a price DataFrame with a 'close' column indexed by intraday timestamps."""
    import numpy as np
    rng = pd.date_range(start, periods=n, freq="5min", tz="America/New_York")
    closes = 480.0 + np.cumsum(np.random.default_rng(42).normal(0, 0.5, n))
    return pd.DataFrame({"close": closes}, index=rng)


def _trade_log(price_df: pd.DataFrame) -> pd.DataFrame:
    """Build a minimal trade log with columns expected by plot_signals_on_price."""
    idx = price_df.index
    return pd.DataFrame(
        {
            "direction": ["long", "short", "long"],
            "entry_time": [idx[0], idx[4], idx[8]],
            "entry_price": [price_df["close"].iloc[0],
                            price_df["close"].iloc[4],
                            price_df["close"].iloc[8]],
            "exit_time": [idx[3], idx[7], idx[12]],
            "exit_price": [price_df["close"].iloc[3],
                           price_df["close"].iloc[7],
                           price_df["close"].iloc[12]],
        }
    )


# ---------------------------------------------------------------------------
# plot_equity_curve
# ---------------------------------------------------------------------------

class TestPlotEquityCurve:
    def test_creates_png_file(self, tmp_path):
        out = tmp_path / "equity_curve.png"
        plot_equity_curve(_equity_df(), title="Test Equity", save_path=str(out))
        plt.close("all")
        assert out.exists()
        assert out.stat().st_size > 0

    def test_creates_png_with_default_title(self, tmp_path):
        out = tmp_path / "equity_default.png"
        plot_equity_curve(_equity_df(), save_path=str(out))
        plt.close("all")
        assert out.exists()

    def test_no_save_path_does_not_raise(self):
        """Calling without save_path should silently succeed (no file written)."""
        plot_equity_curve(_equity_df())
        plt.close("all")

    def test_flat_equity_curve(self, tmp_path):
        """A perfectly flat equity curve (zero variance) should still render."""
        flat = [100_000] * 10
        out = tmp_path / "equity_flat.png"
        plot_equity_curve(_equity_df(flat), save_path=str(out))
        plt.close("all")
        assert out.exists()

    def test_single_point_equity_curve(self, tmp_path):
        """A single data point should not raise."""
        out = tmp_path / "equity_single.png"
        plot_equity_curve(_equity_df([100_000]), save_path=str(out))
        plt.close("all")
        assert out.exists()


# ---------------------------------------------------------------------------
# plot_drawdown
# ---------------------------------------------------------------------------

class TestPlotDrawdown:
    def test_creates_png_file(self, tmp_path):
        out = tmp_path / "drawdown.png"
        plot_drawdown(_equity_df(), save_path=str(out))
        plt.close("all")
        assert out.exists()
        assert out.stat().st_size > 0

    def test_no_save_path_does_not_raise(self):
        plot_drawdown(_equity_df())
        plt.close("all")

    def test_monotonically_increasing_equity(self, tmp_path):
        """No drawdown (always going up) — fill_between stays at zero, should render fine."""
        rising = [100_000 + i * 1_000 for i in range(10)]
        out = tmp_path / "drawdown_none.png"
        plot_drawdown(_equity_df(rising), save_path=str(out))
        plt.close("all")
        assert out.exists()

    def test_equity_with_known_drawdown(self, tmp_path):
        """Equity dips below previous peak — the chart should still be created."""
        values = [100_000, 120_000, 90_000, 110_000, 105_000]
        out = tmp_path / "drawdown_known.png"
        plot_drawdown(_equity_df(values), save_path=str(out))
        plt.close("all")
        assert out.exists()


# ---------------------------------------------------------------------------
# plot_signals_on_price
# ---------------------------------------------------------------------------

class TestPlotSignalsOnPrice:
    def test_creates_png_file(self, tmp_path):
        price = _price_df()
        trades = _trade_log(price)
        out = tmp_path / "signals.png"
        plot_signals_on_price(price, trades, save_path=str(out))
        plt.close("all")
        assert out.exists()
        assert out.stat().st_size > 0

    def test_no_save_path_does_not_raise(self):
        price = _price_df()
        trades = _trade_log(price)
        plot_signals_on_price(price, trades)
        plt.close("all")

    def test_empty_trade_log(self, tmp_path):
        """An empty trade log should produce a price-only chart without error."""
        price = _price_df()
        empty_trades = pd.DataFrame(
            columns=["direction", "entry_time", "entry_price", "exit_time", "exit_price"]
        )
        out = tmp_path / "signals_empty.png"
        plot_signals_on_price(price, empty_trades, save_path=str(out))
        plt.close("all")
        assert out.exists()

    def test_only_long_trades(self, tmp_path):
        """Trade log with only long entries — short scatter should be empty but not error."""
        price = _price_df()
        idx = price.index
        trades = pd.DataFrame(
            {
                "direction": ["long", "long"],
                "entry_time": [idx[0], idx[5]],
                "entry_price": [price["close"].iloc[0], price["close"].iloc[5]],
                "exit_time": [idx[4], idx[9]],
                "exit_price": [price["close"].iloc[4], price["close"].iloc[9]],
            }
        )
        out = tmp_path / "signals_longs_only.png"
        plot_signals_on_price(price, trades, save_path=str(out))
        plt.close("all")
        assert out.exists()

    def test_only_short_trades(self, tmp_path):
        """Trade log with only short entries — long scatter should be empty but not error."""
        price = _price_df()
        idx = price.index
        trades = pd.DataFrame(
            {
                "direction": ["short", "short"],
                "entry_time": [idx[2], idx[7]],
                "entry_price": [price["close"].iloc[2], price["close"].iloc[7]],
                "exit_time": [idx[5], idx[10]],
                "exit_price": [price["close"].iloc[5], price["close"].iloc[10]],
            }
        )
        out = tmp_path / "signals_shorts_only.png"
        plot_signals_on_price(price, trades, save_path=str(out))
        plt.close("all")
        assert out.exists()
