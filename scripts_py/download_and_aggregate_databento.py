#!/usr/bin/env python3
"""Download Databento 1-min equity bars and aggregate to 5-min monthly CSVs.

Usage:
    python scripts_py/download_and_aggregate_databento.py [start] [end]
    python scripts_py/download_and_aggregate_databento.py 2018-05-01 2026-02-14

Downloads 1-min OHLCV bars from Databento XNAS.ITCH via API, organizes by year,
aggregates to 5-min, and saves as monthly CSVs matching Alpaca naming convention.

Requires: DATA_BENTO_PW environment variable

Output:
    data/DataBento/equities/SYMBOL/1min/YYYY/SYMBOL_1min_YYYYMM.csv  (1-min cache)
    data/DataBento/equities/SYMBOL/5min/YYYY/SYMBOL_5min_YYYYMM.csv  (5-min aggregated)
"""
import logging
import sys
import os

import pandas as pd

# Get project root (parent of scripts_py/ directory)
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
sys.path.insert(0, project_root)
os.chdir(project_root)  # Ensure working directory is project root

from src.utils.logging_config import setup_logging
from src.data.databento_loader import download_databento_equities, load_1m_csv, aggregate_and_save_monthly

logger = logging.getLogger(__name__)

OUTPUT_DIR = "data/DataBento/equities/SYMBOL/5min"
CACHE_DIR = "data/DataBento/equities/SYMBOL/1min"


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else "2018-05-01"
    end = sys.argv[2] if len(sys.argv) > 2 else "2026-02-14"

    # Validate date arguments before making any API calls
    args = sys.argv[1:]
    if len(args) >= 2:
        start, end = args[0], args[1]
        try:
            pd.Timestamp(start)
            pd.Timestamp(end)
        except Exception:
            logger.error("Invalid date arguments: start=%r, end=%r — expected YYYY-MM-DD", start, end)
            sys.exit(1)

    # Step 1: Download 1-min bars from Databento API
    logger.info("Step 1: Download 1-min bars from Databento API")
    csv_path = download_databento_equities(symbol="SYMBOL", start=start, end=end, cache_dir=CACHE_DIR)
    logger.info("Downloaded and saved to: %s", csv_path)

    # Step 2: Load the cached 1-min data directly from the downloaded file
    logger.info("Step 2: Load and aggregate to 5-min")
    df_1m = load_1m_csv(csv_path)
    logger.info("%d 1-min bars loaded (%s to %s)", len(df_1m), start, end)

    # Step 3: Save as monthly CSVs
    logger.info("Step 3: Save monthly CSVs")
    months = df_1m.index.to_period("M").unique()
    saved = 0
    for period in months:
        month_mask = df_1m.index.to_period("M") == period
        aggregate_and_save_monthly(df_1m[month_mask], period.year, period.month, OUTPUT_DIR)
        saved += 1

    logger.info("Done. %d monthly files in %s/", saved, OUTPUT_DIR)
    return 0


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

