import pandas as pd


def get_market_hours_window(
    bar_time: pd.Timestamp | str,
    open_time: str = "09:30",
    close_time: str = "16:00",
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Get the standard market hours window for a given date.

    Parameters
    ----------
    bar_time : pd.Timestamp or str
        The reference time to derive the market date from.
    open_time : str
        The market open time (HH:MM format, default 09:30).
    close_time : str
        The market close time (HH:MM format, default 16:00).

    Returns
    -------
    (day_start, day_end) : tuple of pd.Timestamp
        Localised timestamps for the market open and close on that date.
    """
    ts = pd.Timestamp(bar_time)
    day_only = ts.normalize()

    open_h, open_m = map(int, open_time.split(":"))
    close_h, close_m = map(int, close_time.split(":"))

    day_start = day_only + pd.Timedelta(hours=open_h, minutes=open_m)
    day_end = day_only + pd.Timedelta(hours=close_h, minutes=close_m)

    if ts.tz is not None:
        day_start = day_start.tz_localize(ts.tz) if day_start.tz is None else day_start.tz_convert(ts.tz)
        day_end = day_end.tz_localize(ts.tz) if day_end.tz is None else day_end.tz_convert(ts.tz)

    return day_start, day_end
