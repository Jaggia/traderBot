import pytest

from src.options.greeks import compute_greeks
from src.options.option_pricer import black_scholes_price


class TestGreeks:
    def test_call_delta_positive(self):
        g = compute_greeks(S=400, K=400, T=7 / 365, sigma=0.25, option_type="C")
        assert 0 < g["delta"] < 1

    def test_put_delta_negative(self):
        g = compute_greeks(S=400, K=400, T=7 / 365, sigma=0.25, option_type="P")
        assert -1 < g["delta"] < 0

    def test_atm_delta_near_50(self):
        g = compute_greeks(S=400, K=400, T=30 / 365, sigma=0.25, option_type="C")
        assert 0.45 < g["delta"] < 0.65

    def test_gamma_positive(self):
        g = compute_greeks(S=400, K=400, T=7 / 365, sigma=0.25, option_type="C")
        assert g["gamma"] > 0

    def test_theta_negative_for_long(self):
        g = compute_greeks(S=400, K=400, T=7 / 365, sigma=0.25, option_type="C")
        assert g["theta"] < 0

    def test_expired_option(self):
        g = compute_greeks(S=400, K=395, T=0, sigma=0.25, option_type="C")
        assert g["price"] == pytest.approx(5.0)
        assert g["delta"] == 1.0

    def test_expired_otm(self):
        g = compute_greeks(S=400, K=405, T=0, sigma=0.25, option_type="C")
        assert g["price"] == 0.0
        assert g["delta"] == 0.0

    def test_price_positive(self):
        g = compute_greeks(S=400, K=400, T=7 / 365, sigma=0.25, option_type="C")
        assert g["price"] > 0


    def test_expired_atm_call_delta_half(self):
        """ATM call at expiration: delta = 0.5 (50/50 boundary convention)."""
        g = compute_greeks(S=400, K=400, T=0, sigma=0.25, option_type="C")
        assert g["price"] == pytest.approx(0.0)
        assert g["delta"] == pytest.approx(0.5)

    def test_expired_atm_put_delta_neg_half(self):
        """ATM put at expiration: delta = -0.5."""
        g = compute_greeks(S=400, K=400, T=0, sigma=0.25, option_type="P")
        assert g["price"] == pytest.approx(0.0)
        assert g["delta"] == pytest.approx(-0.5)

    def test_expired_itm_put(self):
        """ITM put at expiration: price = K - S, delta = -1."""
        g = compute_greeks(S=390, K=400, T=0, sigma=0.25, option_type="P")
        assert g["price"] == pytest.approx(10.0)
        assert g["delta"] == pytest.approx(-1.0)

    def test_expired_otm_put(self):
        """OTM put at expiration: price = 0, delta = 0."""
        g = compute_greeks(S=410, K=400, T=0, sigma=0.25, option_type="P")
        assert g["price"] == pytest.approx(0.0)
        assert g["delta"] == pytest.approx(0.0)

    def test_nonpositive_s_raises(self):
        with pytest.raises(ValueError, match="S must be positive"):
            compute_greeks(S=0, K=400, T=7 / 365, sigma=0.25)

    def test_negative_s_raises(self):
        with pytest.raises(ValueError, match="S must be positive"):
            compute_greeks(S=-10, K=400, T=7 / 365, sigma=0.25)

    def test_nonpositive_k_raises(self):
        with pytest.raises(ValueError, match="K must be positive"):
            compute_greeks(S=400, K=0, T=7 / 365, sigma=0.25)


class TestBlackScholesPricer:
    def test_call_price_positive(self):
        p = black_scholes_price(S=400, K=400, T=7 / 365, sigma=0.25, option_type="C")
        assert p > 0

    def test_put_price_positive(self):
        p = black_scholes_price(S=400, K=400, T=7 / 365, sigma=0.25, option_type="P")
        assert p > 0

    def test_put_call_parity(self):
        """C - P = S - K*exp(-rT) for same strike/expiry."""
        import math
        S, K, T, sigma, r = 400, 400, 30 / 365, 0.25, 0.05
        c = black_scholes_price(S, K, T, sigma, r, "C")
        p = black_scholes_price(S, K, T, sigma, r, "P")
        parity = S - K * math.exp(-r * T)
        assert c - p == pytest.approx(parity, abs=0.01)

    def test_deep_itm_call_near_intrinsic(self):
        p = black_scholes_price(S=400, K=350, T=1 / 365, sigma=0.25, option_type="C")
        assert p == pytest.approx(50, abs=1.0)

    def test_expired_intrinsic(self):
        p = black_scholes_price(S=400, K=395, T=0, sigma=0.25, option_type="C")
        assert p == pytest.approx(5.0)

    def test_nonpositive_s_raises(self):
        with pytest.raises(ValueError, match="S must be positive"):
            black_scholes_price(S=0, K=400, T=7 / 365, sigma=0.25)

    def test_nonpositive_k_raises(self):
        with pytest.raises(ValueError, match="K must be positive"):
            black_scholes_price(S=400, K=0, T=7 / 365, sigma=0.25)
