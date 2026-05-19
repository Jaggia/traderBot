"""
Options module test suite — validates Black-Scholes pricing, Greeks,
strike selection, P&L math, exit logic, and the full entry pipeline.

Uses known analytical values and put-call parity to verify correctness
without needing an external data source.

Run:
    pytest tests/test_options.py -v
"""

import math
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from src.options.greeks import compute_greeks, _n_cdf, _d1d2
from src.options.option_pricer import black_scholes_price
from src.options.strike_selector import (
    build_occ_symbol, get_target_expiry, round_to_strike, select_strike,
)
from src.options.position import Position
from src.options.exit_rules import check_option_exit
from src.options.utils import dte_years


# ═══════════════════════════════════════════════════════════════════════
# 1. Black-Scholes Pricing
# ═══════════════════════════════════════════════════════════════════════

class TestBlackScholesPricing:
    """Validate BS pricing against known analytical values."""

    # Reference values computed with scipy.stats.norm and standard BS formula.
    # S=100, K=100, T=1yr, r=5%, sigma=20%
    # Call ≈ 10.4506, Put ≈ 5.5735 (from Hull's textbook example)
    HULL_S, HULL_K, HULL_T, HULL_R, HULL_SIGMA = 100.0, 100.0, 1.0, 0.05, 0.20
    HULL_CALL = 10.4506
    HULL_PUT = 5.5735

    def test_atm_call_price(self):
        price = black_scholes_price(
            self.HULL_S, self.HULL_K, self.HULL_T, self.HULL_SIGMA, self.HULL_R, "C"
        )
        assert abs(price - self.HULL_CALL) < 0.01, f"Call={price}, expected~{self.HULL_CALL}"

    def test_atm_put_price(self):
        price = black_scholes_price(
            self.HULL_S, self.HULL_K, self.HULL_T, self.HULL_SIGMA, self.HULL_R, "P"
        )
        assert abs(price - self.HULL_PUT) < 0.01, f"Put={price}, expected~{self.HULL_PUT}"

    def test_put_call_parity(self):
        """C - P = S - K*e^(-rT) — fundamental relationship."""
        call = black_scholes_price(100, 105, 0.5, 0.25, 0.05, "C")
        put = black_scholes_price(100, 105, 0.5, 0.25, 0.05, "P")
        parity_rhs = 100 - 105 * math.exp(-0.05 * 0.5)
        assert abs((call - put) - parity_rhs) < 1e-6, "Put-call parity violated"

    @pytest.mark.parametrize("S,K,T,sigma,r", [
        (450, 450, 7/365, 0.25, 0.05),     # SYMBOL ATM 7DTE
        (450, 455, 1/365, 0.30, 0.05),      # SYMBOL OTM 0DTE
        (450, 445, 0/365, 0.25, 0.05),      # expired ITM
        (450, 455, 0/365, 0.25, 0.05),      # expired OTM
    ])
    def test_put_call_parity_various(self, S, K, T, sigma, r):
        """Put-call parity holds across multiple scenarios."""
        call = black_scholes_price(S, K, T, sigma, r, "C")
        put = black_scholes_price(S, K, T, sigma, r, "P")
        if T <= 0:
            # At expiry: C - P = S - K (no discounting)
            assert abs((call - put) - (S - K)) < 1e-6
        else:
            assert abs((call - put) - (S - K * math.exp(-r * T))) < 1e-6

    def test_expired_call_itm(self):
        price = black_scholes_price(450, 445, 0, 0.25, 0.05, "C")
        assert price == 5.0  # intrinsic value

    def test_expired_call_otm(self):
        price = black_scholes_price(450, 455, 0, 0.25, 0.05, "C")
        assert price == 0.0

    def test_expired_put_itm(self):
        price = black_scholes_price(450, 455, 0, 0.25, 0.05, "P")
        assert price == 5.0

    def test_expired_put_otm(self):
        price = black_scholes_price(450, 445, 0, 0.25, 0.05, "P")
        assert price == 0.0

    def test_deep_itm_call_near_intrinsic(self):
        """Deep ITM call with short DTE should be close to intrinsic."""
        price = black_scholes_price(500, 450, 1/365, 0.25, 0.05, "C")
        assert price > 49.9  # intrinsic is 50

    def test_deep_otm_call_near_zero(self):
        """Deep OTM call with short DTE should be near zero."""
        price = black_scholes_price(400, 500, 1/365, 0.25, 0.05, "C")
        assert price < 0.01

    def test_price_matches_greeks_price(self):
        """BS pricer and compute_greeks should return the same price."""
        params = (450, 450, 7/365, 0.25, 0.05)
        for ot in ("C", "P"):
            pricer_price = black_scholes_price(*params, option_type=ot)
            greeks_price = compute_greeks(*params[:4], r=params[4], option_type=ot)["price"]
            assert abs(pricer_price - greeks_price) < 0.01


# ═══════════════════════════════════════════════════════════════════════
# 2. Greeks
# ═══════════════════════════════════════════════════════════════════════

class TestGreeks:
    """Validate Greek values against known properties and bounds."""

    def test_call_delta_bounds(self):
        """Call delta must be in [0, 1]."""
        for K in [400, 450, 500]:
            g = compute_greeks(450, K, 7/365, 0.25, 0.05, "C")
            assert 0.0 <= g["delta"] <= 1.0, f"Call delta={g['delta']} for K={K}"

    def test_put_delta_bounds(self):
        """Put delta must be in [-1, 0]."""
        for K in [400, 450, 500]:
            g = compute_greeks(450, K, 7/365, 0.25, 0.05, "P")
            assert -1.0 <= g["delta"] <= 0.0, f"Put delta={g['delta']} for K={K}"

    def test_call_put_delta_relationship(self):
        """Call delta - Put delta = 1 (for same strike/expiry)."""
        call_g = compute_greeks(450, 450, 30/365, 0.25, 0.05, "C")
        put_g = compute_greeks(450, 450, 30/365, 0.25, 0.05, "P")
        assert abs((call_g["delta"] - put_g["delta"]) - 1.0) < 0.01

    def test_atm_call_delta_near_half(self):
        """ATM call delta should be approximately 0.5."""
        g = compute_greeks(450, 450, 30/365, 0.25, 0.05, "C")
        assert abs(g["delta"] - 0.5) < 0.1

    def test_gamma_always_positive(self):
        """Gamma is always positive for both calls and puts."""
        for ot in ("C", "P"):
            for K in [400, 450, 500]:
                g = compute_greeks(450, K, 7/365, 0.25, 0.05, ot)
                assert g["gamma"] >= 0.0

    def test_gamma_peaks_atm(self):
        """Gamma should be highest for ATM options."""
        gammas = {}
        for K in [440, 445, 450, 455, 460]:
            g = compute_greeks(450, K, 7/365, 0.25, 0.05, "C")
            gammas[K] = g["gamma"]
        assert gammas[450] == max(gammas.values()), f"ATM gamma not highest: {gammas}"

    def test_call_put_gamma_equal(self):
        """Call and put gamma are identical for same strike."""
        call_g = compute_greeks(450, 450, 30/365, 0.25, 0.05, "C")
        put_g = compute_greeks(450, 450, 30/365, 0.25, 0.05, "P")
        assert abs(call_g["gamma"] - put_g["gamma"]) < 1e-6

    def test_theta_negative_for_long(self):
        """Long options lose value over time (theta < 0)."""
        for ot in ("C", "P"):
            g = compute_greeks(450, 450, 30/365, 0.25, 0.05, ot)
            assert g["theta"] < 0, f"{ot} theta={g['theta']} should be negative"

    def test_0dte_theta_larger_than_30dte(self):
        """Theta decay accelerates near expiration."""
        g_30 = compute_greeks(450, 450, 30/365, 0.25, 0.05, "C")
        g_1 = compute_greeks(450, 450, 1/365, 0.25, 0.05, "C")
        assert abs(g_1["theta"]) > abs(g_30["theta"]), "0DTE theta should be larger magnitude"

    def test_vega_positive(self):
        """Vega is positive for long options."""
        for ot in ("C", "P"):
            g = compute_greeks(450, 450, 30/365, 0.25, 0.05, ot)
            assert g["vega"] > 0

    def test_call_put_vega_equal(self):
        """Call and put vega are identical for same strike."""
        call_g = compute_greeks(450, 450, 30/365, 0.25, 0.05, "C")
        put_g = compute_greeks(450, 450, 30/365, 0.25, 0.05, "P")
        assert abs(call_g["vega"] - put_g["vega"]) < 1e-4

    def test_expired_greeks(self):
        """Expired options: intrinsic value, delta 0 or 1, gamma/theta/vega = 0."""
        g = compute_greeks(450, 445, 0, 0.25, 0.05, "C")
        assert g["price"] == 5.0
        assert g["delta"] == 1.0
        assert g["gamma"] == 0.0
        assert g["theta"] == 0.0
        assert g["vega"] == 0.0

    def test_expired_otm_greeks(self):
        g = compute_greeks(450, 460, 0, 0.25, 0.05, "C")
        assert g["price"] == 0.0
        assert g["delta"] == 0.0


# ═══════════════════════════════════════════════════════════════════════
# 3. Strike Selection
# ═══════════════════════════════════════════════════════════════════════

class TestStrikeSelection:
    """Validate strike selection, OCC symbol building, and expiry logic."""

    def test_build_occ_symbol(self):
        sym = build_occ_symbol("SYMBOL", datetime(2026, 2, 21), "C", 451.0)
        assert sym == "SYMBOL   260221C00451000"

    def test_build_occ_symbol_fractional_strike(self):
        sym = build_occ_symbol("SYMBOL", datetime(2026, 3, 20), "P", 450.5)
        assert sym == "SYMBOL   260320P00450500"

    def test_round_to_strike(self):
        assert round_to_strike(450.3) == 450.0
        assert round_to_strike(450.7) == 451.0
        assert round_to_strike(450.5) == 450.0  # banker's rounding

    @pytest.mark.parametrize("target_dte,current,expected_weekday", [
        # 0DTE: same-day (daily expirations) — Friday stays Friday, Wednesday stays Wednesday
        (0, datetime(2026, 3, 20), 4),  # Friday → Friday (same day)
        (0, datetime(2026, 3, 18), 2),  # Wednesday → Wednesday (same day, daily expiry)
        (7, datetime(2026, 3, 13), 4),  # +7 days → Friday 3/20
    ])
    def test_get_target_expiry_is_friday(self, target_dte, current, expected_weekday):
        expiry = get_target_expiry(current, target_dte)
        assert expiry.weekday() == expected_weekday or expiry.weekday() == 3  # Friday or Thursday (holiday roll)

    def test_atm_strike_selection(self):
        config = {"options": {"target_dte": 7, "strike_selection": "ATM", "sigma": 0.25}}
        result = select_strike(450.3, datetime(2026, 3, 13), "C", config)
        assert result["strike"] == 450.0  # rounded to nearest $1

    def test_1_otm_call_is_higher(self):
        """1_OTM call should have strike above ATM."""
        config = {"options": {"target_dte": 7, "strike_selection": "1_OTM", "sigma": 0.25}}
        result = select_strike(450.0, datetime(2026, 3, 13), "C", config)
        assert result["strike"] == 451.0

    def test_1_otm_put_is_lower(self):
        """1_OTM put should have strike below ATM (standard finance)."""
        config = {"options": {"target_dte": 7, "strike_selection": "1_OTM", "sigma": 0.25}}
        result = select_strike(450.0, datetime(2026, 3, 13), "P", config)
        assert result["strike"] == 449.0

    def test_1_itm_call_is_lower(self):
        """1_ITM call should have strike below ATM."""
        config = {"options": {"target_dte": 7, "strike_selection": "1_ITM", "sigma": 0.25}}
        result = select_strike(450.0, datetime(2026, 3, 13), "C", config)
        assert result["strike"] == 449.0

    def test_1_itm_put_is_higher(self):
        """1_ITM put should have strike above ATM (standard finance)."""
        config = {"options": {"target_dte": 7, "strike_selection": "1_ITM", "sigma": 0.25}}
        result = select_strike(450.0, datetime(2026, 3, 13), "P", config)
        assert result["strike"] == 451.0

    def test_2_otm_offset(self):
        config = {"options": {"target_dte": 7, "strike_selection": "2_OTM", "sigma": 0.25}}
        result = select_strike(450.0, datetime(2026, 3, 13), "C", config)
        assert result["strike"] == 452.0

    def test_occ_symbol_in_result(self):
        config = {"options": {"target_dte": 0, "strike_selection": "ATM", "sigma": 0.25}}
        result = select_strike(450.0, datetime(2026, 3, 20), "C", config)
        assert result["raw_symbol"].startswith("SYMBOL")
        assert "C" in result["raw_symbol"]

    def test_target_delta_selection(self):
        """target_delta mode should pick a strike with delta near the target."""
        config = {"options": {"target_dte": 7, "strike_selection": "target_delta",
                              "target_delta": 0.30, "sigma": 0.25}}
        result = select_strike(450.0, datetime(2026, 3, 13), "C", config)
        # Verify the selected strike produces delta near 0.30
        dte = max((result["expiry"].date() - datetime(2026, 3, 13).date()).days, 1) / 365.0
        g = compute_greeks(450.0, result["strike"], dte, 0.25, 0.05, "C")
        assert abs(g["delta"] - 0.30) < 0.05, f"Delta={g['delta']}, wanted ~0.30"


# ═══════════════════════════════════════════════════════════════════════
# 4. Position P&L Math
# ═══════════════════════════════════════════════════════════════════════

class TestPositionPnL:
    """Validate P&L calculations on the Position dataclass."""

    def _make_pos(self, direction=1, entry=2.50, current=3.00, contracts=1,
                  mode="options", **kw):
        pos = Position(
            direction=direction, entry_price=entry, entry_time=datetime.now(),
            contracts=contracts, trade_mode=mode, current_price=current, **kw
        )
        return pos

    def test_long_call_winning(self):
        """Long call: bought at 2.50, now 5.00 → +$250 per contract."""
        pos = self._make_pos(direction=1, entry=2.50, current=5.00, contracts=1)
        assert pos.unrealized_pnl() == 250.0  # (5-2.5)*1*100*1
        assert pos.pnl_pct() == 100.0

    def test_long_call_losing(self):
        """Long call: bought at 2.50, now 1.25 → -$125."""
        pos = self._make_pos(direction=1, entry=2.50, current=1.25, contracts=1)
        assert pos.unrealized_pnl() == -125.0
        assert pos.pnl_pct() == -50.0

    def test_long_put_winning(self):
        """Long put (direction=-1): bought at 3.00, now 5.00 → +$200.

        Options are always long (long call or long put). The direction field tracks
        the underlying signal direction but does NOT flip the P&L sign for options.
        Appreciation is always a gain regardless of call vs put.
        """
        pos = self._make_pos(direction=-1, entry=3.00, current=5.00, contracts=1)
        pnl = pos.unrealized_pnl()
        assert pnl == 200.0  # (5-3) * 1 * 100 = +$200, option appreciated

    def test_multiple_contracts(self):
        """5 contracts at $2.00, now $3.00 → +$500."""
        pos = self._make_pos(direction=1, entry=2.00, current=3.00, contracts=5)
        assert pos.unrealized_pnl() == 500.0  # (3-2)*5*100*1

    def test_equities_no_multiplier(self):
        """Equity positions don't use the 100x multiplier."""
        pos = self._make_pos(direction=1, entry=450.0, current=455.0,
                             contracts=100, mode="equities")
        assert pos.unrealized_pnl() == 500.0  # (455-450)*100*1
        assert abs(pos.pnl_pct() - 1.111) < 0.01

    def test_pnl_pct_zero_entry(self):
        """Zero entry price should return 0% (no division by zero)."""
        pos = self._make_pos(direction=1, entry=0.0, current=1.0)
        assert pos.pnl_pct() == 0.0


# ═══════════════════════════════════════════════════════════════════════
# 5. Exit Rules
# ═══════════════════════════════════════════════════════════════════════

class TestOptionExitRules:
    """Validate check_option_exit priority and logic."""

    def _make_pos(self, entry=2.00, current=2.00, direction=1, expiry=None):
        return Position(
            direction=direction, entry_price=entry, entry_time=datetime.now(),
            contracts=1, trade_mode="options", option_type="C",
            strike=450.0, current_price=current,
            expiry=expiry or datetime.now() + timedelta(days=7),
        )

    def test_stop_loss_triggers(self):
        """Price drops 25% → stop loss at 20%."""
        pos = self._make_pos(entry=2.00, current=1.50)  # -25%
        reason = check_option_exit(pos, 0, pd.Timestamp.now(), 20.0, 20.0, False, True)
        assert reason == "stop_loss"

    def test_profit_target_triggers(self):
        """Price rises 25% → profit target at 20%."""
        pos = self._make_pos(entry=2.00, current=2.50)  # +25%
        reason = check_option_exit(pos, 0, pd.Timestamp.now(), 20.0, 20.0, False, True)
        assert reason == "profit_target"

    def test_stop_beats_profit_when_both(self):
        """If somehow both trigger (impossible in practice), stop wins (checked first)."""
        # This can't happen with real prices, but tests priority
        pos = self._make_pos(entry=2.00, current=1.50)
        reason = check_option_exit(pos, 0, pd.Timestamp.now(), 20.0, 20.0, False, True)
        assert reason == "stop_loss"  # stop checked before profit

    def test_opposite_signal_triggers(self):
        """Long call + bearish signal → opposite_signal."""
        pos = self._make_pos(entry=2.00, current=2.00, direction=1)
        reason = check_option_exit(pos, -1, pd.Timestamp.now(), 20.0, 20.0, False, True)
        assert reason == "opposite_signal"

    def test_same_signal_no_exit(self):
        """Long call + bullish signal → no exit."""
        pos = self._make_pos(entry=2.00, current=2.00, direction=1)
        reason = check_option_exit(pos, 1, pd.Timestamp.now(), 20.0, 20.0, False, True)
        assert reason is None

    def test_opposite_signal_disabled(self):
        """opposite_signal=False → doesn't trigger even on reverse signal."""
        pos = self._make_pos(entry=2.00, current=2.00, direction=1)
        reason = check_option_exit(pos, -1, pd.Timestamp.now(), 20.0, 20.0, False, False)
        assert reason is None

    def test_eod_close_at_1555(self):
        """EOD close at 15:55."""
        pos = self._make_pos(entry=2.00, current=2.00)
        ts = pd.Timestamp("2026-03-20 15:55:00", tz="US/Eastern")
        reason = check_option_exit(pos, 0, ts, 20.0, 20.0, True, True, eod_cutoff_time="15:55")
        assert reason == "eod_close"

    def test_eod_close_disabled(self):
        pos = self._make_pos(entry=2.00, current=2.00)
        ts = pd.Timestamp("2026-03-20 15:55:00", tz="US/Eastern")
        reason = check_option_exit(pos, 0, ts, 20.0, 20.0, False, True, eod_cutoff_time="15:55")
        assert reason is None  # eod_close=False

    def test_expiration_triggers_on_expiry_day_at_cutoff(self):
        """Expiration fires on the day of expiry at the cutoff time."""
        expiry = datetime(2026, 3, 20)
        pos = self._make_pos(entry=2.00, current=2.00, expiry=expiry)
        ts = pd.Timestamp("2026-03-20 15:55:00")
        reason = check_option_exit(pos, 0, ts, 20.0, 20.0, False, True, eod_cutoff_time="15:55")
        assert reason == "expiration"

    def test_expiration_triggers_day_after(self):
        """Expiration fires anytime the day after expiry."""
        expiry = datetime(2026, 3, 20)
        pos = self._make_pos(entry=2.00, current=2.00, expiry=expiry)
        ts = pd.Timestamp("2026-03-21 09:30:00")
        reason = check_option_exit(pos, 0, ts, 20.0, 20.0, False, True)
        assert reason == "expiration"

    def test_expiration_does_not_trigger_before_cutoff_on_expiry_day(self):
        """0-DTE: position remains open until the EOD cutoff on the day of expiry."""
        expiry = datetime(2026, 3, 20)
        pos = self._make_pos(entry=2.00, current=2.00, expiry=expiry)
        ts = pd.Timestamp("2026-03-20 10:00:00")
        reason = check_option_exit(pos, 0, ts, 20.0, 20.0, False, True, eod_cutoff_time="15:55")
        assert reason is None

    def test_no_exit_when_flat(self):
        """No exit when everything is neutral."""
        pos = self._make_pos(entry=2.00, current=2.10)  # +5%, under threshold
        ts = pd.Timestamp("2026-03-18 10:00:00")
        reason = check_option_exit(pos, 0, ts, 20.0, 20.0, False, True)
        assert reason is None

    def test_exit_priority_stop_before_opposite(self):
        """Stop loss fires even when opposite signal is also present."""
        pos = self._make_pos(entry=2.00, current=1.50, direction=1)
        reason = check_option_exit(pos, -1, pd.Timestamp.now(), 20.0, 20.0, False, True)
        assert reason == "stop_loss"


# ═══════════════════════════════════════════════════════════════════════
# 6. Utility Functions
# ═══════════════════════════════════════════════════════════════════════

class TestUtils:
    def test_dte_years_7_days(self):
        result = dte_years(datetime(2026, 3, 27), datetime(2026, 3, 20))
        assert abs(result - 7/365) < 1e-6

    def test_dte_years_expired(self):
        result = dte_years(datetime(2026, 3, 18), datetime(2026, 3, 20))
        assert result == 0.0

    def test_dte_years_same_day(self):
        result = dte_years(datetime(2026, 3, 20), datetime(2026, 3, 20))
        assert result == 0.0

    def test_dte_years_intraday_precision(self):
        """Intraday time must not be truncated — this is the 0-DTE cliff fix."""
        expiry = datetime(2026, 3, 21, 16, 0)   # tomorrow 4 PM
        now = datetime(2026, 3, 20, 15, 0)       # today 3 PM (25 hours away)
        result = dte_years(expiry, now)
        expected = 25 * 3600 / (365.0 * 86400.0)  # 25 hours in year-fraction
        assert result == pytest.approx(expected, rel=1e-6)
        assert result > 0, "Must not be zero — options still have time value"

    def test_dte_years_hours_remaining(self):
        """Same-day expiry with hours left must return nonzero T."""
        expiry = datetime(2026, 3, 20, 16, 0)   # 4 PM
        now = datetime(2026, 3, 20, 9, 30)       # 9:30 AM (6.5 hours)
        result = dte_years(expiry, now)
        assert result > 0, "Same-day expiry with hours left must not be zero"
        expected = 6.5 * 3600 / (365.0 * 86400.0)
        assert result == pytest.approx(expected, rel=1e-6)


# ═══════════════════════════════════════════════════════════════════════
# 7. Greeks Numerical Verification (finite difference)
# ═══════════════════════════════════════════════════════════════════════

class TestGreeksFiniteDifference:
    """Cross-check analytical Greeks against finite-difference approximations."""

    S, K, T, SIGMA, R = 450.0, 450.0, 30/365, 0.25, 0.05
    H = 0.01  # bump size

    def test_delta_finite_diff(self):
        """Delta ≈ (Price(S+h) - Price(S-h)) / (2h)."""
        for ot in ("C", "P"):
            p_up = black_scholes_price(self.S + self.H, self.K, self.T, self.SIGMA, self.R, ot)
            p_dn = black_scholes_price(self.S - self.H, self.K, self.T, self.SIGMA, self.R, ot)
            fd_delta = (p_up - p_dn) / (2 * self.H)
            g = compute_greeks(self.S, self.K, self.T, self.SIGMA, self.R, ot)
            assert abs(fd_delta - g["delta"]) < 1e-3, f"{ot} delta: fd={fd_delta}, analytical={g['delta']}"

    def test_gamma_finite_diff(self):
        """Gamma ≈ (Price(S+h) - 2*Price(S) + Price(S-h)) / h²."""
        for ot in ("C", "P"):
            p_up = black_scholes_price(self.S + self.H, self.K, self.T, self.SIGMA, self.R, ot)
            p_mid = black_scholes_price(self.S, self.K, self.T, self.SIGMA, self.R, ot)
            p_dn = black_scholes_price(self.S - self.H, self.K, self.T, self.SIGMA, self.R, ot)
            fd_gamma = (p_up - 2 * p_mid + p_dn) / (self.H ** 2)
            g = compute_greeks(self.S, self.K, self.T, self.SIGMA, self.R, ot)
            assert abs(fd_gamma - g["gamma"]) < 1e-3, f"{ot} gamma: fd={fd_gamma}, analytical={g['gamma']}"

    def test_theta_finite_diff(self):
        """Theta ≈ (Price(T-dt) - Price(T)) / dt, expressed per day."""
        dt = 1 / 365  # 1 day
        for ot in ("C", "P"):
            p_now = black_scholes_price(self.S, self.K, self.T, self.SIGMA, self.R, ot)
            p_later = black_scholes_price(self.S, self.K, self.T - dt, self.SIGMA, self.R, ot)
            fd_theta = (p_later - p_now) / 1  # per day (dt = 1/365 year, 1 day)
            g = compute_greeks(self.S, self.K, self.T, self.SIGMA, self.R, ot)
            assert abs(fd_theta - g["theta"]) < 0.02, f"{ot} theta: fd={fd_theta}, analytical={g['theta']}"

    def test_vega_finite_diff(self):
        """Vega ≈ (Price(sigma+0.01) - Price(sigma-0.01)) / 2, per 1% vol move."""
        for ot in ("C", "P"):
            p_up = black_scholes_price(self.S, self.K, self.T, self.SIGMA + 0.01, self.R, ot)
            p_dn = black_scholes_price(self.S, self.K, self.T, self.SIGMA - 0.01, self.R, ot)
            fd_vega = (p_up - p_dn) / 2  # per 1% vol move
            g = compute_greeks(self.S, self.K, self.T, self.SIGMA, self.R, ot)
            assert abs(fd_vega - g["vega"]) < 0.01, f"{ot} vega: fd={fd_vega}, analytical={g['vega']}"
