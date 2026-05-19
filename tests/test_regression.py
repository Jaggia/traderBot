"""
Regression fixture test for the backtest engine.

Pins a known-good trade log as a CSV. Any code change that silently shifts
results will cause this test to fail, forcing an explicit acknowledgment.

Fixture update workflow:
  After an intentional change, regenerate with:
    REGEN=1 pytest tests/test_regression.py

The test checks os.environ.get("REGEN"): if set, writes the fixture instead
of comparing. On subsequent runs without REGEN, it compares against the fixture.
"""
import os
from pathlib import Path

import pandas as pd
import pytest

from src.backtest.engine import BacktestEngine
from src.backtest.portfolio import Portfolio
from tests.conftest import MockStrategy


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "regression_trade_log.csv"

# Columns used for comparison — excludes timestamps (avoid CSV round-trip tz issues)
_CMP_COLS = [
    "direction", "trade_mode",
    "entry_price", "exit_price",
    "contracts", "pnl", "pnl_pct",
    "exit_reason",
]


# ---------------------------------------------------------------------------
# Helpers (self-contained — no imports from test_engine)
# ---------------------------------------------------------------------------

def _make_bars(n, base_price=400.0, start="2025-01-02 09:30", freq="5min") -> pd.DataFrame:
    """Build flat OHLCV bars at base_price; tight default high/low (±0.1%) stay inside 20% TP/SL."""
    idx = pd.date_range(start, periods=n, freq=freq, tz="America/New_York")
    return pd.DataFrame(
        {
            "open": base_price,
            "high": base_price * 1.001,
            "low": base_price * 0.999,
            "close": base_price,
            "volume": 1_000_000,
        },
        index=idx,
    )


def _run(df: pd.DataFrame, config: dict, signals: pd.Series) -> Portfolio:
    """Inject mock strategy, run engine, return portfolio."""
    strategy = MockStrategy(df, signals)
    engine = BacktestEngine(config=config, equity_data=df, strategy=strategy)
    return engine.run()


# ---------------------------------------------------------------------------
# Scenario
#   30 bars starting 09:30 EST, flat at $400, zero costs, 10 contracts.
#   3 injected signals producing 4 closed trades:
#     Bar  5 → long; bar  7 high=481 → profit_target (pnl=+800)
#     Bar 12 → long; bar 14 low=319  → stop_loss     (pnl=-800)
#     Bar 20 → long; bar 25 signal=-1 → opposite_signal (pnl=0) + short opens
#     Short (from bar 25) closed at backtest_end at $400            (pnl=0)
# ---------------------------------------------------------------------------

def _build_scenario():
    """Construct the 30-bar, 4-trade pinned scenario used by the regression test."""
    df = _make_bars(30)

    # Bar 7: high hits TP for the long entered at bar 5 (limit_px = 400*1.20 = 480)
    df.iloc[7, df.columns.get_loc("high")] = 481.0
    # Bar 14: low hits SL for the long entered at bar 12 (stop_px = 400*0.80 = 320)
    df.iloc[14, df.columns.get_loc("low")] = 319.0

    signals = pd.Series(0, index=df.index)
    signals.iloc[5] = 1    # long → TP exit at bar 7
    signals.iloc[12] = 1   # long → SL exit at bar 14
    signals.iloc[20] = 1   # long → opposite_signal exit at bar 25
    signals.iloc[25] = -1  # triggers opposite_signal + opens short

    config = {
        "strategy": {"trade_mode": "equities", "initial_capital": 100_000},
        "exits": {
            "profit_target_pct": 20.0,
            "stop_loss_pct": 20.0,
            "eod_close": False,
            "opposite_signal": True,
        },
        "position": {
            "sizing_mode": "fixed",
            "contracts_per_trade": 10,
            "max_concurrent_positions": 1,
        },
        "costs": {"commission_per_contract": 0.0, "slippage_pct": 0.0},
    }
    return df, config, signals


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_regression_trade_log():
    """
    Run the pinned scenario and compare exit_reason, prices, and pnl against
    the saved fixture. Set REGEN=1 to regenerate after an intentional change.
    """
    df, config, signals = _build_scenario()
    portfolio = _run(df, config, signals)
    actual = portfolio.get_trade_log()[_CMP_COLS].reset_index(drop=True)

    regen = os.environ.get("REGEN")

    if regen:
        FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
        actual.to_csv(FIXTURE_PATH)
        print(f"\nFixture written to {FIXTURE_PATH}")
        return

    if not FIXTURE_PATH.exists():
        pytest.fail(
            f"Fixture not found at {FIXTURE_PATH}. "
            "Run: REGEN=1 pytest tests/test_regression.py"
        )

    fixture = pd.read_csv(FIXTURE_PATH, index_col=0)

    pd.testing.assert_frame_equal(
        actual,
        fixture.reset_index(drop=True),
        check_dtype=False,
        rtol=1e-4,
    )
