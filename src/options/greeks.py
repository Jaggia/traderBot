import math
from typing import Dict

from src.constants import CALENDAR_DAYS_PER_YEAR, DEFAULT_RISK_FREE_RATE


def _n_cdf(x: float) -> float:
    """
    Calculate the cumulative distribution function (CDF) of the standard normal distribution.
    
    Uses the error function (erf) to approximate the CDF of a standard normal distribution.
    This is based on the mathematical relationship: CDF(x) = 0.5 * (1 + erf(x / sqrt(2))).
    
    Args:
        x: The value at which to evaluate the standard normal CDF.
    
    Returns:
        The approximate probability that a standard normal random variable is less than or equal to x.
        Returns a value between 0.0 and 1.0.
    
    Examples:
        >>> _n_cdf(0.0)  # CDF at mean
        0.5
        >>> _n_cdf(1.96)  # Approximately 97.5%
        0.975
    """
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _n_pdf(x: float) -> float:
    """
    Calculate the probability density function of the standard normal distribution.
    
    This function computes the value of the standard normal (Gaussian) distribution
    at a given point. It is commonly used in financial mathematics, particularly in
    option pricing models like Black-Scholes.
    
    Args:
        x: A float representing the point at which to evaluate the PDF.
    
    Returns:
        A float representing the probability density at the given point x according
        to the standard normal distribution N(0, 1).
    
    Example:
        >>> _n_pdf(0)  # Peak of the standard normal distribution
        0.3989422804014327
        >>> _n_pdf(1)  # One standard deviation from the mean
        0.2419707245191434
    """
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1d2(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0):
    """
    Calculate d1 and d2 parameters for the Black-Scholes option pricing model.
    
    These parameters are intermediate calculations used in the Black-Scholes formula
    to determine option prices and their Greeks.
    
    Args:
        S (float): Current stock price
        K (float): Strike price of the option
        T (float): Time to expiration (in years)
        r (float): Risk-free interest rate (annualized)
        sigma (float): Volatility of the stock (annualized, standard deviation)
        q (float): Dividend yield (annualized)
    
    Returns:
        tuple: A tuple containing (d1, d2) where:
            d1 (float): First intermediate parameter used in Black-Scholes formula
            d2 (float): Second intermediate parameter, calculated as d1 - sigma * sqrt(T)
    """
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return d1, d2


def compute_greeks(
    S: float, K: float, T: float, sigma: float, r: float = DEFAULT_RISK_FREE_RATE, option_type: str = "C", q: float = 0.0
) -> Dict[str, float]:
    """Compute Black-Scholes Greeks.

    Parameters
    ----------
    S : underlying price
    K : strike price
    T : time to expiration in years (e.g. 7/365)
    sigma : implied volatility (annualized, e.g. 0.25)
    r : risk-free rate (annualized)
    option_type : "C" for call, "P" for put
    q : dividend yield (annualized)

    Returns
    -------
    dict with keys: delta, gamma, theta, vega, price
    """
    if S <= 0:
        raise ValueError(f"Underlying price S must be positive, got {S}")
    if K <= 0:
        raise ValueError(f"Strike price K must be positive, got {K}")

    if T <= 0 or sigma <= 0:
        # Expired or zero vol — return intrinsic value.
        # When S == K at expiry, delta is 0.5 by convention (50/50 on ITM/OTM).
        if option_type == "C":
            intrinsic = max(S - K, 0)
            if S > K:
                delta = 1.0
            elif S < K:
                delta = 0.0
            else:
                delta = 0.5
        else:
            intrinsic = max(K - S, 0)
            if K > S:
                delta = -1.0
            elif K < S:
                delta = 0.0
            else:
                delta = -0.5
        return {"delta": delta, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "price": intrinsic}

    d1, d2 = _d1d2(S, K, T, r, sigma, q)
    sqrt_T = math.sqrt(T)

    gamma = math.exp(-q * T) * _n_pdf(d1) / (S * sigma * sqrt_T)
    vega = S * math.exp(-q * T) * _n_pdf(d1) * sqrt_T / 100.0  # per 1% vol move (divided by 100 — differs from textbook ∂V/∂σ convention)

    if option_type == "C":
        delta = math.exp(-q * T) * _n_cdf(d1)
        price = S * math.exp(-q * T) * _n_cdf(d1) - K * math.exp(-r * T) * _n_cdf(d2)
        theta = (
            -S * math.exp(-q * T) * _n_pdf(d1) * sigma / (2 * sqrt_T)
            + q * S * math.exp(-q * T) * _n_cdf(d1)
            - r * K * math.exp(-r * T) * _n_cdf(d2)
        ) / CALENDAR_DAYS_PER_YEAR
    else:
        delta = math.exp(-q * T) * (_n_cdf(d1) - 1.0)
        price = K * math.exp(-r * T) * _n_cdf(-d2) - S * math.exp(-q * T) * _n_cdf(-d1)
        theta = (
            -S * math.exp(-q * T) * _n_pdf(d1) * sigma / (2 * sqrt_T)
            - q * S * math.exp(-q * T) * _n_cdf(-d1)
            + r * K * math.exp(-r * T) * _n_cdf(-d2)
        ) / CALENDAR_DAYS_PER_YEAR

    return {
        "delta": round(delta, 6),
        "gamma": round(gamma, 6),
        "theta": round(theta, 4),
        "vega": round(vega, 4),
        "price": round(price, 4),
    }
