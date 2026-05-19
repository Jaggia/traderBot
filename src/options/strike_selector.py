from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pandas.tseries.holiday import (
    AbstractHolidayCalendar, GoodFriday, Holiday, nearest_workday,
    USLaborDay, USMartinLutherKingJr, USMemorialDay, USPresidentsDay, USThanksgivingDay,
)
from pandas.tseries.offsets import DateOffset

from src.constants import CALENDAR_DAYS_PER_YEAR, DEFAULT_SIGMA, MINUTES_PER_DAY, OCC_STRIKE_MULTIPLIER
from src.options.greeks import compute_greeks
from src.options.utils import dte_years as _dte_years

_NY_TZ = ZoneInfo("America/New_York")


class _NYSEHolidayCalendar(AbstractHolidayCalendar):
    """NYSE market holidays (excludes Columbus Day and Veterans Day, includes Good Friday)."""
    rules = [
        Holiday("New Year's Day", month=1, day=1, observance=nearest_workday),
        USMartinLutherKingJr,
        USPresidentsDay,
        GoodFriday,
        USMemorialDay,
        Holiday("Juneteenth", month=6, day=19, start_date="2022-01-01", observance=nearest_workday),
        Holiday("Independence Day", month=7, day=4, observance=nearest_workday),
        USLaborDay,
        USThanksgivingDay,
        Holiday("Christmas", month=12, day=25, observance=nearest_workday),
    ]


_NYSE_CAL = _NYSEHolidayCalendar()


def _is_nyse_holiday(dt: datetime) -> bool:
    import pandas as pd
    date = pd.Timestamp(dt.date())
    year = str(date.year)
    holidays = _NYSE_CAL.holidays(f"{year}-01-01", f"{year}-12-31")
    return date in holidays


def build_occ_symbol(underlying: str, expiry: datetime, option_type: str, strike: float) -> str:
    """Construct OCC/OSI option symbol.

    Format: ROOT(6) + YYMMDD + C/P + Strike*1000(8 digits)
    e.g. build_occ_symbol('SYMBOL', datetime(2026,2,21), 'C', 451.0) → 'SYMBOL   260221C00451000'
    """
    root = underlying.ljust(6)
    exp = expiry.strftime("%y%m%d")
    strike_int = int(round(strike * OCC_STRIKE_MULTIPLIER))
    return f"{root}{exp}{option_type}{strike_int:08d}"


def get_target_expiry(current_date: datetime, target_dte: int) -> datetime:
    """Find the expiry date for an option given DTE.

    Always returns a tz-aware datetime in America/New_York.

    For target_dte == 0 (0-DTE / daily expirations): return today, advancing
    past weekends and NYSE holidays to the next valid trading day.

    For target_dte > 0: find the nearest Friday on or after
    current_date + target_dte days.  If that Friday is a NYSE holiday
    (e.g. Good Friday, Independence Day), roll back to Thursday.
    """
    # Strip tz for date arithmetic (timedelta on tz-aware datetimes is fine,
    # but _is_nyse_holiday only needs the date).
    target = current_date.replace(tzinfo=None) + timedelta(days=target_dte)
    _MARKET_CLOSE_HOUR = 16  # 4:00 PM ET — options expire at market close
    if target_dte == 0:
        # 0-DTE: use today's expiry (daily expirations). Advance past weekends/holidays.
        while target.weekday() >= 5 or _is_nyse_holiday(target):
            target += timedelta(days=1)
        return target.replace(hour=_MARKET_CLOSE_HOUR, minute=0, second=0,
                               microsecond=0, tzinfo=_NY_TZ)
    # Non-zero DTE: roll to nearest Friday as before
    days_to_friday = (4 - target.weekday()) % 7
    expiry = target + timedelta(days=days_to_friday)
    if _is_nyse_holiday(expiry):
        expiry = expiry - timedelta(days=1)  # roll back to Thursday
    return expiry.replace(hour=_MARKET_CLOSE_HOUR, minute=0, second=0,
                           microsecond=0, tzinfo=_NY_TZ)


def round_to_strike(price: float, tick: float = 1.0) -> float:
    """Round price to the nearest valid strike increment."""
    return round(price / tick) * tick


def select_strike(
    underlying_price: float,
    current_time: datetime,
    option_type: str,
    config: dict,
    underlying: str = "SYMBOL",
) -> dict:
    """Select an option contract based on config parameters.

    Parameters
    ----------
    underlying_price : current price of the underlying
    current_time : current bar timestamp
    option_type : "C" for call, "P" for put
    config : parsed YAML config dict

    Returns
    -------
    dict with keys: strike, expiry, raw_symbol
    """
    opts = config["options"]
    target_dte = opts.get("target_dte", 7)
    if target_dte < 0:
        raise ValueError(
            f"target_dte must be >= 0, got {target_dte}. "
            "Use 0 for same-day expiry, or a positive integer for future expiry."
        )
    selection = opts.get("strike_selection", "ATM")

    expiry = get_target_expiry(current_time, target_dte)

    # Determine strike based on moneyness selection
    tick = opts.get("strike_interval", 1.0)
    atm_strike = round_to_strike(underlying_price, tick)

    if selection == "target_delta":
        target = opts.get("target_delta", 0.33)
        dte_years = max(_dte_years(expiry, current_time), 1 / (CALENDAR_DAYS_PER_YEAR * MINUTES_PER_DAY))  # 1-minute floor
        sigma = opts.get("sigma", DEFAULT_SIGMA)
        r = opts.get("risk_free_rate", 0.05)
        q = opts.get("dividend_yield", 0.0)
        
        # Search range should cover enough strikes to find the delta target.
        # Default to 50 strikes above/below ATM.
        search_count = opts.get("strike_search_count", 50)
        
        best_strike, best_diff = atm_strike, float("inf")
        for off in range(-search_count, search_count + 1):
            candidate = atm_strike + off * tick
            if candidate <= 0: continue
            g = compute_greeks(S=underlying_price, K=candidate, T=dte_years,
                               sigma=sigma, r=r, q=q, option_type=option_type)
            diff = abs(abs(g["delta"]) - target)
            if diff < best_diff:
                best_diff = diff
                best_strike = candidate
        strike = best_strike
    else:
        offset = 0
        if selection == "1_ITM":
            offset = tick if option_type == "C" else -tick
        elif selection == "1_OTM":
            offset = -tick if option_type == "C" else tick
        elif selection == "2_ITM":
            offset = 2 * tick if option_type == "C" else -2 * tick
        elif selection == "2_OTM":
            offset = -2 * tick if option_type == "C" else 2 * tick
        # ITM means lower strike for calls, higher for puts
        strike = atm_strike - offset

    # Construct OCC symbol directly (no API call needed)
    raw_symbol = build_occ_symbol(underlying, expiry, option_type, strike)

    return {
        "strike": strike,
        "expiry": expiry,
        "raw_symbol": raw_symbol,
    }
