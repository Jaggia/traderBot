"""
Tests for BacktestEngine execution mechanics.

Signal injection uses a MockStrategy that returns pre-built DataFrames and
signal Series — we're testing the engine's fill logic, not the signal generator.
"""
import contextlib

import pandas as pd
import pytest
from unittest.mock import patch

from src.backtest.engine import BacktestEngine
from src.backtest.portfolio import Portfolio
from src.options.position import Position
from tests.conftest import MockStrategy


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_bars(n, base_price=400.0, start="2025-01-02 09:30", freq="5min") -> pd.DataFrame:
    """Flat OHLCV bars at base_price with tight default high/low (±0.1%)."""
    idx = pd.date_range(start, periods=n, freq=freq, tz="America/New_York")
    return pd.DataFrame(
        {
            "open": base_price,
            "high": base_price * 1.001,   # 400.4 — well inside any 20% TP
            "low": base_price * 0.999,    # 399.6 — well inside any 20% SL
            "close": base_price,
            "volume": 1_000_000,
        },
        index=idx,
    )


def _config(tp=20.0, sl=20.0, eod_close=False, opp_signal=True, max_pos=1, contracts=10):
    """Minimal equities config. Zero costs for clean P&L math."""
    return {
        "strategy": {"trade_mode": "equities", "initial_capital": 100_000},
        "exits": {
            "profit_target_pct": tp,
            "stop_loss_pct": sl,
            "eod_close": eod_close,
            "opposite_signal": opp_signal,
        },
        "position": {
            "sizing_mode": "fixed",
            "contracts_per_trade": contracts,
            "max_concurrent_positions": max_pos,
        },
        "costs": {"commission_per_contract": 0.0, "slippage_pct": 0.0},
    }


def _run(df: pd.DataFrame, config: dict, signals: pd.Series, trade_start=None) -> Portfolio:
    """Inject a mock strategy, run engine, return portfolio."""
    strategy = MockStrategy(df, signals)
    engine = BacktestEngine(config=config, equity_data=df, trade_start=trade_start, strategy=strategy)
    return engine.run()


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestEngineEntryExit:
    """Entry and exit mechanics: profit target, stop loss, opposite signal, and EOD close."""

    def test_profit_target_long(self):
        """Long signal at bar 3, entry fills at bar 4 open, bar 5 high hits TP → profit_target, pnl=$800."""
        df = _make_bars(10)
        df.iloc[5, df.columns.get_loc("high")] = 481.0  # 481 >= limit_px=480

        signals = pd.Series(0, index=df.index)
        signals.iloc[3] = 1  # long signal at bar 3; fills at bar 4 open (400.0)

        portfolio = _run(df, _config(tp=20.0, sl=20.0), signals)

        assert len(portfolio.closed_trades) == 1
        trade = portfolio.closed_trades[0]
        assert trade["exit_reason"] == "profit_target"
        # entry=400, limit=480, 10 contracts, zero costs → pnl = (480-400)*10 = 800
        assert trade["pnl"] == pytest.approx(800.0)

    def test_stop_loss_long(self):
        """Long signal at bar 3, entry fills at bar 4 open, bar 5 low hits SL → stop_loss, pnl=-$800."""
        df = _make_bars(10)
        df.iloc[5, df.columns.get_loc("low")] = 319.0  # 319 <= stop_px=320

        signals = pd.Series(0, index=df.index)
        signals.iloc[3] = 1

        portfolio = _run(df, _config(tp=20.0, sl=20.0), signals)

        assert len(portfolio.closed_trades) == 1
        trade = portfolio.closed_trades[0]
        assert trade["exit_reason"] == "stop_loss"
        # entry=400, stop=320, 10 contracts → pnl = (320-400)*10 = -800
        assert trade["pnl"] == pytest.approx(-800.0)

    def test_profit_target_short(self):
        """Short signal at bar 3, entry fills at bar 4 open, bar 5 low hits TP → profit_target, pnl=$800."""
        df = _make_bars(10)
        # For short: limit_px = 400*(1-0.20) = 320; low <= 320 triggers TP
        df.iloc[5, df.columns.get_loc("low")] = 319.0

        signals = pd.Series(0, index=df.index)
        signals.iloc[3] = -1  # short signal at bar 3; fills at bar 4 open

        portfolio = _run(df, _config(tp=20.0, sl=20.0), signals)

        assert len(portfolio.closed_trades) == 1
        trade = portfolio.closed_trades[0]
        assert trade["exit_reason"] == "profit_target"
        # direction=-1, exit=320, entry=400 → pnl = -1*(320-400)*10 = 800
        assert trade["pnl"] == pytest.approx(800.0)

    def test_stop_loss_short(self):
        """Short signal at bar 3, entry fills at bar 4 open, bar 5 high hits SL → stop_loss, pnl=-$800."""
        df = _make_bars(10)
        # For short: stop_px = 400*(1+0.20) = 480; high >= 480 triggers SL
        df.iloc[5, df.columns.get_loc("high")] = 481.0

        signals = pd.Series(0, index=df.index)
        signals.iloc[3] = -1

        portfolio = _run(df, _config(tp=20.0, sl=20.0), signals)

        assert len(portfolio.closed_trades) == 1
        trade = portfolio.closed_trades[0]
        assert trade["exit_reason"] == "stop_loss"
        # direction=-1, exit=480, entry=400 → pnl = -1*(480-400)*10 = -800
        assert trade["pnl"] == pytest.approx(-800.0)

    def test_opposite_signal_exit(self):
        """Long at bar 3, short signal at bar 7 (no TP/SL hit) → opposite_signal exit."""
        df = _make_bars(15)  # default high/low stay within 20% bounds

        signals = pd.Series(0, index=df.index)
        signals.iloc[3] = 1   # long
        signals.iloc[7] = -1  # opposite signal

        portfolio = _run(df, _config(tp=20.0, sl=20.0, opp_signal=True, eod_close=False), signals)

        # First trade: long closed by opposite_signal
        assert portfolio.closed_trades[0]["exit_reason"] == "opposite_signal"

    def test_eod_close(self):
        """Long at 15:40, bar at 15:55 → eod_close exit."""
        # 8 bars: 15:30, 15:35, 15:40, 15:45, 15:50, 15:55, 16:00, 16:05
        df = _make_bars(8, start="2025-01-02 15:30")

        signals = pd.Series(0, index=df.index)
        signals.iloc[2] = 1  # 15:40 long

        portfolio = _run(df, _config(tp=20.0, sl=20.0, eod_close=True, opp_signal=False), signals)

        assert len(portfolio.closed_trades) == 1
        assert portfolio.closed_trades[0]["exit_reason"] == "eod_close"

    def test_no_entry_on_eod_bar_when_eod_close_enabled(self):
        """Signal fires exactly at 15:55 with eod_close=True → entry blocked (M-1 fix).

        Without the fix, the engine would open a position at 15:55 that is never
        EOD-closed because the eod_closed_this_bar flag only fires for positions that
        were already open. With the fix, is_eod_bar prevents any new entry at/after 15:55.
        """
        # 5 bars: 15:45, 15:50, 15:55, 16:00, 16:05
        df = _make_bars(5, start="2025-01-02 15:45")

        signals = pd.Series(0, index=df.index)
        signals.iloc[2] = 1  # signal fires at 15:55 — must be blocked

        portfolio = _run(df, _config(tp=20.0, sl=20.0, eod_close=True, opp_signal=False), signals)

        # No position should ever open
        assert len(portfolio.closed_trades) == 0
        assert len(portfolio.positions) == 0

    def test_no_entry_on_eod_bar_eod_close_disabled(self):
        """Signal at 15:55 with eod_close=False → entry is allowed (guard must not fire)."""
        df = _make_bars(5, start="2025-01-02 15:45")

        signals = pd.Series(0, index=df.index)
        signals.iloc[2] = 1  # signal fires at 15:55

        portfolio = _run(df, _config(tp=20.0, sl=20.0, eod_close=False, opp_signal=False), signals)

        # Position opens and is closed at backtest_end
        assert len(portfolio.closed_trades) == 1
        assert portfolio.closed_trades[0]["exit_reason"] == "backtest_end"


class TestEngineGating:
    """Signal gating: warm-up period enforcement, max concurrent positions, and no-signal baseline."""

    def test_warmup_gating(self):
        """Signal during warm-up (bar 2) is ignored; signal after trade_start (bar 6) executes."""
        df = _make_bars(15)

        signals = pd.Series(0, index=df.index)
        signals.iloc[2] = 1  # during warm-up
        signals.iloc[6] = 1  # after trade_start

        trade_start = df.index[5]  # trade_start_idx = 5

        portfolio = _run(df, _config(), signals, trade_start=trade_start)

        # Signal fires at bar 6 (not bar 2); entry fills at bar 7's open (next-bar-open)
        assert len(portfolio.closed_trades) == 1
        entry_ts = pd.Timestamp(portfolio.closed_trades[0]["entry_time"])
        assert entry_ts == df.index[7]

    def test_max_concurrent(self):
        """Two consecutive long signals with max_pos=1 → only 1 position opened."""
        df = _make_bars(15)

        signals = pd.Series(0, index=df.index)
        signals.iloc[3] = 1  # first long
        signals.iloc[4] = 1  # second long (same direction, no exit → blocked by max_pos)

        portfolio = _run(df, _config(max_pos=1, opp_signal=False), signals)

        # Bar 4's signal can't open while bar 3's position is still open
        assert len(portfolio.closed_trades) == 1

    def test_no_entry_no_signal(self):
        """All signals=0 → 0 trades."""
        df = _make_bars(10)
        signals = pd.Series(0, index=df.index)

        portfolio = _run(df, _config(), signals)

        assert len(portfolio.closed_trades) == 0
        assert len(portfolio.positions) == 0


class TestEnginePnLMath:
    """P&L calculation accuracy: correct dollar profit and equity curve update after a closed trade."""

    def test_pnl_math(self):
        """Long signal at bar 3, fills at bar 4 open ($400), bar 5 high hits TP at $420 (5%) → pnl=$200."""
        df = _make_bars(10)
        # tp=5% → limit_px = 400*1.05 = 420; bar 5 high=421 triggers it
        df.iloc[5, df.columns.get_loc("high")] = 421.0

        signals = pd.Series(0, index=df.index)
        signals.iloc[3] = 1

        cfg = _config(tp=5.0, sl=20.0, contracts=10)
        portfolio = _run(df, cfg, signals)

        trade = portfolio.closed_trades[0]
        assert trade["pnl"] == pytest.approx(200.0)  # (420-400)*10

        # All positions closed → equity = cash = initial + pnl
        assert portfolio.get_equity() == pytest.approx(100_000 + 200.0)


# ---------------------------------------------------------------------------
# Options-mode helpers
# ---------------------------------------------------------------------------

def _opts_config(tp=50.0, sl=50.0, eod_close=False, opp_signal=True, max_pos=1, contracts=1):
    """Minimal options config. Zero costs for clean P&L math."""
    return {
        "strategy": {"trade_mode": "options", "initial_capital": 100_000},
        "exits": {
            "profit_target_pct": tp,
            "stop_loss_pct": sl,
            "eod_close": eod_close,
            "opposite_signal": opp_signal,
        },
        "position": {
            "sizing_mode": "fixed",
            "contracts_per_trade": contracts,
            "max_concurrent_positions": max_pos,
        },
        "costs": {"commission_per_contract": 0.0, "slippage_pct": 0.0},
        "options": {"target_dte": 14, "strike_selection": "ATM", "sigma": 0.25},
    }


def _make_option_pos(signal=1, entry_price=5.0, expiry_days=14):
    """Synthetic options Position — no real strike selection or pricing."""
    entry_ts = pd.Timestamp("2025-01-02 09:35:00", tz="America/New_York")
    expiry = (entry_ts + pd.Timedelta(days=expiry_days)).to_pydatetime()
    return Position(
        direction=signal,
        entry_price=entry_price,
        entry_time=entry_ts.to_pydatetime(),
        contracts=1,
        trade_mode="options",
        option_type="C" if signal == 1 else "P",
        strike=400.0,
        expiry=expiry,
        raw_symbol="SYMBOL   250116C00400000",
        current_price=entry_price,
    )


def _run_options(df, config, signals, fixed_option_price=None, trade_start=None):
    """Run engine in options mode with mocked build_option_position.

    Returns (engine, portfolio). Optionally patches _get_option_price to return
    a fixed float so exit-condition tests are fully deterministic.
    """
    def make_pos(sig, *a, **kw):
        return _make_option_pos(signal=sig)

    strategy = MockStrategy(df, signals)
    with contextlib.ExitStack() as stack:
        stack.enter_context(
            patch("src.backtest.trade_logic.build_option_position", side_effect=make_pos)
        )
        if fixed_option_price is not None:
            stack.enter_context(
                patch.object(BacktestEngine, "_get_option_price", return_value=fixed_option_price)
            )
        engine = BacktestEngine(config=config, equity_data=df, trade_start=trade_start, strategy=strategy)
        portfolio = engine.run()
    return engine, portfolio


# ---------------------------------------------------------------------------
# Options-mode tests
# ---------------------------------------------------------------------------

class TestEngineOptionsMode:
    """Options entry/exit mechanics exercised through the full engine.run() loop."""

    def test_options_entry_buy_signal(self):
        """Buy signal → call position opened, closed at backtest_end."""
        df = _make_bars(10)
        signals = pd.Series(0, index=df.index)
        signals.iloc[3] = 1

        _, portfolio = _run_options(df, _opts_config(), signals, fixed_option_price=5.0)

        assert len(portfolio.closed_trades) == 1
        trade = portfolio.closed_trades[0]
        assert trade["trade_mode"] == "options"
        assert trade["option_type"] == "C"
        assert trade["direction"] == "long"

    def test_options_entry_sell_signal(self):
        """Sell signal → put position opened with direction='short'."""
        df = _make_bars(10)
        signals = pd.Series(0, index=df.index)
        signals.iloc[3] = -1

        _, portfolio = _run_options(df, _opts_config(), signals, fixed_option_price=5.0)

        assert len(portfolio.closed_trades) == 1
        trade = portfolio.closed_trades[0]
        assert trade["trade_mode"] == "options"
        assert trade["option_type"] == "P"
        assert trade["direction"] == "short"

    def test_options_profit_target_exit(self):
        """current_price >= entry*(1+tp/100) → profit_target exit."""
        df = _make_bars(10)
        signals = pd.Series(0, index=df.index)
        signals.iloc[3] = 1

        # entry_price=5.0, tp=50% → needs current_price >= 7.5
        _, portfolio = _run_options(
            df, _opts_config(tp=50.0, sl=99.0), signals, fixed_option_price=7.5
        )

        assert len(portfolio.closed_trades) == 1
        assert portfolio.closed_trades[0]["exit_reason"] == "profit_target"

    def test_options_stop_loss_exit(self):
        """current_price <= entry*(1-sl/100) → stop_loss exit."""
        df = _make_bars(10)
        signals = pd.Series(0, index=df.index)
        signals.iloc[3] = 1

        # entry_price=5.0, sl=50% → needs current_price <= 2.5
        _, portfolio = _run_options(
            df, _opts_config(tp=200.0, sl=50.0), signals, fixed_option_price=2.4
        )

        assert len(portfolio.closed_trades) == 1
        assert portfolio.closed_trades[0]["exit_reason"] == "stop_loss"

    def test_options_eod_close_exit(self):
        """15:55 bar triggers eod_close on an open options position."""
        df = _make_bars(8, start="2025-01-02 15:30")
        signals = pd.Series(0, index=df.index)
        signals.iloc[2] = 1  # entry at 15:40

        _, portfolio = _run_options(
            df,
            _opts_config(tp=200.0, sl=99.0, eod_close=True, opp_signal=False),
            signals,
            fixed_option_price=5.0,
        )

        assert len(portfolio.closed_trades) == 1
        assert portfolio.closed_trades[0]["exit_reason"] == "eod_close"

    def test_options_opposite_signal_exit(self):
        """Long options position closed when opposite (-1) signal fires."""
        df = _make_bars(15)
        signals = pd.Series(0, index=df.index)
        signals.iloc[3] = 1   # long entry
        signals.iloc[8] = -1  # opposite signal

        _, portfolio = _run_options(
            df, _opts_config(tp=200.0, sl=99.0, opp_signal=True),
            signals, fixed_option_price=5.0,
        )

        assert portfolio.closed_trades[0]["exit_reason"] == "opposite_signal"

    def test_options_pnl_multiplier(self):
        """Options P&L uses 100× multiplier: entry=5.0, exit=7.5, 1 contract → pnl=$250."""
        df = _make_bars(10)
        signals = pd.Series(0, index=df.index)
        signals.iloc[3] = 1

        _, portfolio = _run_options(
            df, _opts_config(tp=50.0, sl=99.0), signals, fixed_option_price=7.5
        )

        trade = portfolio.closed_trades[0]
        assert trade["exit_reason"] == "profit_target"
        # pnl = direction * (exit - entry) * contracts * 100 = 1*(7.5-5.0)*1*100 = 250
        assert trade["pnl"] == pytest.approx(250.0)

    def test_options_run_raises_when_no_option_market_data(self, monkeypatch, tmp_path):
        """Options backtests must fail if option market data is unavailable (API key missing)."""
        df = _make_bars(10)
        signals = pd.Series(0, index=df.index)
        signals.iloc[3] = 1

        monkeypatch.delenv("DATA_BENTO_PW", raising=False)
        monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
        config = _opts_config(tp=200.0, sl=99.0)
        config["data"] = {"options_dir": str(tmp_path)}

        with pytest.raises(ValueError, match="Missing Databento API key"):
            _run_options(df, config, signals)
    def test_options_expiration_exit(self):
        """Options position whose expiry date < bar date triggers 'expiration' in the engine loop.

        This exercises the expiration branch inside the engine's hot loop via check_option_exit,
        not just the pure function in isolation. Expiry is set to the day before the bars so
        that `ts.date() > pos.expiry.date()` fires at bar 3 (first exit check after entry).
        Same-day expiry no longer triggers (eod_close handles 0-DTE; expiration is a safety net
        for positions that survive past their expiry date).
        """
        df = _make_bars(10)
        signals = pd.Series(0, index=df.index)
        signals.iloc[2] = 1  # entry at bar 2

        # Bar dates are all 2025-01-02; expiry set to 2025-01-01 so the check
        # `ts.date() > pos.expiry.date()` fires at bar 3 (first exit check after entry).
        def make_expiring_pos(sig, *a, **kw):
            entry_ts = pd.Timestamp("2025-01-02 09:35:00", tz="America/New_York")
            # Must be tz-aware to match bar timestamps (tz="America/New_York")
            expiry = pd.Timestamp("2025-01-01", tz="America/New_York").to_pydatetime()
            return Position(
                direction=sig,
                entry_price=5.0,
                entry_time=entry_ts.to_pydatetime(),
                contracts=1,
                trade_mode="options",
                option_type="C" if sig == 1 else "P",
                strike=400.0,
                expiry=expiry,
                raw_symbol="SYMBOL   250102C00400000",
                current_price=5.0,
            )

        strategy = MockStrategy(df, signals)
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch("src.backtest.trade_logic.build_option_position", side_effect=make_expiring_pos)
            )
            # Fix option price so stop_loss/profit_target don't fire before expiration
            stack.enter_context(
                patch.object(BacktestEngine, "_get_option_price", return_value=5.0)
            )
            engine = BacktestEngine(
                config=_opts_config(tp=200.0, sl=99.0, eod_close=False, opp_signal=False),
                equity_data=df,
                strategy=strategy,
            )
            portfolio = engine.run()

        assert len(portfolio.closed_trades) == 1
        assert portfolio.closed_trades[0]["exit_reason"] == "expiration"


# ---------------------------------------------------------------------------
# Additional helpers
# ---------------------------------------------------------------------------

def _config_poe(sizing_pct=50, capital=100_000, tp=20.0, sl=20.0, eod_close=False,
                opp_signal=True, max_pos=1):
    """Config with percent_of_equity sizing. Zero costs."""
    return {
        "strategy": {"trade_mode": "equities", "initial_capital": capital},
        "exits": {
            "profit_target_pct": tp,
            "stop_loss_pct": sl,
            "eod_close": eod_close,
            "opposite_signal": opp_signal,
        },
        "position": {
            "sizing_mode": "equity_pct",
            "sizing_pct": sizing_pct,
            "max_concurrent_positions": max_pos,
        },
        "costs": {"commission_per_contract": 0.0, "slippage_pct": 0.0},
    }




def _run_with_engine(df, config, signals, trade_start=None, oos_start=None):
    """Inject mock strategy and run engine, returning (engine, portfolio) for inspection."""
    strategy = MockStrategy(df, signals)
    engine = BacktestEngine(
        config=config, equity_data=df,
        trade_start=trade_start, oos_start=oos_start,
        strategy=strategy,
    )
    portfolio = engine.run()
    return engine, portfolio




# ---------------------------------------------------------------------------
# CRITICAL — Untested code paths
# ---------------------------------------------------------------------------

class TestEngineSizing:
    """percent_of_equity sizing mode."""

    def test_percent_of_equity_sizing_computes_shares(self):
        """equity=100k, sizing_pct=50, price=400 → int(50000/400)=125 contracts, pnl=(480-400)*125=10000."""
        df = _make_bars(10)
        df.iloc[5, df.columns.get_loc("high")] = 481.0  # triggers TP at 480 (bar 5, after next-bar-open entry at bar 4)

        signals = pd.Series(0, index=df.index)
        signals.iloc[3] = 1

        portfolio = _run(df, _config_poe(sizing_pct=50, capital=100_000, tp=20.0, sl=20.0), signals)

        assert len(portfolio.closed_trades) == 1
        trade = portfolio.closed_trades[0]
        assert trade["contracts"] == 125
        assert trade["exit_reason"] == "profit_target"
        assert trade["pnl"] == pytest.approx(10_000.0)

    def test_equity_pct_sizing_fractional_shares(self):
        """equity=100, sizing_pct=1, price=400 → 1.0/400=0.0025 contracts.

        Verifies fractional shares are supported in equities mode.
        """
        df = _make_bars(10)
        df.iloc[5, df.columns.get_loc("high")] = 481.0

        signals = pd.Series(0, index=df.index)
        signals.iloc[3] = 1

        portfolio = _run(df, _config_poe(sizing_pct=1, capital=100, tp=20.0, sl=20.0), signals)

        assert len(portfolio.closed_trades) == 1
        trade = portfolio.closed_trades[0]
        assert trade["contracts"] == 0.0025


class TestOptionPriceStaleness:
    """_get_option_price() staleness guard: return None + log WARNING when gap > threshold."""

    def _make_options_engine(self, config=None):
        """Build a minimal options-mode engine with a MockStrategy."""
        df = _make_bars(10)
        signals = pd.Series(0, index=df.index)
        strategy = MockStrategy(df, signals)
        cfg = config or _opts_config()
        engine = BacktestEngine(config=cfg, equity_data=df, strategy=strategy)
        return engine, df

    def test_stale_price_returns_none_and_warns(self, caplog):
        """Bar at 10:00, next data bar at 10:45 (45-min gap > 25-min threshold) → None + WARNING."""
        import logging
        engine, _ = self._make_options_engine()

        # Build mock option data: bar at 10:00, next bar at 10:45 — 45-min gap
        bar_10_00 = pd.Timestamp("2025-01-02 10:00:00", tz="America/New_York")
        bar_10_45 = pd.Timestamp("2025-01-02 10:45:00", tz="America/New_York")
        idx = pd.DatetimeIndex([bar_10_00, bar_10_45], tz="America/New_York")
        mock_df = pd.DataFrame(
            {"open": [5.0, 5.2], "high": [5.1, 5.3], "low": [4.9, 5.1], "close": [5.0, 5.2]},
            index=idx,
        )

        requested_ts = pd.Timestamp("2025-01-02 10:30:00", tz="America/New_York")

        from unittest.mock import MagicMock
        mock_loader = MagicMock()
        mock_loader.load_option_bars.return_value = mock_df
        engine._options_loader = mock_loader
        with caplog.at_level(logging.WARNING, logger="src.backtest.engine"):
            result = engine._get_option_price(
                underlying_price=400.0,
                strike=400.0,
                dte_years=0.1,
                option_type="C",
                raw_symbol="SYMBOL   250116C00400000",
                bar_time=requested_ts,
            )

        assert result is None, f"Expected None for stale price, got {result}"
        assert any("stale" in r.message.lower() for r in caplog.records), (
            "Expected a WARNING mentioning 'stale' in log records"
        )

    def test_fresh_price_returns_value(self, caplog):
        """Bar at 10:00, request at 10:01 (1-min gap < 25-min threshold) → price returned."""
        import logging
        engine, _ = self._make_options_engine()

        bar_10_00 = pd.Timestamp("2025-01-02 10:00:00", tz="America/New_York")
        bar_10_06 = pd.Timestamp("2025-01-02 10:06:00", tz="America/New_York")
        idx = pd.DatetimeIndex([bar_10_00, bar_10_06], tz="America/New_York")
        mock_df = pd.DataFrame(
            {"open": [5.0, 5.2], "high": [5.1, 5.3], "low": [4.9, 5.1], "close": [5.0, 5.2]},
            index=idx,
        )

        # Request at 10:03 — only 3 min stale from 10:00 bar
        requested_ts = pd.Timestamp("2025-01-02 10:03:00", tz="America/New_York")

        from unittest.mock import MagicMock
        mock_loader = MagicMock()
        mock_loader.load_option_bars.return_value = mock_df
        engine._options_loader = mock_loader
        result = engine._get_option_price(
            underlying_price=400.0,
            strike=400.0,
            dte_years=0.1,
            option_type="C",
            raw_symbol="SYMBOL   250116C00400000",
            bar_time=requested_ts,
        )

        assert result == pytest.approx(5.0), f"Expected 5.0 for fresh price, got {result}"


class TestStalenessThresholdConfigurable:
    """max_option_staleness_minutes config key controls the staleness threshold."""

    def _build_mock_data(self, gap_minutes=10):
        """Build option data with a fixed gap from the last bar."""
        bar_time = pd.Timestamp("2025-01-02 10:00:00", tz="America/New_York")
        next_bar = bar_time + pd.Timedelta(minutes=gap_minutes)
        idx = pd.DatetimeIndex([bar_time, next_bar], tz="America/New_York")
        return pd.DataFrame(
            {"open": [5.0, 5.2], "high": [5.1, 5.3], "low": [4.9, 5.1], "close": [5.0, 5.2]},
            index=idx,
        )

    def test_tight_threshold_triggers_staleness(self, caplog):
        """10-min gap with 5-min threshold → stale (returns None + WARNING).

        Data: bar at 10:00, next bar at 10:10 (10-min gap). Request at 10:05.
        Staleness = 10:05 - 10:00 = 5 min. Threshold = 5 min.
        But the staleness check uses > (strict), so 5 > 5 is False.
        Request at 10:06 instead: staleness = 6 min > 5 min → stale.
        """
        import logging
        cfg = _opts_config()
        cfg["data"] = {"max_option_staleness_minutes": 5}
        engine, _ = TestOptionPriceStaleness()._make_options_engine(config=cfg)

        from unittest.mock import MagicMock
        mock_loader = MagicMock()
        mock_loader.load_option_bars.return_value = self._build_mock_data(gap_minutes=10)
        engine._options_loader = mock_loader

        # Request at 10:06 — 6 min stale from 10:00 bar, > 5-min threshold
        requested_ts = pd.Timestamp("2025-01-02 10:06:00", tz="America/New_York")
        with caplog.at_level(logging.WARNING, logger="src.backtest.engine"):
            result = engine._get_option_price(
                underlying_price=400.0, strike=400.0, dte_years=0.1,
                option_type="C", raw_symbol="SYMBOL   250116C00400000",
                bar_time=requested_ts,
            )

        assert result is None
        assert any("stale" in r.message.lower() for r in caplog.records)

    def test_wide_threshold_allows_same_data(self):
        """10-min gap with 60-min threshold → fresh (returns price)."""
        cfg = _opts_config()
        cfg["data"] = {"max_option_staleness_minutes": 60}
        engine, _ = TestOptionPriceStaleness()._make_options_engine(config=cfg)

        from unittest.mock import MagicMock
        mock_loader = MagicMock()
        mock_loader.load_option_bars.return_value = self._build_mock_data(gap_minutes=10)
        engine._options_loader = mock_loader

        # Request at 10:06 — 6 min stale, but threshold is 60 min → fresh
        requested_ts = pd.Timestamp("2025-01-02 10:06:00", tz="America/New_York")
        result = engine._get_option_price(
            underlying_price=400.0, strike=400.0, dte_years=0.1,
            option_type="C", raw_symbol="SYMBOL   250116C00400000",
            bar_time=requested_ts,
        )

        assert result == pytest.approx(5.0)


class TestEngineBothModeRemoved:
    """trade_mode='both' is no longer supported — raises ValueError."""

    def test_both_mode_raises(self):
        df = _make_bars(5)
        config = {
            "strategy": {"trade_mode": "both", "initial_capital": 100_000},
            "exits": {"profit_target_pct": 20.0, "stop_loss_pct": 20.0,
                      "eod_close": False, "opposite_signal": True},
            "position": {"sizing_mode": "fixed", "contracts_per_trade": 1,
                         "max_concurrent_positions": 1},
            "costs": {"commission_per_contract": 0.0, "slippage_pct": 0.0},
        }
        signals = pd.Series(0, index=df.index)
        strategy = MockStrategy(df, signals)
        with pytest.raises(ValueError, match="trade_mode must be"):
            BacktestEngine(config=config, equity_data=df, strategy=strategy)


class TestEngineOOSSplit:
    """oos_start_idx resolution in the engine."""

    def test_oos_start_idx_resolves_correctly(self):
        """oos_start at bar 8 → engine.oos_start_idx == 8; trades execute in both IS and OOS."""
        df = _make_bars(15)
        signals = pd.Series(0, index=df.index)
        signals.iloc[3] = 1   # IS trade
        signals.iloc[10] = 1  # OOS trade

        engine, portfolio = _run_with_engine(
            df, _config(opp_signal=False, max_pos=2), signals, oos_start=df.index[8],
        )

        assert engine.oos_start_idx == 8
        # OOS is a marker, not a gate — both trades execute
        assert len(portfolio.closed_trades) == 2

    def test_oos_start_defaults_to_trade_start_when_none(self):
        """oos_start=None → oos_start_idx == trade_start_idx."""
        df = _make_bars(15)
        signals = pd.Series(0, index=df.index)
        signals.iloc[6] = 1

        engine, _ = _run_with_engine(
            df, _config(), signals, trade_start=df.index[5], oos_start=None,
        )

        assert engine.oos_start_idx == 5

    def test_oos_start_without_tz_on_tz_aware_data(self):
        """tz-naive oos_start with tz-aware data → no exception (exercises tz-localize branch)."""
        df = _make_bars(15)  # tz-aware index (America/New_York)
        signals = pd.Series(0, index=df.index)
        signals.iloc[6] = 1

        # Pass oos_start as tz-naive string — engine should localize it
        oos_ts = df.index[8].strftime("%Y-%m-%d %H:%M:%S")  # strips tz
        engine, _ = _run_with_engine(df, _config(), signals, oos_start=oos_ts)

        assert engine.oos_start_idx == 8


# ---------------------------------------------------------------------------
# HIGH — Exit priority, signal transitions
# ---------------------------------------------------------------------------

class TestExitPrioritySameBar:
    """When both stop and limit trigger on the same bar, stop wins (checked first)."""

    def test_stop_beats_limit_on_wide_range_bar_long(self):
        """Long signal at bar 3, fills at bar 4 open; bar 5 low=319 (SL) AND high=481 (TP) → stop_loss wins."""
        df = _make_bars(10)
        df.iloc[5, df.columns.get_loc("low")] = 319.0
        df.iloc[5, df.columns.get_loc("high")] = 481.0

        signals = pd.Series(0, index=df.index)
        signals.iloc[3] = 1

        portfolio = _run(df, _config(tp=20.0, sl=20.0), signals)

        trade = portfolio.closed_trades[0]
        assert trade["exit_reason"] == "stop_loss"
        assert trade["exit_price"] == pytest.approx(320.0)
        assert trade["pnl"] == pytest.approx(-800.0)

    def test_stop_beats_limit_on_wide_range_bar_short(self):
        """Short signal at bar 3, fills at bar 4 open; bar 5 high=481 (SL) AND low=319 (TP) → stop_loss wins."""
        df = _make_bars(10)
        df.iloc[5, df.columns.get_loc("high")] = 481.0
        df.iloc[5, df.columns.get_loc("low")] = 319.0

        signals = pd.Series(0, index=df.index)
        signals.iloc[3] = -1

        portfolio = _run(df, _config(tp=20.0, sl=20.0), signals)

        trade = portfolio.closed_trades[0]
        assert trade["exit_reason"] == "stop_loss"
        assert trade["exit_price"] == pytest.approx(480.0)
        assert trade["pnl"] == pytest.approx(-800.0)


class TestSignalTransitions:
    """Opposite signal closes existing position and opens a new one on the same bar."""

    def test_opposite_signal_closes_long_and_opens_short(self):
        """Long at bar 3, short signal at bar 7 → close long + open short."""
        df = _make_bars(15)
        signals = pd.Series(0, index=df.index)
        signals.iloc[3] = 1
        signals.iloc[7] = -1

        portfolio = _run(df, _config(tp=20.0, sl=20.0, opp_signal=True), signals)

        assert len(portfolio.closed_trades) == 2
        assert portfolio.closed_trades[0]["direction"] == "long"
        assert portfolio.closed_trades[0]["exit_reason"] == "opposite_signal"
        assert portfolio.closed_trades[1]["direction"] == "short"
        assert portfolio.closed_trades[1]["exit_reason"] == "backtest_end"

    def test_opposite_signal_closes_short_and_opens_long(self):
        """Short at bar 3, long signal at bar 7 → close short + open long."""
        df = _make_bars(15)
        signals = pd.Series(0, index=df.index)
        signals.iloc[3] = -1
        signals.iloc[7] = 1

        portfolio = _run(df, _config(tp=20.0, sl=20.0, opp_signal=True), signals)

        assert len(portfolio.closed_trades) == 2
        assert portfolio.closed_trades[0]["direction"] == "short"
        assert portfolio.closed_trades[0]["exit_reason"] == "opposite_signal"
        assert portfolio.closed_trades[1]["direction"] == "long"
        assert portfolio.closed_trades[1]["exit_reason"] == "backtest_end"


# ---------------------------------------------------------------------------
# MEDIUM — Edge cases
# ---------------------------------------------------------------------------

class TestEquityCurveMidTrade:
    """Mark-to-market equity reflects unrealized P&L while a position is open."""

    def test_equity_mid_trade_reflects_unrealized_pnl(self):
        """Long 10 contracts at 400 (fills bar 4 open), bar 5 close=405 → equity=100050."""
        df = _make_bars(10)
        df.iloc[5, df.columns.get_loc("close")] = 405.0

        signals = pd.Series(0, index=df.index)
        signals.iloc[3] = 1

        _engine, portfolio = _run_with_engine(
            df, _config(tp=20.0, sl=20.0, opp_signal=False), signals,
        )

        # equity_curve[0] = initial baseline (record_initial_equity)
        # equity_curve[1..10] = bars 0..9; bar 5 is at index 6
        # Signal at bar[3] → fills at bar[4].open=400; bar[5].close=405 → unrealized
        # cash=96000 (100k - 400*10), positions_value=405*10=4050, equity=100050
        eq_at_bar5 = portfolio.equity_curve[6]["equity"]
        assert eq_at_bar5 == pytest.approx(100_050.0)


class TestOppSignalDisabled:
    """opposite_signal=False keeps position open despite opposite signal."""

    def test_opposite_signal_disabled_does_not_close(self):
        """Long at bar 3, short signal at bar 7 with opp_signal=False → held until backtest_end."""
        df = _make_bars(15)
        signals = pd.Series(0, index=df.index)
        signals.iloc[3] = 1
        signals.iloc[7] = -1

        portfolio = _run(df, _config(tp=20.0, sl=20.0, opp_signal=False), signals)

        assert len(portfolio.closed_trades) == 1
        assert portfolio.closed_trades[0]["exit_reason"] == "backtest_end"


class TestPendingEntryExpiration:
    """Pending entry expires after age > 1 (max 2 fill attempts)."""

    def test_pending_entry_does_not_persist_indefinitely(self):
        """When max_pos=1 and one position is held, a second same-direction signal ages out.

        Signal at bar 3 → pending entry. Portfolio full (position opened at bar 3
        fills at bar 4). With no opposite signal, position stays open. Pending entry
        at bar 5 has age 1 (OK), at bar 6 age 2 (expired).
        """
        df = _make_bars(10)

        signals = pd.Series(0, index=df.index)
        signals.iloc[3] = 1  # first signal → opens position at bar 4
        signals.iloc[5] = 1  # second signal → pending, but portfolio full

        portfolio = _run(df, _config(max_pos=1, opp_signal=False), signals)

        # Only 1 trade from the first signal; second signal's pending entry expires
        assert len(portfolio.closed_trades) == 1
        assert portfolio.closed_trades[0]["direction"] == "long"

    def test_opposite_signal_closes_and_opens_new(self):
        """Opposite signal closes existing position and new pending entry fills next bar."""
        df = _make_bars(15)

        signals = pd.Series(0, index=df.index)
        signals.iloc[3] = 1   # long signal
        signals.iloc[7] = -1  # opposite signal closes long, buffers short entry

        portfolio = _run(df, _config(tp=20.0, sl=20.0, opp_signal=True), signals)

        # Two trades: long closed by opposite_signal, short closed at backtest_end
        assert len(portfolio.closed_trades) == 2
        assert portfolio.closed_trades[0]["direction"] == "long"
        assert portfolio.closed_trades[0]["exit_reason"] == "opposite_signal"
        assert portfolio.closed_trades[1]["direction"] == "short"
        assert portfolio.closed_trades[1]["exit_reason"] == "backtest_end"


class TestEngineTransactionCosts:
    """Transaction costs reduce P&L."""

    def test_transaction_costs_reduce_pnl(self):
        """Commission $1/contract + 0.1% slippage → net PnL = 200 - 28.2 = 171.8."""
        df = _make_bars(10)
        df.iloc[5, df.columns.get_loc("high")] = 421.0  # triggers 5% TP at 420 (bar 5, after next-bar-open entry at bar 4)

        signals = pd.Series(0, index=df.index)
        signals.iloc[3] = 1

        cfg = _config(tp=5.0, sl=20.0, contracts=10)
        cfg["costs"]["commission_per_contract"] = 1.0
        cfg["costs"]["slippage_pct"] = 0.1

        portfolio = _run(df, cfg, signals)

        trade = portfolio.closed_trades[0]
        # entry cost: commission=10, slippage=400*0.001*10=4 → 14
        # exit cost:  commission=10, slippage=420*0.001*10=4.2 → 14.2
        # gross pnl: (420-400)*10=200, net: 200-14-14.2=171.8
        assert trade["pnl"] == pytest.approx(171.8)


# ---------------------------------------------------------------------------
# Signal system dispatch
# ---------------------------------------------------------------------------

class TestEngineSignalSystemDispatch:
    """Verify that signal_system config routes to the correct strategy."""

    def test_default_uses_indicator_pair(self):
        """Without signal_system in config, IndicatorPairStrategy is used."""
        from src.signals.strategy import IndicatorPairStrategy
        df = _make_bars(10)
        signals = pd.Series(0, index=df.index)
        cfg = _config()

        # Let create_strategy pick the default, but patch the concrete methods
        with (
            patch.object(IndicatorPairStrategy, "compute_indicators", return_value=df) as mock_ci,
            patch.object(IndicatorPairStrategy, "generate_signals", return_value=signals) as mock_gs,
        ):
            engine = BacktestEngine(config=cfg, equity_data=df)
            assert mock_ci.call_count == 1
            assert mock_gs.call_count == 1
        assert isinstance(engine._strategy, IndicatorPairStrategy)

    def test_ema_233_dispatches_to_ema_strategy(self):
        """With signal_system='ema_233', Ema233Strategy is used."""
        from src.signals.strategy import Ema233Strategy
        df = _make_bars(10)
        signals = pd.Series(0, index=df.index)
        cfg = _config()
        cfg["strategy"]["signal_system"] = "ema_233"
        cfg["signals_ema"] = {"ema_period": 233, "entry_offset_cents": 0.02}

        with (
            patch.object(Ema233Strategy, "compute_indicators", return_value=df) as mock_ci,
            patch.object(Ema233Strategy, "generate_signals", return_value=signals) as mock_gs,
        ):
            engine = BacktestEngine(config=cfg, equity_data=df)
            assert mock_ci.call_count == 1
            assert mock_gs.call_count == 1
        assert isinstance(engine._strategy, Ema233Strategy)

    def test_injected_strategy_used(self):
        """An explicitly injected strategy is used instead of config-based dispatch."""
        df = _make_bars(10)
        signals = pd.Series(0, index=df.index)
        cfg = _config()

        strategy = MockStrategy(df, signals)
        engine = BacktestEngine(config=cfg, equity_data=df, strategy=strategy)
        assert engine._strategy is strategy

    def test_entry_price_hint_used_as_fill(self):
        """When entry_price_hint is present, engine uses it instead of bar.open."""
        df = _make_bars(10)
        df["entry_price_hint"] = float("nan")
        df.iloc[3, df.columns.get_loc("entry_price_hint")] = 400.1

        signals = pd.Series(0, index=df.index)
        signals.iloc[3] = 1

        cfg = _config(tp=20.0, sl=20.0, contracts=10)
        strategy = MockStrategy(df, signals)
        engine = BacktestEngine(config=cfg, equity_data=df, strategy=strategy)
        portfolio = engine.run()

        assert len(portfolio.closed_trades) >= 1
        trade = portfolio.closed_trades[0]
        assert trade["entry_price"] == pytest.approx(400.1)

    def test_entry_price_hint_outside_range_falls_back_to_open(self):
        """When entry_price_hint is outside the bar's High/Low range, entry falls back to bar.open."""
        df = _make_bars(10)
        df["entry_price_hint"] = float("nan")
        # Bar 4 high is 400.4, so 405.0 is outside the range
        df.iloc[3, df.columns.get_loc("entry_price_hint")] = 405.0

        signals = pd.Series(0, index=df.index)
        signals.iloc[3] = 1

        cfg = _config(tp=20.0, sl=20.0, contracts=10)
        strategy = MockStrategy(df, signals)
        engine = BacktestEngine(config=cfg, equity_data=df, strategy=strategy)
        portfolio = engine.run()

        # BUG-004: entry is no longer skipped; hint falls back to open price
        assert len(portfolio.closed_trades) == 1
        trade = portfolio.closed_trades[0]
        assert trade["entry_price"] == pytest.approx(400.0)  # fallback to open

    def test_no_hint_uses_open(self):
        """Without entry_price_hint column, engine fills at bar.open as before."""
        df = _make_bars(10)
        signals = pd.Series(0, index=df.index)
        signals.iloc[3] = 1

        cfg = _config(tp=20.0, sl=20.0, contracts=10)
        portfolio = _run(df, cfg, signals)

        assert len(portfolio.closed_trades) >= 1
        trade = portfolio.closed_trades[0]
        assert trade["entry_price"] == pytest.approx(400.0)

    def test_unknown_signal_system_raises(self):
        """Unknown signal_system in config raises ValueError."""
        from src.signals.strategy import create_strategy
        cfg = {"strategy": {"signal_system": "bogus"}}
        with pytest.raises(ValueError, match="Unknown signal_system"):
            create_strategy(cfg)


# ---------------------------------------------------------------------------
# Bug 1: Final-bar signal silently dropped — must log a warning
# ---------------------------------------------------------------------------

class TestFinalBarSignalWarning:
    """Signal fires on the very last bar → no trade opened, warning logged."""

    def test_no_trade_opened_when_signal_on_last_bar(self):
        """A signal on the final bar is buffered but never filled — trade log stays empty."""
        df = _make_bars(5)
        signals = pd.Series(0, index=df.index)
        signals.iloc[-1] = 1  # signal fires on the last bar

        portfolio = _run(df, _config(eod_close=False, opp_signal=False), signals)

        # The signal can never fill (no next bar) — no trades should be opened
        assert len(portfolio.closed_trades) == 0

    def test_warning_logged_when_signal_on_last_bar(self, caplog):
        """Engine logs a WARNING when a signal fires on the final bar and gets dropped."""
        import logging
        df = _make_bars(5)
        signals = pd.Series(0, index=df.index)
        signals.iloc[-1] = 1  # signal fires on the last bar

        with caplog.at_level(logging.WARNING, logger="src.backtest.engine"):
            _run(df, _config(eod_close=False, opp_signal=False), signals)

        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any(
            "Signal fired on final bar" in msg for msg in warning_messages
        ), f"Expected final-bar warning not found. Warnings: {warning_messages}"

    def test_no_warning_when_signal_not_on_last_bar(self, caplog):
        """No spurious final-bar warning when the signal fires earlier (not the last bar)."""
        import logging
        df = _make_bars(5)
        signals = pd.Series(0, index=df.index)
        signals.iloc[2] = 1  # signal fires mid-run, fills on bar 3

        with caplog.at_level(logging.WARNING, logger="src.backtest.engine"):
            _run(df, _config(eod_close=False, opp_signal=False), signals)

        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert not any(
            "Signal fired on final bar" in msg for msg in warning_messages
        ), f"Unexpected final-bar warning found. Warnings: {warning_messages}"
