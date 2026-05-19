"""Centralized constants for the stonks backtesting framework.

Import from here instead of scattering magic numbers across modules.
"""

# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------
OPTIONS_MULTIPLIER = 100        # standard equity options contract size (shares per contract)
OCC_STRIKE_MULTIPLIER = 1000    # OCC symbol encoding: strike dollars × 1000 → integer field

# ---------------------------------------------------------------------------
# Time / calendar
# ---------------------------------------------------------------------------
CALENDAR_DAYS_PER_YEAR = 365.0  # used in Black-Scholes theta and DTE conversions
SECONDS_PER_DAY = 86400.0       # seconds in a calendar day
MINUTES_PER_DAY = 1440.0        # minutes in a calendar day

# ---------------------------------------------------------------------------
# Black-Scholes defaults
# ---------------------------------------------------------------------------
DEFAULT_SIGMA = 0.25            # fallback implied volatility when market data is unavailable
DEFAULT_RISK_FREE_RATE = 0.05   # annual risk-free rate used in Greeks and option pricing

# ---------------------------------------------------------------------------
# Williams %R thresholds
# ---------------------------------------------------------------------------
WR_OVERSOLD = -80.0             # WR crossover above this level → long signal candidate
WR_OVERBOUGHT = -20.0           # WR crossunder below this level → short signal candidate
WR_SCALE = -100.0               # formula constant: WR = -100 * (high_roll - close) / range

# ---------------------------------------------------------------------------
# Live / streaming
# ---------------------------------------------------------------------------
STREAMER_STALE_TIMEOUT_S = 120          # seconds without a record before treating stream as stale
STREAMER_RETRY_WAITS = [5, 10, 20, 40, 60]  # back-off delays (seconds) between reconnection attempts
