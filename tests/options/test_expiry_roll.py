from datetime import datetime
from src.options.strike_selector import get_target_expiry

def test_good_friday_expiry_roll():
    """Verify that expiry rolls to Thursday when Friday is Good Friday."""
    # Good Friday 2025 is April 18 (Friday)
    # Current date is Monday April 14. 
    # target_dte = 4 lands exactly on Friday April 18.
    monday = datetime(2025, 4, 14)
    expiry = get_target_expiry(monday, 4)
    
    # Should roll back to Thursday April 17
    assert expiry.year == 2025
    assert expiry.month == 4
    assert expiry.day == 17
    assert expiry.weekday() == 3  # Thursday
    assert expiry.hour == 16      # Market close


def test_independence_day_thursday_roll():
    """Verify roll for a mid-week holiday if it somehow lands on Friday."""
    # Independence Day 2025 is Friday July 4.
    monday = datetime(2025, 6, 30)
    # Nearest Friday on or after 2025-06-30 + 4 days is Friday July 4.
    expiry = get_target_expiry(monday, 4)
    
    # Should roll back to Thursday July 3
    assert expiry.year == 2025
    assert expiry.month == 7
    assert expiry.day == 3
    assert expiry.weekday() == 3  # Thursday


def test_0dte_holiday_skip():
    """Verify 0-DTE skips holidays and weekends."""
    # Good Friday 2025 is April 18 (Friday)
    good_friday = datetime(2025, 4, 18)
    expiry = get_target_expiry(good_friday, 0)
    
    # 0-DTE on a holiday should skip to next trading day: Monday April 21
    assert expiry.year == 2025
    assert expiry.month == 4
    assert expiry.day == 21
    assert expiry.weekday() == 0  # Monday


def test_0dte_weekend_skip():
    """Verify 0-DTE skips weekends."""
    saturday = datetime(2025, 4, 19)
    expiry = get_target_expiry(saturday, 0)
    
    # Should skip to Monday April 21
    assert expiry.year == 2025
    assert expiry.month == 4
    assert expiry.day == 21
    assert expiry.weekday() == 0  # Monday
