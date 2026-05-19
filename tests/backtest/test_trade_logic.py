"""
Tests for src/backtest/trade_logic.py — the deep module.

Covers the previously-untestable equity exit logic (intrabar stop/limit fills)
and verifies that options paths delegate correctly.
"""

from datetime import datetime
from unittest.mock import ANY, patch, MagicMock

import pandas as pd
import pytest

from src.options.position import Position
from src.backtest.trade_logic import (
    BarContext, ExitConfig, ExitResult,
    check_exit, build_entry,
    _parse_cutoff_time,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bar(open=400.0, close=400.0, high=401.0, low=399.0, signal=0, hour=10, minute=0):
    ts = pd.Timestamp(f"2025-01-02 {hour:02d}:{minute:02d}:00", tz="America/New_York")
    return BarContext(timestamp=ts, open=open, close=close, high=high, low=low,
                      signal=signal, hour=hour, minute=minute)


def _exit_config(tp=20.0, sl=20.0, eod=False, opp=True):
    return ExitConfig(profit_target_pct=tp, stop_loss_pct=sl,
                      eod_close=eod, opposite_signal=opp)


def _equity_pos(direction=1, entry=400.0, stop=320.0, limit=480.0):
    return Position(
        direction=direction, entry_price=entry,
        entry_time=datetime(2025, 1, 2, 9, 35),
        contracts=10, trade_mode="equities",
        stop_price=stop, limit_price=limit,
    )


def _option_pos(direction=1, entry=5.0, current=5.0, option_type=None):
    ot = option_type if option_type is not None else ("C" if direction == 1 else "P")
    symbol = f"SYMBOL250116{ot}00400000"
    return Position(
        direction=direction, entry_price=entry,
        entry_time=datetime(2025, 1, 2, 9, 35),
        contracts=1, trade_mode="options",
        option_type=ot,
        strike=400.0,
        expiry=pd.Timestamp("2025-01-16", tz="America/New_York").to_pydatetime(),
        raw_symbol=symbol,
        current_price=current,
    )


# ---------------------------------------------------------------------------
# check_exit — Gap Aware Fills
# ---------------------------------------------------------------------------

class TestCheckExitGapAware:
    def test_long_stop_loss_gaps_down_at_open(self):
        """Market gaps down past stop price → fill at open, not stop price."""
        pos = _equity_pos(direction=1, stop=320.0)
        # Open at 310.0, which is below stop at 320.0
        bar = _bar(open=310.0, close=315.0, low=305.0, high=325.0)
        result = check_exit(pos, bar, _exit_config())
        assert result == ExitResult("stop_loss", 310.0) # Fills at 310 open

    def test_short_stop_loss_gaps_up_at_open(self):
        """Short position: market gaps up past stop price → fill at open."""
        pos = _equity_pos(direction=-1, stop=480.0)
        # Open at 490.0, which is above stop at 480.0
        bar = _bar(open=490.0, close=485.0, low=470.0, high=500.0)
        result = check_exit(pos, bar, _exit_config())
        assert result == ExitResult("stop_loss", 490.0) # Fills at 490 open

    def test_long_profit_target_gaps_up_at_open(self):
        """Market gaps up past profit limit → fill at open (better price)."""
        pos = _equity_pos(direction=1, limit=480.0)
        # Open at 490.0, which is above limit at 480.0
        bar = _bar(open=490.0, close=485.0, low=470.0, high=500.0)
        result = check_exit(pos, bar, _exit_config())
        assert result == ExitResult("profit_target", 490.0) # Fills at 490 open

    def test_short_profit_target_gaps_down_at_open(self):
        """Short position: market gaps down past profit limit → fill at open."""
        # Entry 400, limit 320, stop 480 (far away)
        pos = _equity_pos(direction=-1, entry=400.0, limit=320.0, stop=480.0)
        # Open at 310.0, which is below limit at 320.0
        bar = _bar(open=310.0, close=315.0, low=305.0, high=325.0)
        result = check_exit(pos, bar, _exit_config())
        assert result == ExitResult("profit_target", 310.0) # Fills at 310 open


# ---------------------------------------------------------------------------
# check_exit — equity stop loss
# ---------------------------------------------------------------------------

class TestCheckExitEquityStopLoss:
    def test_long_stop_fires_when_low_breaches(self):
        pos = _equity_pos(direction=1, stop=320.0)
        bar = _bar(close=330.0, low=319.0, high=340.0)
        result = check_exit(pos, bar, _exit_config())
        assert result == ExitResult("stop_loss", 320.0)

    def test_short_stop_fires_when_high_breaches(self):
        pos = _equity_pos(direction=-1, entry=400.0, stop=480.0, limit=320.0)
        bar = _bar(close=470.0, high=481.0, low=460.0)
        result = check_exit(pos, bar, _exit_config())
        assert result == ExitResult("stop_loss", 480.0)

    def test_long_stop_does_not_fire_above_stop(self):
        pos = _equity_pos(direction=1, stop=320.0)
        bar = _bar(close=400.0, low=321.0, high=410.0)
        result = check_exit(pos, bar, _exit_config(opp=False))
        assert result is None

    def test_short_stop_does_not_fire_below_stop(self):
        pos = _equity_pos(direction=-1, entry=400.0, stop=480.0, limit=320.0)
        bar = _bar(close=400.0, high=479.0, low=390.0)
        result = check_exit(pos, bar, _exit_config(opp=False))
        assert result is None


# ---------------------------------------------------------------------------
# check_exit — equity profit target
# ---------------------------------------------------------------------------

class TestCheckExitEquityProfitTarget:
    def test_long_tp_fires_when_high_reaches_limit(self):
        pos = _equity_pos(direction=1, limit=480.0)
        bar = _bar(close=470.0, high=481.0, low=460.0)
        result = check_exit(pos, bar, _exit_config())
        assert result == ExitResult("profit_target", 480.0)

    def test_short_tp_fires_when_low_reaches_limit(self):
        pos = _equity_pos(direction=-1, entry=400.0, stop=480.0, limit=320.0)
        bar = _bar(close=330.0, low=319.0, high=340.0)
        result = check_exit(pos, bar, _exit_config())
        assert result == ExitResult("profit_target", 320.0)


# ---------------------------------------------------------------------------
# check_exit — priority (stop beats limit)
# ---------------------------------------------------------------------------

class TestCheckExitEquityPriority:
    def test_stop_beats_limit_on_wide_bar_long(self):
        pos = _equity_pos(direction=1, stop=320.0, limit=480.0)
        bar = _bar(close=400.0, low=319.0, high=481.0)
        result = check_exit(pos, bar, _exit_config())
        assert result.reason == "stop_loss"
        assert result.fill_price == 320.0

    def test_stop_beats_limit_on_wide_bar_short(self):
        pos = _equity_pos(direction=-1, entry=400.0, stop=480.0, limit=320.0)
        bar = _bar(close=400.0, high=481.0, low=319.0)
        result = check_exit(pos, bar, _exit_config())
        assert result.reason == "stop_loss"
        assert result.fill_price == 480.0


# ---------------------------------------------------------------------------
# check_exit — opposite signal
# ---------------------------------------------------------------------------

class TestCheckExitEquityOppositeSignal:
    def test_opposite_signal_fires(self):
        pos = _equity_pos(direction=1)
        bar = _bar(signal=-1)
        result = check_exit(pos, bar, _exit_config(opp=True))
        assert result.reason == "opposite_signal"
        assert result.fill_price == 400.0  # bar close price

    def test_opposite_signal_disabled(self):
        pos = _equity_pos(direction=1)
        bar = _bar(signal=-1)
        result = check_exit(pos, bar, _exit_config(opp=False))
        assert result is None

    def test_same_direction_signal_no_exit(self):
        pos = _equity_pos(direction=1)
        bar = _bar(signal=1)
        result = check_exit(pos, bar, _exit_config(opp=True))
        assert result is None

    def test_zero_signal_no_exit(self):
        pos = _equity_pos(direction=1)
        bar = _bar(signal=0)
        result = check_exit(pos, bar, _exit_config(opp=True))
        assert result is None


# ---------------------------------------------------------------------------
# check_exit — EOD close
# ---------------------------------------------------------------------------

class TestCheckExitEquityEodClose:
    def test_eod_fires_at_1555(self):
        pos = _equity_pos(direction=1)
        bar = _bar(hour=15, minute=55)
        result = check_exit(pos, bar, _exit_config(eod=True, opp=False))
        assert result.reason == "eod_close"
        assert result.fill_price == 400.0  # bar close price

    def test_eod_does_not_fire_before_1555(self):
        pos = _equity_pos(direction=1)
        bar = _bar(hour=15, minute=50)
        result = check_exit(pos, bar, _exit_config(eod=True, opp=False))
        assert result is None

    def test_eod_disabled(self):
        pos = _equity_pos(direction=1)
        bar = _bar(hour=15, minute=55)
        result = check_exit(pos, bar, _exit_config(eod=False, opp=False))
        assert result is None


# ---------------------------------------------------------------------------
# check_exit — price update side effect
# ---------------------------------------------------------------------------

class TestCheckExitEquityPriceUpdate:
    def test_current_price_updated_to_bar_close(self):
        pos = _equity_pos(direction=1)
        bar = _bar(close=405.0)
        check_exit(pos, bar, _exit_config(opp=False))
        assert pos.current_price == 405.0


# ---------------------------------------------------------------------------
# check_exit — options delegation
# ---------------------------------------------------------------------------

class TestCheckExitOptions:
    def test_delegates_to_check_option_exit(self):
        # Use tp=200% so intrabar high price (7.5) doesn't trigger the intrabar check
        # (pnl_pct = (7.5-5.0)/5.0*100 = 50% < 200%), letting it fall through to
        # check_option_exit which is patched to return "profit_target".
        pos = _option_pos(direction=1, entry=5.0, current=5.0)
        bar = _bar(close=400.0, signal=0)
        price_fn = MagicMock(return_value=7.5)

        with patch("src.backtest.trade_logic.check_option_exit", return_value="profit_target"):
            result = check_exit(pos, bar, _exit_config(tp=200.0, sl=200.0), get_option_price=price_fn)

        assert result.reason == "profit_target"
        assert result.fill_price == 7.5  # close price from get_option_price(bar.close)
        # price_fn called 3 times: bar.close (update), bar.low (stop check), bar.high (tp check)
        assert price_fn.call_count == 3

    def test_returns_none_when_no_exit(self):
        pos = _option_pos()
        bar = _bar(close=400.0)
        price_fn = MagicMock(return_value=5.0)

        with patch("src.backtest.trade_logic.check_option_exit", return_value=None):
            result = check_exit(pos, bar, _exit_config(), get_option_price=price_fn)

        assert result is None

    def test_call_intrabar_stop_loss_fires_at_option_low(self):
        # Option bar low breaches stop threshold → stop fires.
        # entry=5.0, sl=50% → stop fires when option low <= 2.5
        # field="low" returns 2.0 → pnl_pct = (2.0-5.0)/5.0*100 = -60% <= -50%
        pos = _option_pos(direction=1, entry=5.0, current=5.0, option_type="C")
        bar = _bar(close=400.0, high=401.0, low=399.0)

        def price_fn(sym, und, strike, ot, dte, ts, **kwargs):
            if kwargs.get("field") == "low":
                return 2.0   # option bar low triggers stop
            return 5.0       # close and high don't trigger

        result = check_exit(pos, bar, _exit_config(tp=200.0, sl=50.0),
                            get_option_price=price_fn)

        assert result is not None
        assert result.reason == "stop_loss"
        assert result.fill_price == pytest.approx(2.0)

    def test_call_intrabar_profit_target_fires_at_option_high(self):
        # Option bar high breaches target threshold → target fires.
        # entry=5.0, tp=50% → profit fires when option high >= 7.5
        # field="high" returns 8.0 → pnl_pct = (8.0-5.0)/5.0*100 = 60% >= 50%
        pos = _option_pos(direction=1, entry=5.0, current=5.0, option_type="C")
        bar = _bar(close=400.0, high=401.0, low=399.0)

        def price_fn(sym, und, strike, ot, dte, ts, **kwargs):
            if kwargs.get("field") == "high":
                return 8.0   # option bar high triggers profit target
            return 5.0       # close and low don't trigger

        result = check_exit(pos, bar, _exit_config(tp=50.0, sl=200.0),
                            get_option_price=price_fn)

        assert result is not None
        assert result.reason == "profit_target"
        assert result.fill_price == pytest.approx(8.0)

    def test_put_intrabar_stop_loss_fires_at_option_low(self):
        # Put option bar low breaches stop threshold → stop fires.
        # (always long the option — low is worst case for both calls and puts)
        pos = _option_pos(direction=-1, entry=5.0, current=5.0, option_type="P")
        bar = _bar(close=400.0, high=401.0, low=399.0)

        def price_fn(sym, und, strike, ot, dte, ts, **kwargs):
            if kwargs.get("field") == "low":
                return 2.0   # option bar low triggers stop
            return 5.0

        result = check_exit(pos, bar, _exit_config(tp=200.0, sl=50.0),
                            get_option_price=price_fn)

        assert result is not None
        assert result.reason == "stop_loss"
        assert result.fill_price == pytest.approx(2.0)

    def test_option_intrabar_stop_does_not_fire_when_low_above_threshold(self):
        # Option bar low doesn't breach stop threshold → no stop.
        pos = _option_pos(direction=-1, entry=5.0, current=5.0, option_type="P")
        bar = _bar(close=400.0, high=401.0, low=399.0)

        def price_fn(sym, und, strike, ot, dte, ts, **kwargs):
            if kwargs.get("field") == "low":
                return 4.0   # pnl_pct = -20%, doesn't breach 50% stop
            if kwargs.get("field") == "high":
                return 6.0   # pnl_pct = +20%, doesn't breach 200% target
            return 5.0

        with patch("src.backtest.trade_logic.check_option_exit", return_value=None):
            result = check_exit(pos, bar, _exit_config(tp=200.0, sl=50.0),
                                get_option_price=price_fn)

        assert result is None

    def test_put_intrabar_profit_target_fires_at_option_high(self):
        # Put option bar high breaches target threshold → target fires.
        # (always long the option — high is best case for both calls and puts)
        pos = _option_pos(direction=-1, entry=5.0, current=5.0, option_type="P")
        bar = _bar(close=400.0, high=401.0, low=399.0)

        def price_fn(sym, und, strike, ot, dte, ts, **kwargs):
            if kwargs.get("field") == "high":
                return 8.0   # option bar high triggers profit target
            return 5.0

        result = check_exit(pos, bar, _exit_config(tp=50.0, sl=200.0),
                            get_option_price=price_fn)

        assert result is not None
        assert result.reason == "profit_target"
        assert result.fill_price == pytest.approx(8.0)

    def test_option_intrabar_target_does_not_fire_when_high_below_threshold(self):
        # Option bar high doesn't breach target threshold → no target.
        pos = _option_pos(direction=-1, entry=5.0, current=5.0, option_type="P")
        bar = _bar(close=400.0, high=401.0, low=399.0)

        def price_fn(sym, und, strike, ot, dte, ts, **kwargs):
            if kwargs.get("field") == "high":
                return 6.0   # pnl_pct = +20%, doesn't breach 50% target
            if kwargs.get("field") == "low":
                return 4.0   # pnl_pct = -20%, doesn't breach 200% stop
            return 5.0

        with patch("src.backtest.trade_logic.check_option_exit", return_value=None):
            result = check_exit(pos, bar, _exit_config(tp=50.0, sl=200.0),
                                get_option_price=price_fn)

        assert result is None


class TestCheckExitOptionsPriceUpdate:
    def test_current_price_set_by_get_option_price(self):
        pos = _option_pos(current=5.0)
        bar = _bar(close=400.0)
        price_fn = MagicMock(return_value=8.0)

        with patch("src.backtest.trade_logic.check_option_exit", return_value=None):
            check_exit(pos, bar, _exit_config(), get_option_price=price_fn)

        assert pos.current_price == 8.0


# ---------------------------------------------------------------------------
# check_exit — returns None
# ---------------------------------------------------------------------------

class TestCheckExitReturnsNone:
    def test_no_conditions_met(self):
        pos = _equity_pos(direction=1, stop=320.0, limit=480.0)
        bar = _bar(close=400.0, high=410.0, low=390.0, signal=0)
        result = check_exit(pos, bar, _exit_config(opp=False, eod=False))
        assert result is None


# ---------------------------------------------------------------------------
# build_entry — equities
# ---------------------------------------------------------------------------

class TestBuildEntryEquities:
    def test_long_entry_stop_limit_math(self):
        bar = _bar(close=400.0)
        ec = _exit_config(tp=20.0, sl=20.0)
        pos = build_entry(1, bar, 10, "equities", {}, ec)

        assert pos is not None
        assert pos.direction == 1
        assert pos.entry_price == 400.0
        assert pos.stop_price == pytest.approx(320.0)   # 400*(1-0.20)
        assert pos.limit_price == pytest.approx(480.0)  # 400*(1+0.20)
        assert pos.contracts == 10
        assert pos.trade_mode == "equities"

    def test_short_entry_stop_limit_math(self):
        bar = _bar(close=400.0)
        ec = _exit_config(tp=20.0, sl=20.0)
        pos = build_entry(-1, bar, 10, "equities", {}, ec)

        assert pos.direction == -1
        assert pos.stop_price == pytest.approx(480.0)   # 400*(1+0.20)
        assert pos.limit_price == pytest.approx(320.0)  # 400*(1-0.20)

    def test_signal_zero_returns_none(self):
        bar = _bar(close=400.0)
        pos = build_entry(0, bar, 10, "equities", {}, _exit_config())
        assert pos is None


# ---------------------------------------------------------------------------
# build_entry — options
# ---------------------------------------------------------------------------

class TestBuildEntryOptions:
    def test_delegates_to_build_option_position(self):
        bar = _bar(close=400.0)
        ec = _exit_config()
        price_fn = MagicMock(return_value=5.0)
        config = {"options": {"sigma": 0.25, "target_dte": 14, "strike_selection": "ATM"}}

        with patch("src.backtest.trade_logic.build_option_position") as mock_bop:
            mock_bop.return_value = _option_pos()
            pos = build_entry(1, bar, 1, "options", config, ec, get_option_price=price_fn)

        assert pos is not None
        mock_bop.assert_called_once_with(
            1, 400.0, pd.Timestamp("2025-01-02 10:00:00", tz="America/New_York"), 1, config,
            get_price_fn=ANY,
        )
        assert pos.direction == 1
        assert pos.entry_price == 5.0


# ---------------------------------------------------------------------------
# build_entry — unknown trade_mode returns None
# ---------------------------------------------------------------------------

class TestBuildEntryUnknownMode:
    def test_unknown_mode_returns_none(self):
        """Unknown trade_mode values return None — engine validates mode upstream."""
        bar = _bar(close=400.0)
        pos = build_entry(1, bar, 10, "unknown", {}, _exit_config())
        assert pos is None


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Bug 2: EOD cutoff time is configurable (not hardcoded to "15:55")
# ---------------------------------------------------------------------------

class TestConfigurableEodCutoff:
    """eod_cutoff_time in ExitConfig controls when EOD close fires."""

    def test_eod_fires_at_cutoff_time(self):
        """Bar at exactly the cutoff time triggers EOD close."""
        pos = _equity_pos(direction=1)
        bar = _bar(hour=15, minute=56)  # 15:56 >= 15:56 cutoff → fires
        cfg = ExitConfig(
            profit_target_pct=20.0, stop_loss_pct=20.0,
            eod_close=True, opposite_signal=False,
            eod_cutoff_time="15:56",
        )
        result = check_exit(pos, bar, cfg)
        assert result is not None
        assert result.reason == "eod_close"

    def test_eod_does_not_fire_before_cutoff_time(self):
        """Bar one minute before the cutoff does not trigger EOD close."""
        pos = _equity_pos(direction=1)
        bar = _bar(hour=15, minute=54)  # 15:54 < 15:55 cutoff → no fire
        cfg = ExitConfig(
            profit_target_pct=20.0, stop_loss_pct=20.0,
            eod_close=True, opposite_signal=False,
            eod_cutoff_time="15:55",
        )
        result = check_exit(pos, bar, cfg)
        assert result is None

    def test_eod_fires_at_exactly_cutoff(self):
        """Bar at exactly 15:55 fires when cutoff is 15:55."""
        pos = _equity_pos(direction=1)
        bar = _bar(hour=15, minute=55)
        cfg = ExitConfig(
            profit_target_pct=20.0, stop_loss_pct=20.0,
            eod_close=True, opposite_signal=False,
            eod_cutoff_time="15:55",
        )
        result = check_exit(pos, bar, cfg)
        assert result is not None
        assert result.reason == "eod_close"

    def test_eod_cutoff_default_is_1555(self):
        """Default eod_cutoff_time is '15:55' — preserves backward-compatible behaviour."""
        cfg = ExitConfig(
            profit_target_pct=20.0, stop_loss_pct=20.0,
            eod_close=True, opposite_signal=False,
        )
        assert cfg.eod_cutoff_time == "15:55"

    def test_custom_cutoff_earlier_than_default(self):
        """A cutoff of 15:45 fires at 15:45 but not at 15:44."""
        pos_fires = _equity_pos(direction=1)
        bar_fires = _bar(hour=15, minute=45)
        cfg = ExitConfig(
            profit_target_pct=20.0, stop_loss_pct=20.0,
            eod_close=True, opposite_signal=False,
            eod_cutoff_time="15:45",
        )
        assert check_exit(pos_fires, bar_fires, cfg).reason == "eod_close"

        pos_no_fire = _equity_pos(direction=1)
        bar_no_fire = _bar(hour=15, minute=44)
        result = check_exit(pos_no_fire, bar_no_fire, cfg)
        assert result is None


class TestBarContextExitConfig:
    def test_bar_context_frozen(self):
        bar = _bar()
        with pytest.raises(AttributeError):
            bar.close = 999.0

    def test_exit_config_frozen(self):
        ec = _exit_config()
        with pytest.raises(AttributeError):
            ec.profit_target_pct = 999.0

    def test_exit_result_frozen(self):
        er = ExitResult("stop_loss", 320.0)
        with pytest.raises(AttributeError):
            er.reason = "other"

    def test_exit_result_equality(self):
        a = ExitResult("stop_loss", 320.0)
        b = ExitResult("stop_loss", 320.0)
        assert a == b

    def test_bar_context_fields(self):
        bar = _bar(close=400.0, high=410.0, low=390.0, signal=1, hour=10, minute=30)
        assert bar.close == 400.0
        assert bar.high == 410.0
        assert bar.low == 390.0
        assert bar.signal == 1
        assert bar.hour == 10
        assert bar.minute == 30


class TestParseCutoffTime:
    """_parse_cutoff_time validates HH:MM format and range."""

    @pytest.mark.parametrize("valid,expected_h,expected_m", [
        ("15:55", 15, 55),
        ("09:30", 9, 30),
        ("00:00", 0, 0),
        ("23:59", 23, 59),
    ])
    def test_valid_cutoff_strings(self, valid, expected_h, expected_m):
        h, m = _parse_cutoff_time(valid)
        assert h == expected_h
        assert m == expected_m

    @pytest.mark.parametrize("bad", [
        "25:00",   # hour out of range
        "12:60",   # minute out of range
        "abc",     # non-numeric
        "15",      # missing colon
        "",        # empty string
        "15:55:00", # too many parts
    ])
    def test_invalid_cutoff_raises_value_error(self, bad):
        with pytest.raises(ValueError, match="Invalid cutoff_time"):
            _parse_cutoff_time(bad)


# ---------------------------------------------------------------------------
# Stale Price Recovery
# ---------------------------------------------------------------------------

class TestPriceStaleRecovery:
    def test_stale_price_resets_on_valid_data(self):
        """Verify that price_is_stale is True on None, but False on valid update."""
        pos = _option_pos(entry=5.0)
        bar = _bar()
        config = _exit_config()

        # 1. Stale data -> price_is_stale = True
        check_exit(pos, bar, config, get_option_price=lambda *a, **k: None)
        assert pos.price_is_stale is True

        # 2. Fresh data -> price_is_stale = False
        check_exit(pos, bar, config, get_option_price=lambda *a, **k: 6.0)
        assert pos.price_is_stale is False
        assert pos.current_price == 6.0

