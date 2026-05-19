"""Cross-validate our Black-Scholes pricing and Greeks against py_vollib.

py_vollib is built on Peter Jäckel's LetsBeRational — an industry-standard
implementation used as a reference in quantitative finance.

Convention alignment (verified empirically):
    - Price: both return per-share option price (no multiplier)
    - Delta: both return standard BS delta (call: 0–1, put: -1–0)
    - Gamma: both return standard BS gamma
    - Theta: both return per-calendar-day theta (÷365 baked in)
    - Vega:  both return per-1%-vol-move vega (÷100 baked in)

Source references:
    - py_vollib analytical Greeks: py_vollib/black_scholes/greeks/analytical.py
    - Our implementation: src/options/greeks.py, src/options/option_pricer.py
"""

import math

import pytest
from py_vollib.black_scholes import black_scholes as vollib_price
from py_vollib.black_scholes.greeks.analytical import (
    delta as vollib_delta,
    gamma as vollib_gamma,
    theta as vollib_theta,
    vega as vollib_vega,
)

from src.options.greeks import compute_greeks
from src.options.option_pricer import black_scholes_price


# --- Test parameter matrix ---

# (S, K, T_years, sigma, r, option_type, vollib_flag)
SCENARIOS = [
    # ATM
    (400, 400, 30 / 365, 0.25, 0.05, "C", "c"),
    (400, 400, 30 / 365, 0.25, 0.05, "P", "p"),
    # ITM call / OTM put
    (410, 400, 30 / 365, 0.25, 0.05, "C", "c"),
    (410, 400, 30 / 365, 0.25, 0.05, "P", "p"),
    # OTM call / ITM put
    (390, 400, 30 / 365, 0.25, 0.05, "C", "c"),
    (390, 400, 30 / 365, 0.25, 0.05, "P", "p"),
    # Short DTE (1 day — near 0-DTE)
    (400, 400, 1 / 365, 0.25, 0.05, "C", "c"),
    (400, 400, 1 / 365, 0.25, 0.05, "P", "p"),
    # Medium DTE
    (400, 400, 90 / 365, 0.25, 0.05, "C", "c"),
    (400, 400, 90 / 365, 0.25, 0.05, "P", "p"),
    # Low vol
    (400, 400, 30 / 365, 0.15, 0.05, "C", "c"),
    (400, 400, 30 / 365, 0.15, 0.05, "P", "p"),
    # High vol
    (400, 400, 30 / 365, 0.50, 0.05, "C", "c"),
    (400, 400, 30 / 365, 0.50, 0.05, "P", "p"),
    # Deep ITM call
    (450, 400, 30 / 365, 0.25, 0.05, "C", "c"),
    # Deep OTM call
    (350, 400, 30 / 365, 0.25, 0.05, "C", "c"),
    # Deep ITM put
    (350, 400, 30 / 365, 0.25, 0.05, "P", "p"),
    # Deep OTM put
    (450, 400, 30 / 365, 0.25, 0.05, "P", "p"),
    # Very short T (1 hour)
    (400, 400, 1 / 8760, 0.25, 0.05, "C", "c"),
    (400, 400, 1 / 8760, 0.25, 0.05, "P", "p"),
    # Slightly ITM/OTM near expiry
    (401, 400, 1 / 365, 0.25, 0.05, "C", "c"),
    (399, 400, 1 / 365, 0.25, 0.05, "P", "p"),
]


def _scenario_id(params):
    S, K, T, sigma, r, opt, _ = params
    dte = T * 365
    moneyness = "ATM" if S == K else ("ITM" if (S > K and opt == "C") or (S < K and opt == "P") else "OTM")
    return f"{opt}_{moneyness}_S{S}_K{K}_{dte:.1f}d_vol{sigma}"


class TestPriceVsVollib:
    """Verify our BS price matches py_vollib."""

    @pytest.mark.parametrize("params", SCENARIOS, ids=[_scenario_id(p) for p in SCENARIOS])
    def test_price_matches(self, params):
        S, K, T, sigma, r, opt, flag = params

        our = black_scholes_price(S=S, K=K, T=T, sigma=sigma, r=r, option_type=opt)
        ref = vollib_price(flag, S, K, T, r, sigma)

        assert our == pytest.approx(ref, abs=0.005), (
            f"Price mismatch: ours={our:.6f}, vollib={ref:.6f}"
        )


class TestGreeksVsVollib:
    """Verify our Greeks match py_vollib across the full parameter matrix."""

    @pytest.mark.parametrize("params", SCENARIOS, ids=[_scenario_id(p) for p in SCENARIOS])
    def test_delta_matches(self, params):
        S, K, T, sigma, r, opt, flag = params

        ours = compute_greeks(S=S, K=K, T=T, sigma=sigma, r=r, option_type=opt)
        ref = vollib_delta(flag, S, K, T, r, sigma)

        assert ours["delta"] == pytest.approx(ref, abs=0.001), (
            f"Delta mismatch: ours={ours['delta']}, vollib={ref:.6f}"
        )

    @pytest.mark.parametrize("params", SCENARIOS, ids=[_scenario_id(p) for p in SCENARIOS])
    def test_gamma_matches(self, params):
        S, K, T, sigma, r, opt, flag = params

        ours = compute_greeks(S=S, K=K, T=T, sigma=sigma, r=r, option_type=opt)
        ref = vollib_gamma(flag, S, K, T, r, sigma)

        assert ours["gamma"] == pytest.approx(ref, abs=0.0001), (
            f"Gamma mismatch: ours={ours['gamma']}, vollib={ref:.6f}"
        )

    @pytest.mark.parametrize("params", SCENARIOS, ids=[_scenario_id(p) for p in SCENARIOS])
    def test_theta_matches(self, params):
        S, K, T, sigma, r, opt, flag = params

        ours = compute_greeks(S=S, K=K, T=T, sigma=sigma, r=r, option_type=opt)
        ref = vollib_theta(flag, S, K, T, r, sigma)

        # Both return per-calendar-day theta
        assert ours["theta"] == pytest.approx(ref, abs=0.005), (
            f"Theta mismatch: ours={ours['theta']}, vollib={ref:.6f}"
        )

    @pytest.mark.parametrize("params", SCENARIOS, ids=[_scenario_id(p) for p in SCENARIOS])
    def test_vega_matches(self, params):
        S, K, T, sigma, r, opt, flag = params

        ours = compute_greeks(S=S, K=K, T=T, sigma=sigma, r=r, option_type=opt)
        ref = vollib_vega(flag, S, K, T, r, sigma)

        # Both return per-1%-vol-move vega
        assert ours["vega"] == pytest.approx(ref, abs=0.005), (
            f"Vega mismatch: ours={ours['vega']}, vollib={ref:.6f}"
        )

    @pytest.mark.parametrize("params", SCENARIOS, ids=[_scenario_id(p) for p in SCENARIOS])
    def test_price_from_greeks_matches(self, params):
        """Verify the price returned by compute_greeks matches black_scholes_price."""
        S, K, T, sigma, r, opt, flag = params

        greeks = compute_greeks(S=S, K=K, T=T, sigma=sigma, r=r, option_type=opt)
        price = black_scholes_price(S=S, K=K, T=T, sigma=sigma, r=r, option_type=opt)

        assert greeks["price"] == pytest.approx(price, abs=0.005)


class TestEdgeCases:
    """Edge cases where BS implementations commonly diverge."""

    def test_very_deep_itm_call_delta_near_one(self):
        """Deep ITM call should have delta near 1."""
        ours = compute_greeks(S=500, K=300, T=30 / 365, sigma=0.25, option_type="C")
        ref = vollib_delta("c", 500, 300, 30 / 365, 0.05, 0.25)
        assert ours["delta"] == pytest.approx(ref, abs=0.001)
        assert ours["delta"] > 0.99

    def test_very_deep_otm_call_delta_near_zero(self):
        """Deep OTM call should have delta near 0."""
        ours = compute_greeks(S=300, K=500, T=30 / 365, sigma=0.25, option_type="C")
        ref = vollib_delta("c", 300, 500, 30 / 365, 0.05, 0.25)
        assert ours["delta"] == pytest.approx(ref, abs=0.001)
        assert ours["delta"] < 0.01

    def test_put_call_parity_price(self):
        """Put-call parity: C - P = S - K*exp(-rT)."""
        S, K, T, r, sigma = 400, 400, 30 / 365, 0.05, 0.25
        c = black_scholes_price(S=S, K=K, T=T, sigma=sigma, r=r, option_type="C")
        p = black_scholes_price(S=S, K=K, T=T, sigma=sigma, r=r, option_type="P")
        parity = S - K * math.exp(-r * T)
        assert (c - p) == pytest.approx(parity, abs=0.01)

    def test_put_call_parity_delta(self):
        """Delta parity: call_delta - put_delta = 1."""
        S, K, T, r, sigma = 400, 400, 30 / 365, 0.05, 0.25
        gc = compute_greeks(S=S, K=K, T=T, sigma=sigma, r=r, option_type="C")
        gp = compute_greeks(S=S, K=K, T=T, sigma=sigma, r=r, option_type="P")
        assert (gc["delta"] - gp["delta"]) == pytest.approx(1.0, abs=0.01)

    def test_call_put_same_gamma(self):
        """Call and put gamma are identical for same S, K, T, sigma."""
        S, K, T, r, sigma = 400, 400, 30 / 365, 0.05, 0.25
        gc = compute_greeks(S=S, K=K, T=T, sigma=sigma, r=r, option_type="C")
        gp = compute_greeks(S=S, K=K, T=T, sigma=sigma, r=r, option_type="P")
        assert gc["gamma"] == pytest.approx(gp["gamma"], abs=0.0001)

    def test_call_put_same_vega(self):
        """Call and put vega are identical for same S, K, T, sigma."""
        S, K, T, r, sigma = 400, 400, 30 / 365, 0.05, 0.25
        gc = compute_greeks(S=S, K=K, T=T, sigma=sigma, r=r, option_type="C")
        gp = compute_greeks(S=S, K=K, T=T, sigma=sigma, r=r, option_type="P")
        assert gc["vega"] == pytest.approx(gp["vega"], abs=0.001)

    def test_zero_r_symmetry(self):
        """With r=0, ATM call and put should have equal price."""
        S, K, T, sigma = 400, 400, 30 / 365, 0.25
        c = black_scholes_price(S=S, K=K, T=T, sigma=sigma, r=0.0, option_type="C")
        p = black_scholes_price(S=S, K=K, T=T, sigma=sigma, r=0.0, option_type="P")
        assert c == pytest.approx(p, abs=0.01)
