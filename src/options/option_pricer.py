import logging
import math
from typing import Optional

from src.constants import DEFAULT_RISK_FREE_RATE
from src.options.greeks import _n_cdf, _d1d2

logger = logging.getLogger(__name__)


def black_scholes_price(
    S: float, K: float, T: float, sigma: float, r: float = DEFAULT_RISK_FREE_RATE, option_type: str = "C", q: float = 0.0
) -> float:
    """Black-Scholes option price as fallback when market data is unavailable.

    Parameters
    ----------
    S : underlying price
    K : strike price
    T : time to expiration in years
    sigma : implied volatility (annualized)
    r : risk-free rate
    option_type : "C" for call, "P" for put
    q : dividend yield (annualized)

    Returns
    -------
    float : theoretical option price (per share)
    """
    if S <= 0:
        raise ValueError(f"Underlying price S must be positive, got {S}")
    if K <= 0:
        raise ValueError(f"Strike price K must be positive, got {K}")

    # T <= 0 or sigma <= 0 means we return the intrinsic value (S-K for calls, K-S for puts).
    if T <= 0 or sigma <= 0:
        if option_type == "C":
            return max(S - K, 0.0)
        return max(K - S, 0.0)

    d1, d2 = _d1d2(S, K, T, r, sigma, q)
    if option_type == "C":
        return S * math.exp(-q * T) * _n_cdf(d1) - K * math.exp(-r * T) * _n_cdf(d2)
    return K * math.exp(-r * T) * _n_cdf(-d2) - S * math.exp(-q * T) * _n_cdf(-d1)


def implied_vol(
    market_price: float, S: float, K: float, T: float,
    r: float = DEFAULT_RISK_FREE_RATE, option_type: str = "C", q: float = 0.0,
    lo: float = 0.01, hi: float = 5.0, tol: float = 1e-4, max_iter: int = 100,
) -> Optional[float]:
    """Back-solve for implied volatility via bisection.

    Returns None if market_price is at or below intrinsic value (no real IV exists),
    or if T <= 0, or if bisection fails to bracket.
    """
    if T <= 0:
        logger.warning(
            "implied_vol called with T<=0 (expiry day) — IV is undefined, Greeks are unreliable"
        )
        return None
    intrinsic = max(S - K, 0.0) if option_type == "C" else max(K - S, 0.0)
    if market_price <= intrinsic:
        return None

    lo_price = black_scholes_price(S, K, T, lo, r, option_type, q)
    hi_price = black_scholes_price(S, K, T, hi, r, option_type, q)
    if not (lo_price <= market_price <= hi_price):
        logger.warning(
            "implied_vol: market price %s outside BS price range [%s, %s] "
            "(lo_vol=%s, hi_vol=%s) — cannot back-solve IV",
            market_price, lo_price, hi_price, lo, hi,
        )
        raise ValueError(
            f"implied_vol: market price {market_price} outside BS price range "
            f"[{lo_price}, {hi_price}] — cannot back-solve IV"
        )

    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        price = black_scholes_price(S, K, T, mid, r, option_type, q)
        diff = price - market_price
        if abs(diff) < tol:
            return mid
        if diff > 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2.0
