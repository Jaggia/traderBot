"""Shared utility functions for options calculations."""

import pandas as pd

from src.constants import CALENDAR_DAYS_PER_YEAR, SECONDS_PER_DAY


def dte_years(expiry, current_time) -> float:
    """Time to expiration as a fraction of a year. Returns 0.0 if expired.

    Uses total_seconds() for intraday precision — avoids the 0-DTE cliff
    where .days truncates hours and prices options at intrinsic only.

    Includes a 1-minute floor for non-expired options to prevent numerical
    instability in Black-Scholes Greeks as T approaches zero.

    Both arguments are normalized to America/New_York before subtraction
    so tz-naive and tz-aware timestamps can be mixed safely.
    """
    exp = pd.Timestamp(expiry)
    cur = pd.Timestamp(current_time)
    # Normalize: treat tz-naive values as America/New_York, convert tz-aware to ET
    if exp.tz is None:
        exp = exp.tz_localize("America/New_York")
    else:
        exp = exp.tz_convert("America/New_York")
    if cur.tz is None:
        cur = cur.tz_localize("America/New_York")
    else:
        cur = cur.tz_convert("America/New_York")

    seconds = (exp - cur).total_seconds()
    if seconds <= 0:
        return 0.0

    # 1-minute floor (60 seconds) for stability in Greeks
    seconds = max(seconds, 60.0)
    return seconds / (CALENDAR_DAYS_PER_YEAR * SECONDS_PER_DAY)
