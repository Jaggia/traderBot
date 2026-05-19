"""
Tests for shared options entry/exit logic:
  - src/options/entry_logic.py : build_option_position()
  - src/options/exit_rules.py  : check_option_exit()

check_option_exit is a pure function — no mocks needed.
build_option_position delegates to select_strike / get_price_fn / compute_greeks,
so we patch select_strike and inject a mock get_price_fn.
"""
import math
from datetime import datetime, date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.options.exit_rules import check_option_exit
from src.options.entry_logic import build_option_position
from src.options.position import Position


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_position(
    direction: int = 1,
    entry_price: float = 5.0,
    current_price: float = 5.0,
    option_type: str = "C",
    expiry: datetime = None,
) -> Position:
    """Build a minimal options Position for exit-rule testing."""
    if expiry is None:
        expiry = datetime(2026, 12, 31)
    return Position(
        direction=direction,
        entry_price=entry_price,
        entry_time=datetime(2026, 3, 1, 9, 30),
        contracts=1,
        trade_mode="options",
        option_type=option_type,
        strike=400.0,
        expiry=expiry,
        raw_symbol="SYMBOL   260320C00400000",
        current_price=current_price,
    )


def _minimal_options_config() -> dict:
    """Minimal config dict accepted by build_option_position → select_strike."""
    return {
        "options": {
            "strike_selection": "ATM",
            "target_dte": 7,
            "sigma": 0.25,
        }
    }


# ---------------------------------------------------------------------------
# check_option_exit — pure-function tests (no mocks)
# ---------------------------------------------------------------------------

class TestCheckOptionExitStopLoss:
    """stop_loss triggers when pnl_pct <= -stop_loss_pct."""

    def test_stop_loss_triggered_exactly_at_threshold(self):
        # entry=5.00, current=4.00 → pnl_pct = -20.0
        pos = _make_position(entry_price=5.0, current_price=4.0)
        result = check_option_exit(
            pos=pos,
            signal=0,
            ts=pd.Timestamp("2026-03-10 10:00", tz="America/New_York"),
            profit_target_pct=50.0,
            stop_loss_pct=20.0,
            eod_close=False,
            opposite_signal_enabled=False,
        )
        assert result == "stop_loss"

    def test_stop_loss_triggered_below_threshold(self):
        # entry=5.00, current=3.00 → pnl_pct = -40.0 (worse than -20)
        pos = _make_position(entry_price=5.0, current_price=3.0)
        result = check_option_exit(
            pos=pos,
            signal=0,
            ts=pd.Timestamp("2026-03-10 10:00", tz="America/New_York"),
            profit_target_pct=50.0,
            stop_loss_pct=20.0,
            eod_close=False,
            opposite_signal_enabled=False,
        )
        assert result == "stop_loss"

    def test_stop_loss_not_triggered_when_above_threshold(self):
        # entry=5.00, current=4.50 → pnl_pct = -10.0 (not yet at -20)
        pos = _make_position(entry_price=5.0, current_price=4.50)
        result = check_option_exit(
            pos=pos,
            signal=0,
            ts=pd.Timestamp("2026-03-10 10:00", tz="America/New_York"),
            profit_target_pct=50.0,
            stop_loss_pct=20.0,
            eod_close=False,
            opposite_signal_enabled=False,
        )
        assert result is None


class TestCheckOptionExitProfitTarget:
    """profit_target triggers when pnl_pct >= profit_target_pct."""

    def test_profit_target_triggered_exactly_at_threshold(self):
        # entry=5.00, current=7.50 → pnl_pct = +50.0
        pos = _make_position(entry_price=5.0, current_price=7.50)
        result = check_option_exit(
            pos=pos,
            signal=0,
            ts=pd.Timestamp("2026-03-10 10:00", tz="America/New_York"),
            profit_target_pct=50.0,
            stop_loss_pct=20.0,
            eod_close=False,
            opposite_signal_enabled=False,
        )
        assert result == "profit_target"

    def test_profit_target_triggered_above_threshold(self):
        # entry=5.00, current=10.00 → pnl_pct = +100.0
        pos = _make_position(entry_price=5.0, current_price=10.0)
        result = check_option_exit(
            pos=pos,
            signal=0,
            ts=pd.Timestamp("2026-03-10 10:00", tz="America/New_York"),
            profit_target_pct=50.0,
            stop_loss_pct=20.0,
            eod_close=False,
            opposite_signal_enabled=False,
        )
        assert result == "profit_target"

    def test_profit_target_not_triggered_when_below_threshold(self):
        # entry=5.00, current=6.00 → pnl_pct = +20.0 (below 50)
        pos = _make_position(entry_price=5.0, current_price=6.0)
        result = check_option_exit(
            pos=pos,
            signal=0,
            ts=pd.Timestamp("2026-03-10 10:00", tz="America/New_York"),
            profit_target_pct=50.0,
            stop_loss_pct=20.0,
            eod_close=False,
            opposite_signal_enabled=False,
        )
        assert result is None


class TestCheckOptionExitPriority:
    """stop_loss takes priority over profit_target when both conditions would fire."""

    def test_stop_loss_priority_over_profit_target(self):
        # Use thresholds so that pnl_pct=0.0 satisfies both:
        # stop_loss_pct=0.0  → pnl_pct(0) <= 0  ✓
        # profit_target_pct=0.0 → pnl_pct(0) >= 0 ✓
        # stop_loss check comes first in the function.
        pos = _make_position(entry_price=5.0, current_price=5.0)
        result = check_option_exit(
            pos=pos,
            signal=0,
            ts=pd.Timestamp("2026-03-10 10:00", tz="America/New_York"),
            profit_target_pct=0.0,
            stop_loss_pct=0.0,
            eod_close=False,
            opposite_signal_enabled=False,
        )
        assert result == "stop_loss"


class TestCheckOptionExitOppositeSignal:
    """opposite_signal logic respects the enabled flag and direction mismatch."""

    def test_opposite_signal_enabled_and_reversed_direction(self):
        # pos is long call (direction=+1); new signal is -1 → opposite
        pos = _make_position(direction=1)
        result = check_option_exit(
            pos=pos,
            signal=-1,
            ts=pd.Timestamp("2026-03-10 10:00", tz="America/New_York"),
            profit_target_pct=50.0,
            stop_loss_pct=20.0,
            eod_close=False,
            opposite_signal_enabled=True,
        )
        assert result == "opposite_signal"

    def test_opposite_signal_disabled_does_not_trigger(self):
        # Same setup but opposite_signal_enabled=False → no exit
        pos = _make_position(direction=1)
        result = check_option_exit(
            pos=pos,
            signal=-1,
            ts=pd.Timestamp("2026-03-10 10:00", tz="America/New_York"),
            profit_target_pct=50.0,
            stop_loss_pct=20.0,
            eod_close=False,
            opposite_signal_enabled=False,
        )
        assert result is None

    def test_opposite_signal_same_direction_does_not_trigger(self):
        # pos direction=+1, signal=+1 → same direction, no opposite_signal exit
        pos = _make_position(direction=1)
        result = check_option_exit(
            pos=pos,
            signal=1,
            ts=pd.Timestamp("2026-03-10 10:00", tz="America/New_York"),
            profit_target_pct=50.0,
            stop_loss_pct=20.0,
            eod_close=False,
            opposite_signal_enabled=True,
        )
        assert result is None

    def test_opposite_signal_zero_signal_does_not_trigger(self):
        # signal=0 means no new signal, should not cause opposite_signal exit
        pos = _make_position(direction=1)
        result = check_option_exit(
            pos=pos,
            signal=0,
            ts=pd.Timestamp("2026-03-10 10:00", tz="America/New_York"),
            profit_target_pct=50.0,
            stop_loss_pct=20.0,
            eod_close=False,
            opposite_signal_enabled=True,
        )
        assert result is None


class TestCheckOptionExitEodClose:
    """eod_close triggers at 15:55 when enabled; is skipped when disabled."""

    def test_eod_close_enabled_at_1555(self):
        pos = _make_position()
        result = check_option_exit(
            pos=pos,
            signal=0,
            ts=pd.Timestamp("2026-03-10 15:55", tz="America/New_York"),
            profit_target_pct=50.0,
            stop_loss_pct=20.0,
            eod_close=True,
            opposite_signal_enabled=False,
        )
        assert result == "eod_close"

    def test_eod_close_enabled_at_1559(self):
        # The condition is: hour >= 15 AND minute >= 55
        # 15:59 satisfies both conditions.
        pos = _make_position()
        result = check_option_exit(
            pos=pos,
            signal=0,
            ts=pd.Timestamp("2026-03-10 15:59", tz="America/New_York"),
            profit_target_pct=50.0,
            stop_loss_pct=20.0,
            eod_close=True,
            opposite_signal_enabled=False,
        )
        assert result == "eod_close"

    def test_eod_close_disabled_at_1555(self):
        pos = _make_position()
        result = check_option_exit(
            pos=pos,
            signal=0,
            ts=pd.Timestamp("2026-03-10 15:55", tz="America/New_York"),
            profit_target_pct=50.0,
            stop_loss_pct=20.0,
            eod_close=False,
            opposite_signal_enabled=False,
        )
        assert result is None

    def test_eod_close_enabled_before_1555_does_not_trigger(self):
        pos = _make_position()
        result = check_option_exit(
            pos=pos,
            signal=0,
            ts=pd.Timestamp("2026-03-10 15:54", tz="America/New_York"),
            profit_target_pct=50.0,
            stop_loss_pct=20.0,
            eod_close=True,
            opposite_signal_enabled=False,
        )
        assert result is None


class TestCheckOptionExitExpiration:
    """expiration triggers when ts.date() > pos.expiry.date() (strict).

    Same-day positions are allowed to trade until eod_close fires at 15:55.
    The expiration check is a safety net for positions that survive past their
    expiry date (e.g. eod_close=False or overnight holds).
    """

    def test_expiration_on_expiry_date_does_not_trigger(self):
        """0-DTE: position on expiry day should NOT be closed by expiration check — eod_close handles it."""
        expiry = datetime(2026, 3, 20)
        pos = _make_position(expiry=expiry)
        result = check_option_exit(
            pos=pos,
            signal=0,
            ts=pd.Timestamp("2026-03-20 10:00", tz="America/New_York"),
            profit_target_pct=50.0,
            stop_loss_pct=20.0,
            eod_close=False,
            opposite_signal_enabled=False,
        )
        assert result is None

    def test_expiration_after_expiry_date(self):
        expiry = datetime(2026, 3, 20)
        pos = _make_position(expiry=expiry)
        result = check_option_exit(
            pos=pos,
            signal=0,
            ts=pd.Timestamp("2026-03-21 10:00", tz="America/New_York"),
            profit_target_pct=50.0,
            stop_loss_pct=20.0,
            eod_close=False,
            opposite_signal_enabled=False,
        )
        assert result == "expiration"

    def test_expiration_before_expiry_date_does_not_trigger(self):
        expiry = datetime(2026, 3, 20)
        pos = _make_position(expiry=expiry)
        result = check_option_exit(
            pos=pos,
            signal=0,
            ts=pd.Timestamp("2026-03-19 10:00", tz="America/New_York"),
            profit_target_pct=50.0,
            stop_loss_pct=20.0,
            eod_close=False,
            opposite_signal_enabled=False,
        )
        assert result is None

    def test_no_exit_when_no_conditions_met(self):
        """Returns None when no exit condition is met."""
        pos = _make_position(
            entry_price=5.0,
            current_price=5.10,  # pnl ~+2%, well within bounds
            expiry=datetime(2026, 12, 31),
        )
        result = check_option_exit(
            pos=pos,
            signal=0,
            ts=pd.Timestamp("2026-03-10 10:00", tz="America/New_York"),
            profit_target_pct=50.0,
            stop_loss_pct=20.0,
            eod_close=False,
            opposite_signal_enabled=False,
        )
        assert result is None


class TestCheckOptionExitZeroEntryPrice:
    """Zero entry_price yields pnl_pct=0.0 and does not crash."""

    def test_zero_entry_price_no_crash(self):
        pos = _make_position(entry_price=0.0, current_price=5.0)
        # pnl_pct branch uses entry_price=0 → pnl_pct=0.0
        # stop_loss_pct=20 → 0.0 <= -20 is False; profit_target_pct=50 → 0.0 >= 50 is False
        result = check_option_exit(
            pos=pos,
            signal=0,
            ts=pd.Timestamp("2026-03-10 10:00", tz="America/New_York"),
            profit_target_pct=50.0,
            stop_loss_pct=20.0,
            eod_close=False,
            opposite_signal_enabled=False,
        )
        # No exit triggered; pnl_pct=0.0, no stop/target/eod/expiry hit
        assert result is None


# ---------------------------------------------------------------------------
# build_option_position — mock-injected tests
# ---------------------------------------------------------------------------

# Fake contract returned by select_strike.
# expiry is tz-aware (America/New_York), matching get_target_expiry's return type.
_FAKE_CONTRACT = {
    "strike": 400.0,
    "expiry": datetime(2026, 3, 20, tzinfo=__import__("zoneinfo").ZoneInfo("America/New_York")),
    "raw_symbol": "SYMBOL   260320C00400000",
}

# tz-aware timestamp for use in build_option_position tests
_TS_NAIVE = pd.Timestamp("2026-03-10 10:00", tz="America/New_York")


def _make_get_price_fn(price: float = 5.50) -> MagicMock:
    """Return a MagicMock callable that always returns `price`."""
    fn = MagicMock(return_value=price)
    return fn


class TestBuildOptionPositionOptionType:
    """option_type is 'C' for signal=+1 and 'P' for signal=-1."""

    def test_signal_positive_one_yields_call(self):
        config = _minimal_options_config()
        get_price_fn = _make_get_price_fn(5.50)
        with patch("src.options.entry_logic.select_strike", return_value=_FAKE_CONTRACT):
            pos = build_option_position(
                signal=1,
                close=400.0,
                ts=_TS_NAIVE,
                contracts=1,
                config=config,
                get_price_fn=get_price_fn,
            )
        assert pos.option_type == "C"

    def test_signal_negative_one_yields_put(self):
        config = _minimal_options_config()
        get_price_fn = _make_get_price_fn(5.50)
        with patch("src.options.entry_logic.select_strike", return_value=_FAKE_CONTRACT):
            pos = build_option_position(
                signal=-1,
                close=400.0,
                ts=_TS_NAIVE,
                contracts=1,
                config=config,
                get_price_fn=get_price_fn,
            )
        assert pos.option_type == "P"


class TestBuildOptionPositionEntryPrice:
    """entry_price equals what get_price_fn returns."""

    def test_entry_price_matches_get_price_fn(self):
        config = _minimal_options_config()
        expected_price = 7.25
        get_price_fn = _make_get_price_fn(expected_price)
        with patch("src.options.entry_logic.select_strike", return_value=_FAKE_CONTRACT):
            pos = build_option_position(
                signal=1,
                close=400.0,
                ts=_TS_NAIVE,
                contracts=1,
                config=config,
                get_price_fn=get_price_fn,
            )
        assert pos.entry_price == pytest.approx(expected_price)

    def test_get_price_fn_called_once(self):
        """get_price_fn is invoked exactly once during build."""
        config = _minimal_options_config()
        get_price_fn = _make_get_price_fn(5.50)
        with patch("src.options.entry_logic.select_strike", return_value=_FAKE_CONTRACT):
            build_option_position(
                signal=1,
                close=400.0,
                ts=_TS_NAIVE,
                contracts=1,
                config=config,
                get_price_fn=get_price_fn,
            )
        get_price_fn.assert_called_once()


class TestBuildOptionPositionGreeks:
    """Position Greeks are populated with physically correct values.

    Assertions go beyond mere existence — they validate the sign and magnitude
    that Black-Scholes guarantees for ATM options with positive DTE:
      call delta:  0 < delta <= 1
      put  delta: -1 <= delta < 0
      gamma:       > 0  (always positive for long options)
      theta:       < 0  (time decay always hurts the buyer)
      vega:        > 0  (more vol = more option value)
    """

    def _build_call_pos(self):
        config = _minimal_options_config()
        with patch("src.options.entry_logic.select_strike", return_value=_FAKE_CONTRACT):
            return build_option_position(
                signal=1, close=400.0, ts=_TS_NAIVE, contracts=1,
                config=config, get_price_fn=_make_get_price_fn(5.50),
            )

    def _build_put_pos(self):
        fake_put = {
            "strike": 400.0,
            "expiry": datetime(2026, 3, 20),
            "raw_symbol": "SYMBOL   260320P00400000",
        }
        config = _minimal_options_config()
        with patch("src.options.entry_logic.select_strike", return_value=fake_put):
            return build_option_position(
                signal=-1, close=400.0, ts=_TS_NAIVE, contracts=1,
                config=config, get_price_fn=_make_get_price_fn(5.50),
            )

    def test_greeks_are_populated(self):
        pos = self._build_call_pos()
        for greek in ("delta", "gamma", "theta", "vega"):
            value = getattr(pos, greek)
            assert value is not None, f"{greek} should not be None"
            assert not math.isnan(value), f"{greek} should not be NaN"

    def test_call_delta_bounded(self):
        """ATM call delta is strictly between 0 and 1 (by Black-Scholes definition)."""
        pos = self._build_call_pos()
        assert 0 < pos.delta <= 1.0, f"Call delta {pos.delta} out of (0, 1]"

    def test_put_delta_bounded(self):
        """ATM put delta is strictly between -1 and 0 (by Black-Scholes definition)."""
        pos = self._build_put_pos()
        assert -1.0 <= pos.delta < 0, f"Put delta {pos.delta} out of [-1, 0)"

    def test_gamma_is_positive(self):
        """Gamma is always positive for long options (convexity benefit)."""
        pos = self._build_call_pos()
        assert pos.gamma > 0, f"Expected gamma > 0, got {pos.gamma}"

    def test_theta_is_negative(self):
        """Theta is negative for a long option buyer (time decay costs them)."""
        pos = self._build_call_pos()
        assert pos.theta < 0, f"Expected theta < 0, got {pos.theta}"

    def test_vega_is_positive(self):
        """Vega is positive for long options (higher vol increases option value)."""
        pos = self._build_call_pos()
        assert pos.vega > 0, f"Expected vega > 0, got {pos.vega}"

    def test_atm_call_delta_near_half(self):
        """ATM call delta should be close to 0.5 (standard Black-Scholes result)."""
        pos = self._build_call_pos()
        assert 0.3 < pos.delta < 0.7, f"ATM call delta {pos.delta} unexpectedly far from 0.5"

    def test_atm_put_delta_near_neg_half(self):
        """ATM put delta should be close to -0.5 (standard Black-Scholes result)."""
        pos = self._build_put_pos()
        assert -0.7 < pos.delta < -0.3, f"ATM put delta {pos.delta} unexpectedly far from -0.5"


class TestBuildOptionPositionContracts:
    """contracts value is propagated to Position.contracts."""

    def test_contracts_propagated(self):
        config = _minimal_options_config()
        get_price_fn = _make_get_price_fn(5.50)
        for n_contracts in (1, 3, 10):
            with patch("src.options.entry_logic.select_strike", return_value=_FAKE_CONTRACT):
                pos = build_option_position(
                    signal=1,
                    close=400.0,
                    ts=_TS_NAIVE,
                    contracts=n_contracts,
                    config=config,
                    get_price_fn=get_price_fn,
                )
            assert pos.contracts == n_contracts


class TestBuildOptionPositionTradeMode:
    """trade_mode is set to 'options'."""

    def test_trade_mode_is_options(self):
        config = _minimal_options_config()
        get_price_fn = _make_get_price_fn(5.50)
        with patch("src.options.entry_logic.select_strike", return_value=_FAKE_CONTRACT):
            pos = build_option_position(
                signal=1,
                close=400.0,
                ts=_TS_NAIVE,
                contracts=1,
                config=config,
                get_price_fn=get_price_fn,
            )
        assert pos.trade_mode == "options"


class TestBuildOptionPositionZeroPriceGuard:
    """build_option_position returns None when get_price_fn yields entry_price <= 0."""

    @pytest.mark.parametrize("bad_price", [0.0, -1.0, -0.001])
    def test_returns_none_for_non_positive_price(self, bad_price):
        """Entry is skipped (None returned) when pricing returns zero or negative."""
        config = _minimal_options_config()
        get_price_fn = _make_get_price_fn(bad_price)
        with patch("src.options.entry_logic.select_strike", return_value=_FAKE_CONTRACT):
            result = build_option_position(
                signal=1,
                close=400.0,
                ts=_TS_NAIVE,
                contracts=1,
                config=config,
                get_price_fn=get_price_fn,
            )
        assert result is None

    def test_positive_price_still_builds_position(self):
        """Positive price produces a valid Position — regression guard."""
        config = _minimal_options_config()
        get_price_fn = _make_get_price_fn(0.01)
        with patch("src.options.entry_logic.select_strike", return_value=_FAKE_CONTRACT):
            result = build_option_position(
                signal=1,
                close=400.0,
                ts=_TS_NAIVE,
                contracts=1,
                config=config,
                get_price_fn=get_price_fn,
            )
        assert result is not None
        assert result.entry_price == pytest.approx(0.01)
