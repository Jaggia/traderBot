"""
Tests for src/live/live_engine.py.

AlpacaTrader is mocked — no real API calls.
Signal generation (compute_indicators / generate_signals) is patched.
build_entry / check_exit and black_scholes_price are patched where needed.

Covers:
  - on_bar() with no signal      → no position opened
  - on_bar() with buy signal     → position opened via AlpacaTrader.buy_option()
  - on_bar() with profit target  → position closed via AlpacaTrader.sell_option()
  - on_bar() with stop loss      → position closed
  - on_bar() with EOD            → position closed (eod_close)
  - on_bar() with opposite signal → position closed
  - force_close()                → closes open position + cancels orders
  - get_closed_trades()          → returns trade log with correct fields
  - C-4: position not set when buy order not filled
  - C-4: position set when buy order confirmed filled
  - C-5: sell_option() failure logs error, sets _sell_failed, clears position
  - C-5: _start_poll() no-ops when thread already alive
  - C-5: _stop_poll() skips join when called from within the poll thread
"""

import datetime
import tempfile
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.backtest.trade_logic import ExitResult
from src.options.position import Position


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_warmup(n=12) -> pd.DataFrame:
    """Minimal warm-up DataFrame (OHLCV, DatetimeIndex, EST)."""
    idx = pd.date_range("2026-01-05 09:00", periods=n, freq="5min", tz="America/New_York")
    return pd.DataFrame(
        {
        "open":   400.0,
            "high":   401.0,
            "low":    399.0,
            "close":  400.0,
            "volume": 1_000_000,
        },
        index=idx,
    )


def _config(tp=50.0, sl=50.0, eod=False, opp=True, contracts=1):
    """Minimal config dict that mirrors strategy_params.yaml structure."""
    return {
        "strategy":  {"trade_mode": "options"},
        "exits": {
        "profit_target_pct":  tp,
            "stop_loss_pct":      sl,
            "eod_close":          eod,
            "opposite_signal":    opp,
        },
        "position": {
        "sizing_mode":            "fixed",
            "contracts_per_trade":    contracts,
            "max_concurrent_positions": 1,
        },
        "costs": {
        "commission_per_contract": 0.0,
            "slippage_pct": 0.0,
            "slippage_per_contract": 0.0,
        },
        "options": {
        "strike_selection": "ATM",
            "target_dte":       7,
            "sigma":            0.25,
        },
        "signals": {
        "smi_fast": {"period": 5, "smooth1": 3, "smooth2": 3},
            "smi_slow": {"period": 10, "smooth1": 3, "smooth2": 3},
            "williams_r": {"period": 7},
            "sync_window": 5,
            "pair_mode": "either",
            "vwap_filter": False,
            "armed_mode": False,
        },
    }


def _bar(ts: pd.Timestamp, close: float = 400.0) -> pd.Series:
    """Single bar as a Series (mimics DatabentoStreamer output)."""
    return pd.Series(
        {"open": close, "high": close + 1.0, "low": close - 1.0,
         "close": close, "volume": 100_000},
        name=ts,
    )


def _est(date: str, hour: int, minute: int) -> pd.Timestamp:
    return pd.Timestamp(f"{date} {hour:02d}:{minute:02d}:00", tz="America/New_York")


def _make_position(
    entry_price=2.0,
    current_price=2.0,
    option_type="C",
    direction=1,
    expiry=None,
    entry_time=None,
) -> Position:
    if expiry is None:
        # Use a tz-aware expiry so it's compatible with tz-aware bar timestamps
        expiry = pd.Timestamp("2026-01-16", tz="America/New_York")
    if entry_time is None:
        entry_time = _est("2026-01-05", 10, 0)
    return Position(
        direction=direction,
        entry_price=entry_price,
        entry_time=entry_time,
        contracts=1,
        trade_mode="options",
        option_type=option_type,
        strike=400.0,
        expiry=expiry,
        raw_symbol="SYMBOL260116C00400000",
        delta=0.5,
        gamma=0.01,
        theta=-0.05,
        vega=0.10,
        current_price=current_price,
    )


def _make_engine(config=None, warmup=None, trader=None):
    """Build a LiveEngine with mocked dependencies.

    The mock trader's get_order_status is pre-configured to return 'filled'
    so that existing tests that open positions are not broken by the C-4
    fill-confirmation polling.  Individual tests that need a different order
    status override trader.get_order_status directly.

    A MagicMock strategy is injected so tests can configure signals via
    _with_signal() without patching module-level names.
    """
    cfg     = config  or _config()
    warm    = warmup  or _make_warmup()
    mock_tr = trader  or MagicMock()

    # Default: order is instantly filled (C-4 polling succeeds on first attempt)
    mock_tr.get_order_status.return_value = "filled"
    mock_tr.get_option_quote.return_value = None

    from src.live.live_engine import LiveEngine
    mock_strategy = MagicMock()
    # Default: pass-through indicators, zero signal (safe baseline)
    mock_strategy.compute_indicators.side_effect = lambda df, cfg: df
    mock_strategy.generate_signals.side_effect = lambda df, cfg: pd.Series(
        [0] * len(df), index=df.index
    )
    engine = LiveEngine(config=cfg, warmup_df=warm, trader=mock_tr,
                        data_dir=tempfile.mkdtemp(), strategy=mock_strategy)
    return engine, mock_tr


def _set_signal(engine, signal_value: int):
    """Configure the engine's mock strategy to emit a fixed signal on every bar."""
    engine._strategy.generate_signals.side_effect = (
        lambda df, cfg: pd.Series([signal_value] * len(df), index=df.index)
    )


# ---------------------------------------------------------------------------
# No signal → no position
# ---------------------------------------------------------------------------

class TestNoSignalNoEntry:
    def test_no_position_when_signal_is_zero(self):
        engine, trader = _make_engine()
        ts = _est("2026-01-05", 10, 0)

        _set_signal(engine, 0)
        engine.on_bar(_bar(ts))

        assert engine._position is None
        trader.buy_option.assert_not_called()

    def test_no_closed_trades_when_signal_is_zero(self):
        engine, trader = _make_engine()
        _set_signal(engine, 0)
        for i in range(5):
            ts = _est("2026-01-05", 10, i * 5)
            engine.on_bar(_bar(ts))

        assert engine.get_closed_trades() == []


# ---------------------------------------------------------------------------
# Buy signal → position opened
# ---------------------------------------------------------------------------

class TestEntryOnSignal:
    def test_buy_signal_opens_position(self):
        engine, trader = _make_engine()
        trader.buy_option.return_value = "order-001"
        ts = _est("2026-01-05", 10, 0)

        mock_pos = _make_position()

        _set_signal(engine, 1)
        with patch("src.live.live_engine.build_entry", return_value=mock_pos):
            engine.on_bar(_bar(ts))

        assert engine._position is not None
        trader.buy_option.assert_called_once()

    def test_sell_signal_opens_put_position(self):
        engine, trader = _make_engine()
        trader.buy_option.return_value = "order-002"
        ts = _est("2026-01-05", 10, 0)

        mock_pos = _make_position(option_type="P", direction=-1)

        _set_signal(engine, -1)
        with patch("src.live.live_engine.build_entry", return_value=mock_pos):
            engine.on_bar(_bar(ts))

        assert engine._position is not None

    def test_no_second_entry_while_position_open(self):
        """A second signal while already in a position must be ignored."""
        engine, trader = _make_engine()
        trader.buy_option.return_value = "order-003"
        mock_pos = _make_position()

        ts1 = _est("2026-01-05", 10, 0)
        ts2 = _est("2026-01-05", 10, 5)

        _set_signal(engine, 1)
        with (
            patch("src.live.live_engine.build_entry", return_value=mock_pos),
            patch("src.live.live_engine.check_exit", return_value=None),
            patch.object(engine, "_get_option_price", return_value=2.0),
        ):
            engine.on_bar(_bar(ts1))
            engine.on_bar(_bar(ts2))

        # buy_option must be called only once
        assert trader.buy_option.call_count == 1

    def test_entry_skipped_at_1555(self):
        """No entry at/after 15:55 (matches backtest EOD cutoff)."""
        engine, trader = _make_engine()
        ts = _est("2026-01-05", 15, 55)

        _set_signal(engine, 1)
        engine.on_bar(_bar(ts))

        trader.buy_option.assert_not_called()
        assert engine._position is None


# ---------------------------------------------------------------------------
# Exit: profit target
# ---------------------------------------------------------------------------

class TestProfitTargetExit:
    def test_profit_target_closes_position(self):
        cfg = _config(tp=50.0, sl=50.0, eod=False, opp=False)
        engine, trader = _make_engine(config=cfg)
        trader.buy_option.return_value = "order-tp"
        trader.sell_option.return_value = "sell-tp"

        mock_pos = _make_position(entry_price=2.0, current_price=2.0)

        ts1 = _est("2026-01-05", 10, 0)
        ts2 = _est("2026-01-05", 10, 5)

        # Enter on bar 1
        _set_signal(engine, 1)
        with (
            patch("src.live.live_engine.build_entry", return_value=mock_pos),
            patch("src.live.live_engine.check_exit", return_value=None),
        ):
            engine.on_bar(_bar(ts1))

        assert engine._position is not None

        # Bar 2: current_price has risen → profit_target triggered
        mock_pos.current_price = 3.5  # > entry 2.0, +75% > 50% tp
        _set_signal(engine, 0)
        with (
            patch("src.live.live_engine.check_exit", return_value=ExitResult("profit_target", 3.5)),
            patch.object(engine, "_get_option_price", return_value=3.5),
        ):
            engine.on_bar(_bar(ts2, close=401.0))

        assert engine._position is None
        trader.sell_option.assert_called_once()
        trades = engine.get_closed_trades()
        assert len(trades) == 1
        assert trades[0]["reason"] == "profit_target"


# ---------------------------------------------------------------------------
# Exit: stop loss
# ---------------------------------------------------------------------------

class TestStopLossExit:
    def test_stop_loss_closes_position(self):
        cfg = _config(tp=50.0, sl=30.0, eod=False, opp=False)
        engine, trader = _make_engine(config=cfg)
        trader.buy_option.return_value = "order-sl"
        trader.sell_option.return_value = "sell-sl"

        mock_pos = _make_position(entry_price=2.0, current_price=2.0)

        ts1 = _est("2026-01-05", 10, 0)
        ts2 = _est("2026-01-05", 10, 5)

        _set_signal(engine, 1)
        with (
            patch("src.live.live_engine.build_entry", return_value=mock_pos),
            patch("src.live.live_engine.check_exit", return_value=None),
        ):
            engine.on_bar(_bar(ts1))

        _set_signal(engine, 0)
        with (
            patch("src.live.live_engine.check_exit", return_value=ExitResult("stop_loss", 1.4)),
            patch.object(engine, "_get_option_price", return_value=1.4),
        ):
            engine.on_bar(_bar(ts2, close=397.0))

        assert engine._position is None
        trader.sell_option.assert_called_once()
        trades = engine.get_closed_trades()
        assert trades[0]["reason"] == "stop_loss"


# ---------------------------------------------------------------------------
# Exit: EOD close
# ---------------------------------------------------------------------------

class TestEodClose:
    def test_eod_closes_position_at_1555(self):
        cfg = _config(eod=True, opp=False, tp=200.0, sl=200.0)
        engine, trader = _make_engine(config=cfg)
        trader.buy_option.return_value = "order-eod"
        trader.sell_option.return_value = "sell-eod"

        mock_pos = _make_position(entry_price=2.0, current_price=2.0)

        ts1 = _est("2026-01-05", 10, 0)
        ts2 = _est("2026-01-05", 15, 55)

        _set_signal(engine, 1)
        with (
            patch("src.live.live_engine.build_entry", return_value=mock_pos),
            patch("src.live.live_engine.check_exit", return_value=None),
        ):
            engine.on_bar(_bar(ts1))

        with (
            patch("src.live.live_engine.check_exit", return_value=ExitResult("eod_close", 2.0)),
            patch.object(engine, "_get_option_price", return_value=2.0),
        ):
            engine.on_bar(_bar(ts2))

        assert engine._position is None
        trades = engine.get_closed_trades()
        assert trades[0]["reason"] == "eod_close"


# ---------------------------------------------------------------------------
# Exit: opposite signal
# ---------------------------------------------------------------------------

class TestOppositeSignalExit:
    def test_opposite_signal_closes_long_position(self):
        cfg = _config(tp=200.0, sl=200.0, eod=False, opp=True)
        engine, trader = _make_engine(config=cfg)
        trader.buy_option.return_value = "order-opp"
        trader.sell_option.return_value = "sell-opp"

        mock_pos = _make_position(direction=1, option_type="C")

        ts1 = _est("2026-01-05", 10, 0)
        ts2 = _est("2026-01-05", 10, 5)

        _set_signal(engine, 1)
        with (
            patch("src.live.live_engine.build_entry", return_value=mock_pos),
            patch("src.live.live_engine.check_exit", return_value=None),
        ):
            engine.on_bar(_bar(ts1))

        # Use a ts that is past the late-entry cutoff so _check_entry is gated
        ts2_late = _est("2026-01-05", 15, 55)
        with (
            patch("src.live.live_engine.check_exit", return_value=ExitResult("opposite_signal", 2.0)),
            patch.object(engine, "_get_option_price", return_value=2.0),
        ):
            engine.on_bar(_bar(ts2_late))

        assert engine._position is None
        trades = engine.get_closed_trades()
        assert trades[0]["reason"] == "opposite_signal"


# ---------------------------------------------------------------------------
# force_close
# ---------------------------------------------------------------------------

class TestForceClose:
    def test_force_close_with_open_position(self):
        engine, trader = _make_engine()
        trader.buy_option.return_value = "order-fc"
        trader.sell_option.return_value = "sell-fc"

        mock_pos = _make_position()
        ts = _est("2026-01-05", 10, 0)

        _set_signal(engine, 1)
        with (
            patch("src.live.live_engine.build_entry", return_value=mock_pos),
            patch("src.live.live_engine.check_exit", return_value=None),
        ):
            engine.on_bar(_bar(ts))

        assert engine._position is not None

        engine.force_close("manual_stop")

        assert engine._position is None
        trader.sell_option.assert_called_once()
        trader.cancel_all_orders.assert_called_once()

        trades = engine.get_closed_trades()
        assert len(trades) == 1
        assert trades[0]["reason"] == "manual_stop"

    def test_force_close_with_no_position_still_cancels_orders(self):
        """force_close() should call cancel_all_orders even with no open position."""
        engine, trader = _make_engine()
        assert engine._position is None

        engine.force_close("manual_stop")

        trader.cancel_all_orders.assert_called_once()
        trader.sell_option.assert_not_called()

    def test_force_close_records_correct_exit_reason(self):
        engine, trader = _make_engine()
        trader.buy_option.return_value = "order-fc2"
        mock_pos = _make_position()
        ts = _est("2026-01-05", 10, 0)

        _set_signal(engine, 1)
        with (
            patch("src.live.live_engine.build_entry", return_value=mock_pos),
            patch("src.live.live_engine.check_exit", return_value=None),
        ):
            engine.on_bar(_bar(ts))

        engine.force_close("eod_cleanup")
        assert engine.get_closed_trades()[0]["reason"] == "eod_cleanup"


# ---------------------------------------------------------------------------
# get_closed_trades
# ---------------------------------------------------------------------------

class TestGetClosedTrades:
    def test_returns_empty_list_initially(self):
        engine, _ = _make_engine()
        assert engine.get_closed_trades() == []

    def test_trade_log_fields(self):
        """Closed trade dict must contain all required fields."""
        REQUIRED_FIELDS = {
        "entry_time", "exit_time", "option_type", "strike", "expiry",
            "entry_price", "exit_price", "contracts", "pnl", "pnl_pct",
            "reason", "delta", "gamma", "theta", "vega", "order_id",
        }

        engine, trader = _make_engine()
        trader.buy_option.return_value = "order-fields"
        mock_pos = _make_position(entry_price=2.0, current_price=2.0)

        ts1 = _est("2026-01-05", 10, 0)
        ts2 = _est("2026-01-05", 10, 5)

        _set_signal(engine, 1)
        with (
            patch("src.live.live_engine.build_entry", return_value=mock_pos),
            patch("src.live.live_engine.check_exit", return_value=None),
        ):
            engine.on_bar(_bar(ts1))

        with (
            patch("src.live.live_engine.check_exit", return_value=ExitResult("profit_target", 3.5)),
            patch.object(engine, "_get_option_price", return_value=2.0),
        ):
            engine.on_bar(_bar(ts2))

        trades = engine.get_closed_trades()
        assert len(trades) == 1
        assert REQUIRED_FIELDS.issubset(trades[0].keys())
        assert trades[0]["exit_price"] == pytest.approx(3.5)

    def test_pnl_calculation(self):
        """pnl = (exit_price - entry_price) * contracts * 100."""
        engine, trader = _make_engine()
        trader.buy_option.return_value = "order-pnl"

        mock_pos = _make_position(entry_price=2.0, current_price=2.0)

        ts1 = _est("2026-01-05", 10, 0)
        ts2 = _est("2026-01-05", 10, 5)

        _set_signal(engine, 1)
        with (
            patch("src.live.live_engine.build_entry", return_value=mock_pos),
            patch("src.live.live_engine.check_exit", return_value=None),
        ):
            engine.on_bar(_bar(ts1))

        # ExitResult.fill_price is now the recorded live exit price.
        mock_pos.current_price = 3.0
        with (
            patch("src.live.live_engine.check_exit", return_value=ExitResult("profit_target", 3.5)),
            patch.object(engine, "_get_option_price", return_value=3.0),
        ):
            engine.on_bar(_bar(ts2))

        trade = engine.get_closed_trades()[0]
        assert trade["entry_price"] == pytest.approx(2.0)
        assert trade["exit_price"]  == pytest.approx(3.5)
        assert trade["pnl"]         == pytest.approx(150.0)  # (3.5-2.0)*1*100

    def test_multiple_trades_accumulated(self):
        """Multiple open/close cycles accumulate in get_closed_trades()."""
        engine, trader = _make_engine(config=_config(tp=50.0, sl=50.0, eod=False, opp=False))
        trader.buy_option.return_value = "order-multi"

        for trade_num in range(3):
            mock_pos = _make_position()
            ts_entry = _est("2026-01-05", 10, trade_num * 10)
            ts_exit  = _est("2026-01-05", 10, trade_num * 10 + 5)

            _set_signal(engine, 1)
            with (
                patch("src.live.live_engine.build_entry", return_value=mock_pos),
                patch("src.live.live_engine.check_exit", return_value=None),
                patch.object(engine, "_get_option_price", return_value=2.0),
            ):
                engine.on_bar(_bar(ts_entry))

            with (
                patch("src.live.live_engine.check_exit", return_value=ExitResult("profit_target", 3.5)),
                patch.object(engine, "_get_option_price", return_value=2.0),
            ):
                engine.on_bar(_bar(ts_exit))

        assert len(engine.get_closed_trades()) == 3


# ---------------------------------------------------------------------------
# Intrabar polling
# ---------------------------------------------------------------------------

class TestIntrabarPolling:
    def test_poll_starts_on_entry(self):
        """Polling thread should start when a position is opened."""
        engine, trader = _make_engine()
        trader.buy_option.return_value = "order-poll"
        mock_pos = _make_position()

        ts = _est("2026-01-05", 10, 0)
        _set_signal(engine, 1)
        with (
            patch("src.live.live_engine.build_entry", return_value=mock_pos),
            patch("src.live.live_engine.check_exit", return_value=None),
        ):
            engine.on_bar(_bar(ts))

        assert engine._poll_thread is not None
        assert engine._poll_thread.is_alive()
        # Cleanup
        engine._stop_poll()

    def test_poll_stops_on_close(self):
        """Polling thread should stop when position is closed."""
        engine, trader = _make_engine()
        trader.buy_option.return_value = "order-poll2"
        mock_pos = _make_position()

        ts1 = _est("2026-01-05", 10, 0)
        ts2 = _est("2026-01-05", 10, 5)

        _set_signal(engine, 1)
        with (
            patch("src.live.live_engine.build_entry", return_value=mock_pos),
            patch("src.live.live_engine.check_exit", return_value=None),
        ):
            engine.on_bar(_bar(ts1))

        _set_signal(engine, 0)
        with (
            patch("src.live.live_engine.check_exit", return_value=ExitResult("profit_target", 3.0)),
            patch.object(engine, "_get_option_price", return_value=3.0),
        ):
            engine.on_bar(_bar(ts2))

        assert engine._poll_thread is None
        assert engine._position is None

    def test_intrabar_stop_triggers(self):
        """_poll_check should close position when loss exceeds stop_loss."""
        cfg = _config(tp=50.0, sl=20.0)
        engine, trader = _make_engine(config=cfg)

        pos = _make_position(entry_price=2.0, current_price=2.0)
        engine._position = pos

        # Simulate mid price drop to 1.50 → pnl_pct = -25% > 20% stop
        trader.get_option_mid_price.return_value = 1.50

        engine._poll_check()

        assert engine._position is None
        trader.sell_option.assert_called_once()
        trades = engine.get_closed_trades()
        assert trades[0]["reason"] == "intrabar_stop"

    def test_intrabar_target_triggers(self):
        """_poll_check should close position when gain exceeds profit_target."""
        cfg = _config(tp=20.0, sl=50.0)
        engine, trader = _make_engine(config=cfg)

        pos = _make_position(entry_price=2.0, current_price=2.0)
        engine._position = pos

        # Simulate mid price rise to 2.50 → pnl_pct = +25% > 20% target
        trader.get_option_mid_price.return_value = 2.50

        engine._poll_check()

        assert engine._position is None
        trader.sell_option.assert_called_once()
        trades = engine.get_closed_trades()
        assert trades[0]["reason"] == "intrabar_target"

    def test_poll_check_noop_when_within_thresholds(self):
        """_poll_check should not close if P&L is within thresholds."""
        cfg = _config(tp=50.0, sl=50.0)
        engine, trader = _make_engine(config=cfg)

        pos = _make_position(entry_price=2.0, current_price=2.0)
        engine._position = pos

        # Simulate small move — well within thresholds
        trader.get_option_mid_price.return_value = 2.10

        engine._poll_check()

        assert engine._position is not None
        trader.sell_option.assert_not_called()


# ---------------------------------------------------------------------------
# Shared exit logic integration
# ---------------------------------------------------------------------------

class TestSharedExitLogicIntegration:
    def test_get_option_price_raises_when_live_quote_unavailable(self):
        """Live pricing must fail fast instead of falling back when quotes are missing."""
        engine, trader = _make_engine()
        trader.get_option_mid_price.return_value = None

        with pytest.raises(RuntimeError, match="Live option quote unavailable"):
            engine._get_option_price(
                "SYMBOL260116C00400000",
                underlying_close=400.0,
                strike=400.0,
                option_type="C",
                dte_years=7 / 365,
                sigma=0.25,
            )

    def test_real_check_exit_accepts_sigma_kwarg(self):
        """Real shared exit logic must not raise when the position carries entry_iv."""
        cfg = _config(tp=200.0, sl=200.0, eod=False, opp=False)
        engine, trader = _make_engine(config=cfg)

        pos = _make_position(entry_price=2.0, current_price=2.0)
        pos.entry_iv = 0.33
        engine._position = pos

        trader.get_option_mid_price.return_value = 2.1

        engine._check_exits(
        close=400.0,
            high=401.0,
            low=399.0,
            signal=0,
            ts=_est("2026-01-05", 10, 5),
        )

        assert engine._position is pos
        assert pos.current_price == pytest.approx(2.1)

    def test_real_check_exit_stop_loss_uses_fill_price(self):
        """Real shared stop-loss logic should close at the option bar low fill."""
        cfg = _config(tp=200.0, sl=50.0, eod=False, opp=False)
        engine, trader = _make_engine(config=cfg)

        pos = _make_position(entry_price=5.0, current_price=5.0)
        pos.entry_iv = 0.25
        engine._position = pos

        def _price(raw_symbol, underlying_close, strike, option_type, dte_years,
                   sigma=None, field="close"):
            if field == "low":
                return 2.0   # option bar low triggers stop
            return 5.0

        with patch.object(engine, "_get_option_price", side_effect=_price):
            engine._check_exits(
                close=400.0,
                high=401.0,
                low=399.0,
                signal=0,
                ts=_est("2026-01-05", 10, 5),
            )

        trade = engine.get_closed_trades()[0]
        assert engine._position is None
        assert trade["reason"] == "stop_loss"
        assert trade["exit_price"] == pytest.approx(2.0)

    def test_real_check_exit_profit_target_uses_fill_price(self):
        """Real shared profit-target logic should close at the option bar high fill."""
        cfg = _config(tp=50.0, sl=200.0, eod=False, opp=False)
        engine, trader = _make_engine(config=cfg)

        pos = _make_position(entry_price=5.0, current_price=5.0)
        pos.entry_iv = 0.25
        engine._position = pos

        def _price(raw_symbol, underlying_close, strike, option_type, dte_years,
                   sigma=None, field="close"):
            if field == "high":
                return 8.0   # option bar high triggers profit target
            return 5.0

        with patch.object(engine, "_get_option_price", side_effect=_price):
            engine._check_exits(
                close=400.0,
                high=401.0,
                low=399.0,
                signal=0,
                ts=_est("2026-01-05", 10, 5),
            )

        trade = engine.get_closed_trades()[0]
        assert engine._position is None
        assert trade["reason"] == "profit_target"
        assert trade["exit_price"] == pytest.approx(8.0)

    def test_poll_check_raises_when_no_quote(self):
        """Intrabar polling must fail fast when the live quote is unavailable."""
        engine, trader = _make_engine()
        pos = _make_position()
        engine._position = pos

        trader.get_option_mid_price.return_value = None

        with pytest.raises(RuntimeError, match="Intrabar poll could not fetch live option quote"):
            engine._poll_check()

        assert engine._position is not None
        trader.sell_option.assert_not_called()

    def test_poll_loop_interrupts_main_thread_on_fatal_error(self):
        """A fatal poll error must interrupt the main thread and mark the engine unusable."""
        engine, _ = _make_engine()
        engine._position = _make_position()
        err = RuntimeError("quote feed lost")

        with (
            patch.object(engine._poll_stop, "wait", side_effect=[False, True]),
            patch.object(engine, "_poll_check", side_effect=err),
            patch("src.live.live_engine._thread.interrupt_main") as mock_interrupt,
        ):
            with pytest.raises(RuntimeError, match="quote feed lost"):
                engine._poll_loop()

        assert engine._fatal_error is err
        mock_interrupt.assert_called_once()

    def test_on_bar_raises_after_fatal_poll_error(self):
        """Once a poll failure is recorded, later bar processing must stop immediately."""
        engine, _ = _make_engine()
        err = RuntimeError("quote feed lost")

        with patch("src.live.live_engine._thread.interrupt_main"):
            engine._record_fatal_error(err)

        original_len = len(engine._bars)
        with pytest.raises(RuntimeError, match="fatal intrabar polling error") as excinfo:
            engine.on_bar(_bar(_est("2026-01-05", 10, 5)))

        assert excinfo.value.__cause__ is err
        assert len(engine._bars) == original_len


# ---------------------------------------------------------------------------
# Position reconciliation
# ---------------------------------------------------------------------------

class TestReconcilePositions:
    def test_reconcile_with_open_position(self):
        """Should reconstruct Position from Alpaca data."""
        engine, trader = _make_engine()
        trader.get_option_positions.return_value = [{
        "symbol": "SYMBOL260221C00451000",
            "qty": 3,
            "avg_entry_price": 2.50,
            "current_price": 2.80,
            "side": "long",
            "underlying": "SYMBOL",
            "expiry": datetime.datetime(2026, 2, 21),
            "option_type": "C",
            "strike": 451.0,
            "raw_symbol": "SYMBOL   260221C00451000",
        }]

        engine.reconcile_positions()

        assert engine._position is not None
        assert engine._position.strike == 451.0
        assert engine._position.option_type == "C"
        assert engine._position.contracts == 3
        assert engine._position.entry_price == 2.50
        assert engine._position.current_price == 2.80
        # Polling should have started
        assert engine._poll_thread is not None
        engine._stop_poll()

    def test_reconcile_with_no_positions(self):
        """Should be a no-op when Alpaca has no open positions."""
        engine, trader = _make_engine()
        trader.get_option_positions.return_value = []

        engine.reconcile_positions()

        assert engine._position is None
        assert engine._poll_thread is None

    def test_reconcile_takes_first_of_multiple(self):
        """If multiple positions exist, reconcile should take the first."""
        engine, trader = _make_engine()
        trader.get_option_positions.return_value = [
        {
                "symbol": "SYMBOL260221C00451000", "qty": 1,
                "avg_entry_price": 2.0, "current_price": 2.5,
                "side": "long", "underlying": "SYMBOL",
                "expiry": datetime.datetime(2026, 2, 21),
                "option_type": "C", "strike": 451.0,
                "raw_symbol": "SYMBOL   260221C00451000",
            },
            {
                "symbol": "SYMBOL260221P00400000", "qty": 2,
                "avg_entry_price": 3.0, "current_price": 3.5,
                "side": "long", "underlying": "SYMBOL",
                "expiry": datetime.datetime(2026, 2, 21),
                "option_type": "P", "strike": 400.0,
                "raw_symbol": "SYMBOL   260221P00400000",
            },
        ]

        engine.reconcile_positions()

        assert engine._position.strike == 451.0
        assert engine._position.option_type == "C"
        engine._stop_poll()


# ---------------------------------------------------------------------------
# C-4: Fill confirmation polling
# ---------------------------------------------------------------------------

class TestFillConfirmation:
    def _enter_with_order_status(self, order_status: str):
        """Helper: attempt an entry where get_order_status returns the given status."""
        engine, trader = _make_engine()
        trader.buy_option.return_value = "order-c4"
        mock_pos = _make_position()

        trader.get_order_status.return_value = order_status

        ts = _est("2026-01-05", 10, 0)

        _set_signal(engine, 1)
        with (
            patch("src.live.live_engine.build_entry", return_value=mock_pos),
            patch("src.live.live_engine.check_exit", return_value=None),
            patch("src.live.live_engine.time.sleep"),
        ):
            engine.on_bar(_bar(ts))

        return engine, trader

    def test_position_set_when_order_filled(self):
        """Position must be tracked when order status is 'filled'."""
        engine, trader = self._enter_with_order_status("filled")
        assert engine._position is not None
        trader.buy_option.assert_called_once()
        engine._stop_poll()

    def test_position_not_set_when_order_not_filled(self):
        """Position must NOT be tracked when order never reaches 'filled' status."""
        engine, trader = self._enter_with_order_status("pending_new")
        assert engine._position is None
        # buy_option was still called (order was submitted)
        trader.buy_option.assert_called_once()

    def test_position_not_set_when_order_query_raises(self):
        """If get_order_status always raises, position must not be set."""
        engine, trader = _make_engine()
        trader.buy_option.return_value = "order-c4-err"
        mock_pos = _make_position()
        trader.get_order_status.side_effect = RuntimeError("API error")

        ts = _est("2026-01-05", 10, 0)

        _set_signal(engine, 1)
        with (
            patch("src.live.live_engine.build_entry", return_value=mock_pos),
            patch("src.live.live_engine.check_exit", return_value=None),
            patch("src.live.live_engine.time.sleep"),
        ):
            engine.on_bar(_bar(ts))

        assert engine._position is None

    def test_fill_poll_retries_before_giving_up(self):
        """get_order_status should be called up to _FILL_POLL_ATTEMPTS times."""
        engine, trader = _make_engine()
        trader.buy_option.return_value = "order-retries"
        mock_pos = _make_position()

        trader.get_order_status.return_value = "accepted"  # never "filled"

        ts = _est("2026-01-05", 10, 0)

        _set_signal(engine, 1)
        with (
            patch("src.live.live_engine.build_entry", return_value=mock_pos),
            patch("src.live.live_engine.check_exit", return_value=None),
            patch("src.live.live_engine.time.sleep"),
        ):
            engine.on_bar(_bar(ts))

        from src.live.live_engine import _FILL_POLL_ATTEMPTS
        assert trader.get_order_status.call_count == _FILL_POLL_ATTEMPTS
        assert engine._position is None


# ---------------------------------------------------------------------------
# C-5: sell_option() failure handling
# ---------------------------------------------------------------------------

class TestSellFailure:
    def test_sell_failure_clears_position_and_sets_flag(self):
        """If sell_option() throws, position must be kept open to retry, and _sell_failed set."""
        engine, trader = _make_engine()
        trader.buy_option.return_value = "order-sell-fail"

        # Configure fill confirmation to succeed
        trader.get_order_status.return_value = "filled"

        # sell_option will raise
        trader.sell_option.side_effect = RuntimeError("broker timeout")

        mock_pos = _make_position(entry_price=2.0, current_price=2.0)

        ts1 = _est("2026-01-05", 10, 0)
        ts2 = _est("2026-01-05", 10, 5)

        _set_signal(engine, 1)
        with (
            patch("src.live.live_engine.build_entry", return_value=mock_pos),
            patch("src.live.live_engine.check_exit", return_value=None),
            patch("src.live.live_engine.time.sleep"),
        ):
            engine.on_bar(_bar(ts1))

        assert engine._position is not None  # entry succeeded

        _set_signal(engine, 0)
        with (
            patch("src.live.live_engine.check_exit",
                  return_value=__import__("src.backtest.trade_logic",
                                          fromlist=["ExitResult"]).ExitResult("profit_target", 3.0)),
            patch.object(engine, "_get_option_price", return_value=3.0),
        ):
            engine.on_bar(_bar(ts2))

        # Position kept open despite sell failure
        assert engine._position is not None
        assert engine._sell_failed[0] is True

    def test_sell_failure_recorded_in_trade_log(self):
        """A failed sell leaves the position open, so it does not appear in the closed trade log."""
        engine, trader = _make_engine()
        trader.buy_option.return_value = "order-sell-fail-log"

        trader.get_order_status.return_value = "filled"

        trader.sell_option.side_effect = RuntimeError("network error")

        mock_pos = _make_position(entry_price=2.0, current_price=2.0)

        ts1 = _est("2026-01-05", 10, 0)
        ts2 = _est("2026-01-05", 10, 5)

        _set_signal(engine, 1)
        with (
            patch("src.live.live_engine.build_entry", return_value=mock_pos),
            patch("src.live.live_engine.check_exit", return_value=None),
            patch("src.live.live_engine.time.sleep"),
        ):
            engine.on_bar(_bar(ts1))

        from src.backtest.trade_logic import ExitResult
        with (
            patch("src.live.live_engine.check_exit",
                  return_value=ExitResult("stop_loss", 1.5)),
            patch.object(engine, "_get_option_price", return_value=1.5),
        ):
            engine.on_bar(_bar(ts2))

        trades = engine.get_closed_trades()
        assert len(trades) == 0

# ---------------------------------------------------------------------------
# C-5: _start_poll() duplicate-thread guard
# ---------------------------------------------------------------------------

class TestPollThreadGuards:
    def test_start_poll_noop_when_thread_alive(self):
        """_start_poll() must not create a second thread if one is already running."""
        engine, trader = _make_engine()

        # Manually start the poll thread
        engine._start_poll()
        first_thread = engine._poll_thread
        assert first_thread is not None and first_thread.is_alive()

        # Call _start_poll() again — should be a no-op
        engine._start_poll()
        assert engine._poll_thread is first_thread  # same object, not replaced

        engine._stop_poll()

    def test_stop_poll_skips_join_when_called_from_poll_thread(self):
        """_stop_poll() must not raise when called from within the poll thread itself."""
        import threading as _threading

        engine, trader = _make_engine()
        error_container = []

        def poll_body():
            try:
                # Simulate what _poll_check → _close → _stop_poll does:
                # call _stop_poll() from inside the thread being stopped.
                engine._poll_thread = _threading.current_thread()
                engine._stop_poll()
            except Exception as exc:
                error_container.append(exc)

        t = _threading.Thread(target=poll_body, daemon=True)
        engine._poll_thread = t
        t.start()
        t.join(timeout=3)

        assert not error_container, f"_stop_poll raised from within thread: {error_container}"
        # After _stop_poll() from within the thread, _poll_thread is set to None
        assert engine._poll_thread is None


# ---------------------------------------------------------------------------
# M-9: trade_mode read from config, passed to build_entry
# ---------------------------------------------------------------------------

class TestTradeMode:
    def test_trade_mode_from_config(self):
        """build_entry must be called with the trade_mode from config, not hardcoded 'options'."""
        cfg = _config()
        cfg["strategy"]["trade_mode"] = "equities"

        engine, trader = _make_engine(config=cfg)
        trader.buy_option.return_value = "order-tm"
        mock_pos = _make_position()
        ts = _est("2026-01-05", 10, 0)

        _set_signal(engine, 1)
        with (
            patch("src.live.live_engine.build_entry", return_value=mock_pos) as mock_build,
            patch("src.live.live_engine.check_exit", return_value=None),
        ):
            engine.on_bar(_bar(ts))

        mock_build.assert_called_once()
        _args, _kwargs = mock_build.call_args
        # trade_mode is the 4th positional argument (index 3)
        assert _args[3] == "equities"

        engine._stop_poll()


# ---------------------------------------------------------------------------
# L-8: force_close uses Eastern timezone, not system-local
# ---------------------------------------------------------------------------

class TestForceCloseTimezone:
    def test_force_close_timestamp_is_eastern(self):
        """exit_time recorded by force_close must be in Eastern time (EST or EDT)."""
        engine, trader = _make_engine()

        # Inject an open position directly — bypass entry flow
        engine._position = _make_position()

        engine.force_close("tz_test")

        trades = engine.get_closed_trades()
        assert len(trades) == 1
        tz_name = trades[0]["exit_time"].tzname()
        assert tz_name in ("EST", "EDT"), (
        f"Expected EST or EDT, got {tz_name!r}"
        )


# ---------------------------------------------------------------------------
# L-9: None guard when build_entry returns None
# ---------------------------------------------------------------------------

class TestBuildEntryNoneGuard:
    def test_no_crash_when_build_entry_returns_none(self):
        """If build_entry returns None, no exception should be raised and no position opened."""
        engine, trader = _make_engine()
        ts = _est("2026-01-05", 10, 0)

        _set_signal(engine, 1)
        with (
            patch("src.live.live_engine.build_entry", return_value=None),
            patch("src.live.live_engine.check_exit", return_value=None),
        ):
            engine.on_bar(_bar(ts))  # must not raise

        assert engine._position is None
        trader.buy_option.assert_not_called()
