#!/usr/bin/env python3
"""Pre-download Databento options data for signal bars.

Loads equity data, computes indicators + signals, then for each signal bar
downloads the 1-min OHLCV for the specific option contract that would be traded.
Data is cached locally so backtests hit cache only (zero API calls).

Usage:
    python scripts_py/download_options_databento.py 2025-11-10 2026-02-13
"""

import logging
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import yaml

from src.utils.logging_config import setup_logging
from src.data.databento_loader import DatabentoOptionsLoader, load_databento_equities
from src.signals.strategy import create_strategy
from src.options.strike_selector import select_strike, build_occ_symbol

logger = logging.getLogger(__name__)

WARMUP_MONTHS = 3


def load_config(path: str = "config/strategy_params.yaml") -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


def _warmup_start(start_arg: str) -> str:
    dt = pd.Timestamp(start_arg)
    year = dt.year
    month = dt.month - WARMUP_MONTHS
    while month < 1:
        month += 12
        year -= 1
    return f"{year}-{month:02d}"


def main():
    if len(sys.argv) < 3:
        logger.error("Usage: python scripts_py/download_options_databento.py START END")
        logger.error("  e.g. python scripts_py/download_options_databento.py 2025-11-10 2026-02-13")
        sys.exit(1)

    start_arg = sys.argv[1]
    end_arg = sys.argv[2]

    try:
        pd.Timestamp(start_arg)
        pd.Timestamp(end_arg)
    except Exception:
        logger.error(
            "Invalid date arguments: start=%r, end=%r — expected YYYY-MM-DD", start_arg, end_arg
        )
        sys.exit(1)

    config = load_config()
    db_equities_dir = config.get("data", {}).get(
        "databento_equities_dir", "data/DataBento/equities/SYMBOL/5min"
    )

    # --- 1. Load equity data with warm-up ---
    load_start = _warmup_start(start_arg)
    logger.info("Loading Databento equity data (%s to %s)...", load_start, end_arg)
    equity_data = load_databento_equities(db_equities_dir, start=load_start, end=end_arg)

    if end_arg:
        end_ts = pd.Timestamp(end_arg, tz=equity_data.index.tz) + pd.Timedelta(days=1)
        equity_data = equity_data[:end_ts]

    logger.info("Data loaded: %s to %s", equity_data.index[0], equity_data.index[-1])

    # --- 2. Compute indicators + signals ---
    strategy = create_strategy(config)
    logger.info("Computing indicators and signals (signal_system=%s)...",
                config.get("strategy", {}).get("signal_system", "smi_wr"))
    data = strategy.compute_indicators(equity_data, config)
    data["signal"] = strategy.generate_signals(data, config)

    # Trim to trading period (signals in warm-up don't count)
    trade_start = pd.Timestamp(start_arg, tz=data.index.tz)
    trade_data = data[trade_start:]

    signal_bars = trade_data[trade_data["signal"] != 0]
    logger.info("Found %d signal bars in trading period", len(signal_bars))

    if signal_bars.empty:
        logger.info("No signals found — nothing to download.")
        return

    # --- 3. Init options loader ---
    api_key = os.getenv("DATA_BENTO_PW") or os.getenv("DATABENTO_API_KEY")
    if not api_key:
        logger.error("Set DATA_BENTO_PW or DATABENTO_API_KEY env var")
        sys.exit(1)

    opts_dir = config.get("data", {}).get("options_dir", "data/DataBento/options/SYMBOL/1min")
    loader = DatabentoOptionsLoader(api_key=api_key, cache_dir=opts_dir)

    # --- 4. Download option bars for each signal ---
    downloaded = 0
    cache_hits = 0
    errors = 0
    seen_symbols = {}  # symbol -> set of dates already requested

    for ts, row in signal_bars.iterrows():
        signal = int(row["signal"])
        close = row["close"]
        option_type = "C" if signal == 1 else "P"

        contract = select_strike(
            underlying_price=close,
            current_time=ts,
            option_type=option_type,
            config=config,
        )

        raw_symbol = contract["raw_symbol"]
        signal_date = pd.Timestamp(ts).normalize()

        # Skip if we already requested this symbol+date
        if raw_symbol in seen_symbols and signal_date in seen_symbols[raw_symbol]:
            continue
        seen_symbols.setdefault(raw_symbol, set()).add(signal_date)

        # Download full trading day for this contract
        cutoff_h, cutoff_m = map(int, config.get("backtest", {}).get("eod_cutoff_time", "15:55").split(":"))
        day_start = signal_date.replace(hour=9, minute=30)
        day_end = signal_date.replace(hour=cutoff_h, minute=cutoff_m)

        # Ensure tz-aware
        if day_start.tz is None:
            tz = pd.Timestamp(ts).tz
            day_start = day_start.tz_localize(tz)
            day_end = day_end.tz_localize(tz)

        logger.info("Signal: %s at %s, close=$%.2f → %s",
                    "LONG" if signal == 1 else "SHORT", ts, close, raw_symbol.strip())

        # Check cache before calling loader (loader also checks, but we track stats)
        cache_path = loader.get_cache_path(raw_symbol)
        was_cached = os.path.exists(cache_path)

        try:
            loader.load_option_bars(raw_symbol, start=day_start, end=day_end)
            if was_cached:
                cache_hits += 1
            else:
                downloaded += 1
        except Exception as e:
            logger.error("Failed to download %s: %s", raw_symbol, e)
            errors += 1

    # --- 5. Summary ---
    total = len(seen_symbols)
    logger.info("Options Pre-Download Summary")
    logger.info("  Signal bars:       %d", len(signal_bars))
    logger.info("  Unique contracts:  %d", total)
    logger.info("  Downloaded (new):  %d", downloaded)
    logger.info("  Cache hits:        %d", cache_hits)
    if errors:
        logger.warning("  Errors:            %d", errors)
    logger.info("  Cache dir:         %s", opts_dir)


if __name__ == "__main__":
    setup_logging()
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
        sys.exit(0)
    except Exception as e:
        logger.error("%s", e, exc_info=True)
        sys.exit(1)
