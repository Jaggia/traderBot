"""Tests for the BaseBacktestRunner split path."""

from pathlib import Path

import pandas as pd

from main_runner.base_runner import BaseBacktestRunner
from main_runner.run_backtest_db import DatabentoRunner
from main_runner.run_backtest_tv import TradingViewRunner
from main_runner.run_backtest_with_alpaca import AlpacaRunner


class _DummyRunner(BaseBacktestRunner):
    source_name = "test"
    warmup_months = 0

    def __init__(self, equity_data: pd.DataFrame):
        self._equity_data = equity_data

    def load_data(self, config: dict, load_start: str | None, end_arg: str | None) -> pd.DataFrame:
        return self._equity_data


class _FakePortfolio:
    def __init__(self, trade_log: pd.DataFrame, equity_curve: pd.DataFrame):
        self._trade_log = trade_log
        self._equity_curve = equity_curve

    def get_trade_log(self) -> pd.DataFrame:
        return self._trade_log

    def get_equity_df(self) -> pd.DataFrame:
        return self._equity_curve


class TestRunnerDataSource:
    def test_named_runners_override_config_data_source(self):
        config = {"data": {"data_source": "databento"}}

        assert DatabentoRunner()._config_with_runner_source(config)["data"]["data_source"] == "databento"
        assert TradingViewRunner()._config_with_runner_source(config)["data"]["data_source"] == "tv"
        assert AlpacaRunner()._config_with_runner_source(config)["data"]["data_source"] == "alpaca"

    def test_data_source_override_does_not_mutate_original_config(self):
        config = {"data": {"data_source": "databento"}}

        updated = TradingViewRunner()._config_with_runner_source(config)

        assert updated["data"]["data_source"] == "tv"
        assert config["data"]["data_source"] == "databento"


class TestIsOosSplit:
    """Verify the runner uses the real timestamp-based split path."""

    def test_runner_split_excludes_warmup_bars(self, monkeypatch, tmp_path):
        idx = pd.date_range("2026-01-02 09:00", periods=8, freq="5min", tz="America/New_York")
        equity_data = pd.DataFrame({"close": range(8)}, index=idx)

        trade_log = pd.DataFrame({
            "entry_time": [idx[4], idx[6]],
            "pnl": [10.0, 20.0],
            "pnl_pct": [1.0, 2.0],
        })
        equity_curve = pd.DataFrame({
            "equity": range(100, 108),
            "cash": range(100, 108),
        }, index=idx)
        portfolio = _FakePortfolio(trade_log, equity_curve)

        captured_metrics: list[tuple[pd.DataFrame, pd.DataFrame]] = []

        class _FakeEngine:
            def __init__(self, config, equity_data, trade_start, oos_start):
                self.data = equity_data

            def run(self):
                return portfolio

        def _capture_metrics(trades: pd.DataFrame, curve: pd.DataFrame, **kwargs) -> dict:
            captured_metrics.append((trades.copy(), curve.copy()))
            final_equity = float(curve["equity"].iloc[-1]) if not curve.empty else 100_000.0
            return {"final_equity": final_equity}

        config = {
            "strategy": {"trade_mode": "equities", "timeframe": "5min", "initial_capital": 100_000},
            "signals": {
                "pair_mode": "either",
                "armed_mode": False,
                "vwap_filter": False,
                "sync_window": 5,
            },
            "options": {"strike_selection": "ATM"},
            "exits": {"profit_target_pct": 20.0, "stop_loss_pct": 20.0},
            "position": {"sizing_pct": 50},
            "backtest": {"is_fraction": 0.5},
            "data": {"data_source": "databento"},
        }

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("main_runner.base_runner._load_config", lambda: config)
        monkeypatch.setattr(
            "main_runner.base_runner._validate_date_args",
            lambda: ("2026-01-02 09:20", "2026-01-02 09:35"),
        )
        # Provider not needed — _DummyRunner overrides load_data and pre_load_check
        # is a no-op via a fake provider
        _fake_provider = type("_FakeProvider", (), {
            "ensure_data": lambda *a, **k: None,
            "should_trim_end": lambda self: True,
        })()
        monkeypatch.setattr("main_runner.base_runner.create_provider", lambda c: _fake_provider)
        monkeypatch.setattr("main_runner.base_runner.count_trials", lambda *a, **k: 1)
        monkeypatch.setattr("main_runner.base_runner.BacktestEngine", _FakeEngine)
        monkeypatch.setattr("main_runner.base_runner.compute_metrics", _capture_metrics)
        monkeypatch.setattr("main_runner.base_runner.compute_buy_hold_benchmark", lambda *a, **k: {})
        monkeypatch.setattr("main_runner.base_runner.print_metrics", lambda *a, **k: None)
        monkeypatch.setattr("main_runner.base_runner.print_benchmark", lambda *a, **k: None)
        monkeypatch.setattr("main_runner.base_runner.save_report_md", lambda *a, **k: None)
        monkeypatch.setattr("main_runner.base_runner.save_config_snapshot", lambda *a, **k: None)
        monkeypatch.setattr("main_runner.base_runner.plot_equity_curve", lambda *a, **k: None)
        monkeypatch.setattr("main_runner.base_runner.plot_drawdown", lambda *a, **k: None)
        monkeypatch.setattr("main_runner.base_runner.plot_signals_on_price", lambda *a, **k: None)
        monkeypatch.setattr("main_runner.base_runner._update_run_key", lambda *a, **k: None)

        _DummyRunner(equity_data).run()

        assert len(captured_metrics) == 2

        is_trades, is_curve = captured_metrics[0]
        oos_trades, oos_curve = captured_metrics[1]

        assert list(is_curve.index) == [idx[4], idx[5]]
        assert list(oos_curve.index) == [idx[6], idx[7]]
        assert list(is_trades["entry_time"]) == [idx[4]]
        assert list(oos_trades["entry_time"]) == [idx[6]]
        assert all(ts >= pd.Timestamp("2026-01-02 09:20", tz="America/New_York") for ts in oos_curve.index)

        assert Path("results").exists()

    def _make_runner_harness(self, monkeypatch, tmp_path, equity_data, trade_log, equity_curve, config):
        """Return a (runner, captured_metrics, captured_oos_starts) triple ready to call .run()."""
        portfolio = _FakePortfolio(trade_log, equity_curve)
        captured_metrics: list[tuple[pd.DataFrame, pd.DataFrame]] = []
        captured_oos_starts: list = []

        class _FakeEngine:
            def __init__(self_inner, config, equity_data, trade_start, oos_start):
                self_inner.data = equity_data
                captured_oos_starts.append(oos_start)

            def run(self_inner):
                return portfolio

        def _capture_metrics(trades: pd.DataFrame, curve: pd.DataFrame, **kwargs) -> dict:
            captured_metrics.append((trades.copy(), curve.copy()))
            final_equity = float(curve["equity"].iloc[-1]) if not curve.empty else 100_000.0
            return {"final_equity": final_equity}

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("main_runner.base_runner._load_config", lambda: config)
        # Provider not needed — _DummyRunner overrides load_data
        _fake_provider = type("_FakeProvider", (), {
            "ensure_data": lambda *a, **k: None,
            "should_trim_end": lambda self: True,
        })()
        monkeypatch.setattr("main_runner.base_runner.create_provider", lambda c: _fake_provider)
        monkeypatch.setattr("main_runner.base_runner.count_trials", lambda *a, **k: 1)
        monkeypatch.setattr("main_runner.base_runner.BacktestEngine", _FakeEngine)
        monkeypatch.setattr("main_runner.base_runner.compute_metrics", _capture_metrics)
        monkeypatch.setattr("main_runner.base_runner.compute_buy_hold_benchmark", lambda *a, **k: {})
        monkeypatch.setattr("main_runner.base_runner.print_metrics", lambda *a, **k: None)
        monkeypatch.setattr("main_runner.base_runner.print_benchmark", lambda *a, **k: None)
        monkeypatch.setattr("main_runner.base_runner.save_report_md", lambda *a, **k: None)
        monkeypatch.setattr("main_runner.base_runner.save_config_snapshot", lambda *a, **k: None)
        monkeypatch.setattr("main_runner.base_runner.plot_equity_curve", lambda *a, **k: None)
        monkeypatch.setattr("main_runner.base_runner.plot_drawdown", lambda *a, **k: None)
        monkeypatch.setattr("main_runner.base_runner.plot_signals_on_price", lambda *a, **k: None)
        monkeypatch.setattr("main_runner.base_runner._update_run_key", lambda *a, **k: None)

        return _DummyRunner(equity_data), captured_metrics, captured_oos_starts

    def test_split_boundary_bar_appears_in_oos_only(self, monkeypatch, tmp_path):
        """Bug 1 regression: the split bar must appear in OOS only, never in both IS and OOS.

        Uses is_fraction=0.5 with 10 bars so the raw oos_start lands exactly on bar idx[5].
        A buggy inclusive slice (df.loc[:oos_start]) would put idx[5] in both curves.
        The correct exclusive slice keeps idx[5] in OOS only.
        """
        # 10 bars so 0.5 fraction gives (end - trade_start) * 0.5 which we verify
        # trade_start = idx[0], end = idx[9]
        # oos_start_raw = idx[0] + (idx[9]-idx[0]) * 0.5 = idx[0] + 22.5min = 09:52:30
        # bfill snaps to idx[5] = 09:55  ... actually let's choose carefully:
        # Use trade_start=idx[0], 10 bars at 10min freq so spacing makes 0.5 hit exactly
        idx = pd.date_range("2026-01-05 09:00", periods=10, freq="10min", tz="America/New_York")
        # trade_start = idx[0] = 09:00, end = idx[9] = 10:30
        # oos_start_raw = 09:00 + (10:30 - 09:00) * 0.5 = 09:00 + 45min = 09:45 = idx[4+1] = idx[4]?
        # 10*10min = 100min, 0.5 * 90min gap (idx[0] to idx[9]) = 45min → 09:45 = idx[4] + 5min = idx[4]+5min
        # idx[4] = 09:40, idx[5] = 09:50... wait, let me use different setup
        # Use 6 bars at 30min: idx[0]=09:00, idx[5]=11:30. 0.5 → 09:00 + 2.5h = 11:30 ≠ but 2.5h = idx[5]
        # Better: 4 bars at 60min → idx[0]=09:00..idx[3]=12:00. 0.5*(3h)=1.5h = 10:30, not on bar
        # Cleanest: use exact midpoint. 2 bars gap = 10min, 0.5*20min = 10min = exactly idx[2]
        # idx[0]=09:00, idx[1]=09:10, idx[2]=09:20, idx[3]=09:30
        # trade_start=idx[0], end=idx[3], 0.5*(30min)=15min → 09:15, NOT on bar
        # Let's just use unambiguous bfill: confirm the snapped bar is in OOS only
        idx = pd.date_range("2026-01-05 09:30", periods=8, freq="5min", tz="America/New_York")
        # trade_start=idx[0]=09:30, end=idx[7]=10:05
        # oos_start_raw = 09:30 + 0.5*(35min) = 09:30 + 17.5min = 09:47:30
        # bfill → idx[4] = 09:50
        # So oos_start = idx[4] = 09:50

        equity_data = pd.DataFrame({"close": range(8)}, index=idx)
        trade_log = pd.DataFrame({
            "entry_time": [idx[2], idx[5]],
            "pnl": [10.0, 20.0],
            "pnl_pct": [1.0, 2.0],
        })
        equity_curve = pd.DataFrame({
            "equity": range(100, 108),
            "cash": range(100, 108),
        }, index=idx)

        config = {
            "strategy": {"trade_mode": "equities", "timeframe": "5min", "initial_capital": 100_000},
            "signals": {
                "pair_mode": "either",
                "armed_mode": False,
                "vwap_filter": False,
                "sync_window": 5,
            },
            "options": {"strike_selection": "ATM"},
            "exits": {"profit_target_pct": 20.0, "stop_loss_pct": 20.0},
            "position": {"sizing_pct": 50},
            "backtest": {"is_fraction": 0.5},
            "data": {"data_source": "databento"},
        }

        monkeypatch.setattr(
            "main_runner.base_runner._validate_date_args",
            lambda: ("2026-01-05 09:30", "2026-01-05 10:05"),
        )
        runner, captured_metrics, captured_oos_starts = self._make_runner_harness(
            monkeypatch, tmp_path, equity_data, trade_log, equity_curve, config
        )
        runner.run()

        assert len(captured_metrics) == 2
        is_trades, is_curve = captured_metrics[0]
        oos_trades, oos_curve = captured_metrics[1]

        # The split bar (oos_start = idx[4] = 09:50) must appear in OOS only
        is_ts = set(is_curve.index)
        oos_ts = set(oos_curve.index)
        overlap = is_ts & oos_ts
        assert overlap == set(), (
            f"IS and OOS equity curves share timestamps: {overlap}. "
            "The split boundary bar must appear in OOS only."
        )

        # Confirm oos_start is idx[4] and that bar is in OOS
        oos_start = captured_oos_starts[0]
        assert oos_start in oos_ts, f"oos_start {oos_start} must be in OOS curve, got {sorted(oos_ts)}"
        assert oos_start not in is_ts, f"oos_start {oos_start} must NOT be in IS curve"

    def test_oos_start_snapped_to_next_bar_when_fraction_is_off_grid(self, monkeypatch, tmp_path):
        """Bug 2 regression: when is_fraction produces a fractional timestamp, oos_start
        must snap FORWARD to the first bar AT OR AFTER the raw split point.

        Uses is_fraction=0.33 which produces a timestamp like 09:44:51 that doesn't
        match any 5-min bar. The runner must snap to the next bar (09:45) not the
        previous one (09:40), so no IS bar leaks into the OOS period.
        """
        idx = pd.date_range("2026-01-06 09:30", periods=10, freq="5min", tz="America/New_York")
        # trade_start=idx[0]=09:30, end=idx[9]=10:15
        # is_fraction=0.33 → oos_start_raw = 09:30 + 0.33*(45min) = 09:30 + 14.85min = 09:44:51
        # Correct snap: bfill → idx[3] = 09:45 (first bar AT OR AFTER 09:44:51)
        # Wrong snap (asof/ffill) → idx[2] = 09:40 (bar BEFORE, leaks IS bars into OOS)

        equity_data = pd.DataFrame({"close": range(10)}, index=idx)
        trade_log = pd.DataFrame({
            "entry_time": [idx[3], idx[7]],
            "pnl": [5.0, 15.0],
            "pnl_pct": [0.5, 1.5],
        })
        equity_curve = pd.DataFrame({
            "equity": range(100, 110),
            "cash": range(100, 110),
        }, index=idx)

        config = {
            "strategy": {"trade_mode": "equities", "timeframe": "5min", "initial_capital": 100_000},
            "signals": {
                "pair_mode": "either",
                "armed_mode": False,
                "vwap_filter": False,
                "sync_window": 5,
            },
            "options": {"strike_selection": "ATM"},
            "exits": {"profit_target_pct": 20.0, "stop_loss_pct": 20.0},
            "position": {"sizing_pct": 50},
            "backtest": {"is_fraction": 0.33},
            "data": {"data_source": "databento"},
        }

        monkeypatch.setattr(
            "main_runner.base_runner._validate_date_args",
            lambda: ("2026-01-06 09:30", "2026-01-06 10:15"),
        )
        runner, captured_metrics, captured_oos_starts = self._make_runner_harness(
            monkeypatch, tmp_path, equity_data, trade_log, equity_curve, config
        )
        runner.run()

        oos_start = captured_oos_starts[0]

        # oos_start must be an actual bar timestamp, not a fractional time
        assert oos_start in set(idx), (
            f"oos_start {oos_start} is not an actual bar timestamp. "
            "It must be snapped to the nearest bar in the DataFrame."
        )

        # The snap must go FORWARD (to idx[3]=09:45), not backward (idx[2]=09:40)
        # Raw oos_start_raw ≈ 09:44:51; forward snap = 09:45; backward snap = 09:40
        expected_oos_start = idx[3]  # 09:45 — first bar at or after 09:44:51
        assert oos_start == expected_oos_start, (
            f"Expected oos_start to snap forward to {expected_oos_start}, got {oos_start}. "
            "Snap must go to first bar AT OR AFTER the raw split point to avoid IS territory leaking into OOS."
        )

        # Confirm no IS bar leaks into OOS: every OOS bar must be >= oos_start
        assert len(captured_metrics) == 2
        _, oos_curve = captured_metrics[1]
        assert all(ts >= oos_start for ts in oos_curve.index), (
            f"OOS curve contains bars before oos_start {oos_start}: {list(oos_curve.index)}"
        )
