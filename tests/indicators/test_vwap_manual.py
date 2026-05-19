import pandas as pd
import pytest
from src.indicators.vwap import compute_vwap

def test_vwap_manual_calculation():
    """Verify VWAP with hand-calculated values for a small 5-bar sequence.
    
    Scenarios covered:
    1. Initial bar of the day
    2. Accumulation within a day
    3. Daily reset at the start of a new day
    4. Zero-volume bar handling (forward-fill within the day)
    """
    # Day 1: 2025-01-02
    # Bar 1: 09:30
    #   H=10, L=8, C=9, Vol=100
    #   Typical Price (TP) = (10+8+9)/3 = 9.0
    #   TP * Vol = 900
    #   CumTPV = 900, CumV = 100
    #   VWAP = 9.0
    #
    # Bar 2: 09:35
    #   H=12, L=10, C=11, Vol=200
    #   TP = (12+10+11)/3 = 11.0
    #   TP * Vol = 2200
    #   CumTPV = 900 + 2200 = 3100
    #   CumV = 100 + 200 = 300
    #   VWAP = 3100 / 300 = 10.3333...

    # Day 2: 2025-01-03 (Reset)
    # Bar 3: 09:30
    #   H=20, L=18, C=19, Vol=100
    #   TP = (20+18+19)/3 = 19.0
    #   TP * Vol = 1900
    #   CumTPV = 1900, CumV = 100
    #   VWAP = 19.0
    #
    # Bar 4: 09:35 (Zero Volume)
    #   H=22, L=20, C=21, Vol=0
    #   TP = (22+20+21)/3 = 21.0
    #   TP * Vol = 0
    #   CumTPV = 1900 + 0 = 1900
    #   CumV = 100 + 0 = 100
    #   VWAP_raw = 1900 / 100 = 19.0
    #
    # Bar 5: 09:40
    #   H=24, L=22, C=23, Vol=100
    #   TP = (24+22+23)/3 = 23.0
    #   TP * Vol = 2300
    #   CumTPV = 1900 + 2300 = 4200
    #   CumV = 100 + 100 = 200
    #   VWAP = 4200 / 200 = 21.0
    
    idx = pd.to_datetime([
        "2025-01-02 09:30", "2025-01-02 09:35",
        "2025-01-03 09:30", "2025-01-03 09:35", "2025-01-03 09:40"
    ]).tz_localize("America/New_York")
    
    df = pd.DataFrame({
        "high": [10.0, 12.0, 20.0, 22.0, 24.0],
        "low":  [8.0, 10.0, 18.0, 20.0, 22.0],
        "close": [9.0, 11.0, 19.0, 21.0, 23.0],
        "volume": [100.0, 200.0, 100.0, 0.0, 100.0]
    }, index=idx)
    
    result = compute_vwap(df)
    
    assert result.iloc[0] == pytest.approx(9.0)
    assert result.iloc[1] == pytest.approx(3100.0 / 300.0)
    assert result.iloc[2] == pytest.approx(19.0)
    assert result.iloc[3] == pytest.approx(19.0)
    assert result.iloc[4] == pytest.approx(4200.0 / 200.0)
