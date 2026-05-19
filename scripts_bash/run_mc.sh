#!/bin/bash

# Monte Carlo Post-Processor
# Run MC simulation on any existing backtest results folder.
# Usage: ./scripts_bash/run_mc.sh <results_dir> [n_simulations]
#
# Example:
#   ./scripts_bash/run_mc.sh results/db/February-27-2026/equities/5min
#   ./scripts_bash/run_mc.sh results/db/February-27-2026/equities/5min 2000

RESULTS_DIR="${1:?Usage: ./scripts_bash/run_mc.sh <results_dir> [n_simulations]}"
N_SIM="${2:-1000}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$SCRIPT_DIR/.."
VENV_PATH="$REPO/venv_stonks/bin/python"

# Fallback for macOS if Linux venv doesn't exist
if [[ ! -f "$VENV_PATH" ]]; then
    VENV_PATH="python"
fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Monte Carlo Simulation"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Results dir: $RESULTS_DIR"
echo "  Simulations: $N_SIM"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

cd "$SCRIPT_DIR/.."
$VENV_PATH main_runner/run_monte_carlo.py "$RESULTS_DIR" --n "$N_SIM"
