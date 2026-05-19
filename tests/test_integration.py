"""
End-to-end integration tests: compute_indicators → generate_signals → BacktestEngine.run()

Nothing is mocked here. These tests exercise the full signal pipeline and backtest loop
together so that bugs in indicator computation, signal generation, or their handoff to
the engine cannot hide behind mock boundaries.
"""
import numpy as np
import pandas as pd
import pytest

from src.backtest.engine import BacktestEngine
from src.backtest.portfolio import Portfolio

# All valid exit reasons the engine can emit
_VALID_EXIT_REASONS = {
    "profit_target", "stop_loss", "opposite_signal",
    "eod_close", "expiration", "backtest_end",
}
_VALID_SIGNALS = {-1, 0, 1}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlcv(n: int = 200, seed: int = 42) -> pd.DataFrame:
    """Seeded synthetic OHLCV with realistic price variance.

    Uses a random walk so prices move enough to trigger indicator crossovers
    (SMI fast/slow and Williams %R) within 200 bars.
    """
    rng = np.random.default_rng(seed)
    returns = rng.normal(0, 0.003, n)          # ~0.3% per-bar vol
    prices = 400.0 * np.cumprod(1 + returns)
    prices = np.clip(prices, 100, 800)

    idx = pd.date_range("2025-01-02 09:30", periods=n, freq="5min", tz="America/New_York")
    spread = rng.uniform(0.1, 1.0, n)
    return pd.DataFrame(
        {
            "open": prices,
            "high": prices + spread,
            "low": prices - spread,
            "close": prices,
            "volume": rng.integers(100_000, 2_000_000, n).astype(float),
        },
        index=idx,
    )


def _full_config(
    pair_mode: str = "indicator_2_then_indicator_1",
    armed_mode: bool = False,
    vwap_filter: bool = False,
) -> dict:
    """Full config dict that exercises real indicator computation and signal generation."""
    return {
        "strategy": {"trade_mode": "equities", "initial_capital": 100_000},
        "signals": {
            "smi_fast":    {"period": 5,  "smooth1": 3, "smooth2": 3},
            "smi_slow":    {"period": 10, "smooth1": 3, "smooth2": 3},
            "williams_r":  {"period": 7},
            "sync_window": 3,
            "vwap_filter": vwap_filter,
            "pair_mode": pair_mode,
            "armed_mode": armed_mode,
        },
        "exits": {
            "profit_target_pct": 3.0,
            "stop_loss_pct":     3.0,
            "eod_close":         True,
            "opposite_signal":   True,
        },
        "position": {
            "sizing_mode":            "fixed",
            "contracts_per_trade":    1,
            "max_concurrent_positions": 1,
        },
        "costs": {"commission_per_contract": 0.0, "slippage_pct": 0.0},
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEndToEndIndicatorsAndSignals:
    """Verify that compute_indicators and generate_signals produce sane outputs."""

    def test_indicator_columns_present(self):
        """BacktestEngine.__init__ pre-computes all expected indicator columns."""
        df = _make_ohlcv()
        engine = BacktestEngine(config=_full_config(), equity_data=df)
        for col in ("smi_fast", "smi_slow", "williams_r", "signal"):
            assert col in engine.data.columns, f"Missing column: {col}"

    def test_signal_column_only_valid_values(self):
        """Signal column contains only -1, 0, +1 — no other values."""
        df = _make_ohlcv()
        engine = BacktestEngine(config=_full_config(), equity_data=df)
        unique = set(engine.data["signal"].unique())
        assert unique <= _VALID_SIGNALS, f"Unexpected signal values: {unique - _VALID_SIGNALS}"

    def test_indicator_columns_have_no_all_nan(self):
        """After warm-up, indicator columns are not entirely NaN."""
        df = _make_ohlcv(n=200)
        engine = BacktestEngine(config=_full_config(), equity_data=df)
        for col in ("smi_fast", "smi_slow", "williams_r"):
            non_nan = engine.data[col].dropna()
            assert len(non_nan) > 0, f"All values are NaN for {col}"

    def test_williams_r_values_in_range(self):
        """Williams %R is always in [-100, 0] — by definition."""
        df = _make_ohlcv(n=200)
        engine = BacktestEngine(config=_full_config(), equity_data=df)
        wr = engine.data["williams_r"].dropna()
        assert (wr >= -100).all() and (wr <= 0).all(), \
            "Williams %R out of [-100, 0] range"

    def test_seeded_data_produces_at_least_one_signal(self):
        """The seeded random OHLCV data should generate at least one trade signal."""
        df = _make_ohlcv(n=200, seed=42)
        engine = BacktestEngine(config=_full_config(), equity_data=df)
        n_signals = (engine.data["signal"] != 0).sum()
        assert n_signals > 0, (
            "No signals generated — seeded data may not produce crossovers. "
            "Try a different seed or increase bar count."
        )


class TestEndToEndEngine:
    """Verify the engine loop behaves correctly when driven by real (un-mocked) signals."""

    def test_run_returns_portfolio(self):
        """engine.run() completes without error and returns a Portfolio."""
        df = _make_ohlcv()
        engine = BacktestEngine(config=_full_config(), equity_data=df)
        portfolio = engine.run()
        assert isinstance(portfolio, Portfolio)

    def test_no_open_positions_after_run(self):
        """All positions are force-closed by the engine at the last bar."""
        df = _make_ohlcv()
        engine = BacktestEngine(config=_full_config(), equity_data=df)
        portfolio = engine.run()
        assert len(portfolio.positions) == 0

    def test_equity_curve_length_equals_bar_count(self):
        """Equity curve = 1 initial baseline + len(df) bars + 1 final re-record after backtest_end closes."""
        df = _make_ohlcv(n=60)
        engine = BacktestEngine(config=_full_config(), equity_data=df)
        portfolio = engine.run()
        assert len(portfolio.equity_curve) == len(df) + 2

    def test_closed_trades_have_valid_exit_reasons(self):
        """Every closed trade has an exit_reason from the known set."""
        df = _make_ohlcv()
        engine = BacktestEngine(config=_full_config(), equity_data=df)
        portfolio = engine.run()
        for trade in portfolio.closed_trades:
            assert trade["exit_reason"] in _VALID_EXIT_REASONS, \
                f"Unknown exit_reason: {trade['exit_reason']}"

    def test_trade_direction_matches_signal(self):
        """Long signal → 'long' trade; short signal → 'short' trade.

        With next-bar-open fill semantics, entry_time is the fill bar (signal bar + 1).
        We look up the signal on the bar immediately before entry_time.
        """
        df = _make_ohlcv()
        engine = BacktestEngine(config=_full_config(), equity_data=df)
        portfolio = engine.run()
        signals = engine.data["signal"]
        for trade in portfolio.closed_trades:
            entry_ts = pd.Timestamp(trade["entry_time"])
            # entry_ts is the fill bar; the signal fired on the prior bar
            idx = signals.index.get_indexer([entry_ts], method="nearest")[0]
            signal_idx = idx - 1
            if signal_idx < 0:
                continue
            sig = signals.iloc[signal_idx]
            if sig == 0:
                continue  # no signal on prior bar (e.g. opposite-signal re-entry)
            expected = "long" if sig == 1 else "short"
            assert trade["direction"] == expected, (
                f"Signal {sig} at {signals.index[signal_idx]} opened a '{trade['direction']}' trade"
            )

    def test_final_equity_equals_initial_plus_pnl(self):
        """Final equity = initial_capital + sum(all closed trade pnls)."""
        df = _make_ohlcv()
        engine = BacktestEngine(config=_full_config(), equity_data=df)
        portfolio = engine.run()
        total_pnl = sum(t["pnl"] for t in portfolio.closed_trades)
        assert portfolio.get_equity() == pytest.approx(100_000 + total_pnl, abs=0.01)

    def test_equity_never_negative_with_fixed_sizing(self):
        """With 1-contract fixed sizing equity should not go negative on synthetic data."""
        df = _make_ohlcv()
        engine = BacktestEngine(config=_full_config(), equity_data=df)
        portfolio = engine.run()
        for point in portfolio.equity_curve:
            assert point["equity"] > 0, \
                f"Equity went negative at {point['timestamp']}: {point['equity']}"

    def test_trade_entry_times_within_bar_index(self):
        """All entry timestamps exist in the bar index (no phantom entries)."""
        df = _make_ohlcv()
        engine = BacktestEngine(config=_full_config(), equity_data=df)
        portfolio = engine.run()
        bar_index = set(df.index)
        for trade in portfolio.closed_trades:
            entry_ts = pd.Timestamp(trade["entry_time"])
            assert entry_ts in bar_index, f"Entry time {entry_ts} not in bar index"


class TestEndToEndSignalModes:
    """All three pair_mode values and armed_mode run end-to-end without error."""

    @pytest.mark.parametrize("mode", ["indicator_2_then_indicator_1", "indicator_1_then_indicator_2", "either"])
    def test_pair_modes_produce_valid_signals(self, mode):
        """Each pair_mode runs without error and produces valid signal values."""
        df = _make_ohlcv()
        engine = BacktestEngine(config=_full_config(pair_mode=mode), equity_data=df)
        engine.run()
        unique = set(engine.data["signal"].unique())
        assert unique <= _VALID_SIGNALS

    @pytest.mark.parametrize("mode", ["indicator_2_then_indicator_1", "indicator_1_then_indicator_2", "either"])
    def test_armed_mode_closes_all_positions(self, mode):
        """armed_mode=True (all pair modes) closes all positions at end of run."""
        df = _make_ohlcv()
        engine = BacktestEngine(
            config=_full_config(pair_mode=mode, armed_mode=True),
            equity_data=df,
        )
        portfolio = engine.run()
        assert len(portfolio.positions) == 0

    def test_armed_mode_fires_fewer_or_equal_signals_than_non_armed(self):
        """Armed mode should not fire MORE signals than non-armed (it adds a gate)."""
        df = _make_ohlcv()
        engine_armed = BacktestEngine(
            config=_full_config(armed_mode=True), equity_data=df
        )
        engine_std = BacktestEngine(
            config=_full_config(armed_mode=False), equity_data=df
        )
        engine_armed.run()
        engine_std.run()
        n_armed = (engine_armed.data["signal"] != 0).sum()
        n_std = (engine_std.data["signal"] != 0).sum()
        assert n_armed <= n_std, (
            f"Armed mode fired {n_armed} signals but non-armed fired only {n_std}"
        )
