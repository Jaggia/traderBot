#!/usr/bin/env python3
"""Validate the 1-min → 5-min aggregator against native Alpaca 5-min data.

Loads Alpaca 1-min bars, aggregates to 5-min, then compares against native
Alpaca 5-min bars for the same period. Exits 0 if close prices match within
tolerance, exits 1 otherwise.
"""
import calendar
import logging
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.utils.logging_config import setup_logging
from src.data.alpaca_loader import load_cached_csvs
from src.data.aggregator import aggregate_1m_to_5m

logger = logging.getLogger(__name__)

# Test period — months where we have both 1-min and 5-min data
TEST_START = "2025-08"
TEST_END = "2025-11"
TOLERANCE = 0.01  # max acceptable close price diff in dollars
_year, _month = int(TEST_END.split("-")[0]), int(TEST_END.split("-")[1])
TEST_END_DATE = f"{TEST_END}-{calendar.monthrange(_year, _month)[1]:02d}"


def main():
    logger.info("Loading Alpaca 1-min data (%s to %s)...", TEST_START, TEST_END)
    df_1m = load_cached_csvs(
        base_dir="data/Alpaca/equities/SYMBOL/1min",
        start=f"{TEST_START}-01",
        end=TEST_END_DATE,
    )
    logger.info("  -> %d 1-min bars", len(df_1m))

    logger.info("Aggregating 1-min -> 5-min...")
    df_agg = aggregate_1m_to_5m(df_1m)
    logger.info("  -> %d aggregated 5-min bars", len(df_agg))

    logger.info("Loading native Alpaca 5-min data (%s to %s)...", TEST_START, TEST_END)
    df_5m = load_cached_csvs(
        base_dir="data/Alpaca/equities/SYMBOL/5min",
        start=f"{TEST_START}-01",
        end=TEST_END_DATE,
    )
    logger.info("  -> %d native 5-min bars", len(df_5m))

    # Join on timestamp
    ohlcv = ["open", "high", "low", "close", "volume"]
    df_agg_ohlcv = df_agg[ohlcv].add_suffix("_agg")
    df_5m_ohlcv = df_5m[ohlcv].add_suffix("_native")

    merged = df_agg_ohlcv.join(df_5m_ohlcv, how="inner")
    logger.info("Matched timestamps: %d (agg: %d, native: %d)", len(merged), len(df_agg), len(df_5m))

    if merged.empty:
        logger.error("No matching timestamps found!")
        return 1

    # Compute diffs per column — intentional tabular display
    print("=" * 60)
    print(f"{'Column':<10} {'Match%':>8} {'MaxDiff':>10} {'MeanDiff':>10}")
    print("=" * 60)

    all_ok = True
    for col in ohlcv:
        diff = (merged[f"{col}_agg"] - merged[f"{col}_native"]).abs()
        match_pct = (diff < TOLERANCE).mean() * 100
        max_diff = diff.max()
        mean_diff = diff.mean()
        print(f"{col:<10} {match_pct:>7.2f}% {max_diff:>10.4f} {mean_diff:>10.6f}")

        if col == "close" and max_diff >= TOLERANCE:
            all_ok = False

    print("=" * 60)

    # Show sample mismatches for close — intentional tabular display
    close_diff = (merged["close_agg"] - merged["close_native"]).abs()
    mismatches = merged[close_diff >= TOLERANCE]
    if not mismatches.empty:
        print(f"\nSample close mismatches (>{TOLERANCE}):")
        sample = mismatches.head(10)
        for ts, row in sample.iterrows():
            print(f"  {ts}: agg={row['close_agg']:.4f}  native={row['close_native']:.4f}  "
                  f"diff={abs(row['close_agg'] - row['close_native']):.4f}")

    if all_ok:
        logger.info("PASS: All close prices match within $%s", TOLERANCE)
        return 0
    else:
        logger.error("FAIL: Close price mismatches exceed $%s", TOLERANCE)
        return 1


if __name__ == "__main__":
    setup_logging()
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
        sys.exit(0)
    except Exception as e:
        logger.error("%s", e, exc_info=True)
        sys.exit(1)
