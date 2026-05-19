#!/usr/bin/env python3
"""Standalone Monte Carlo runner for any completed backtest results folder.

Usage:
    python main_runner/run_monte_carlo.py <results_dir> [--n 1000] [--seed 42]

Reads:
    {results_dir}/backtest.csv
    {results_dir}/config.yaml

Outputs to:
    {results_dir}/monte_carlo/
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import yaml

from src.utils.logging_config import setup_logging
from src.analysis.monte_carlo import run_monte_carlo, run_sizing_validation

logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description="Monte Carlo simulation on a backtest results folder.")
    parser.add_argument("results_dir", help="Path to results folder (must contain backtest.csv and config.yaml)")
    parser.add_argument("--n", type=int, default=1000, metavar="N_SIM", help="Number of simulations (default: 1000)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")
    parser.add_argument("--sizing-tolerance", type=float, default=None, metavar="PCT",
        help="Run sizing validation: max P95 drawdown as %% of capital (e.g. 10 = 10%%). Omit to skip.")
    parser.add_argument("--sizing-max-contracts", type=int, default=20, metavar="N",
        help="Upper bound for contract sweep (default: 20).")
    return parser.parse_args()


def main():
    args = parse_args()
    results_dir = args.results_dir

    backtest_csv = os.path.join(results_dir, "backtest.csv")
    config_yaml = os.path.join(results_dir, "config.yaml")

    if not os.path.isfile(backtest_csv):
        logger.error("%s not found.", backtest_csv)
        sys.exit(1)
    if not os.path.isfile(config_yaml):
        logger.error("%s not found.", config_yaml)
        sys.exit(1)

    trade_log = pd.read_csv(backtest_csv)
    if len(trade_log) < 5:
        logger.error("MC requires at least 5 trades; got %d. Exiting.", len(trade_log))
        sys.exit(1)

    with open(config_yaml, "r") as f:
        config = yaml.safe_load(f)

    run_monte_carlo(results_dir, config, n_simulations=args.n, seed=args.seed)

    if args.sizing_tolerance is not None:
        run_sizing_validation(results_dir, config,
            n_simulations=args.n, seed=args.seed,
            sizing_tolerance_pct=args.sizing_tolerance,
            max_contracts=args.sizing_max_contracts)


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
