#!/bin/bash

# Databento Data Backtest Runner (default / primary)
# Edit the START_DATE and END_DATE below, then run: ./scripts_bash/run_backtest_db.sh

# Configuration
START_DATE="2025-11-10"  # ← Edit this date
END_DATE="2026-02-13"    # ← Edit this date
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$SCRIPT_DIR/.."
VENV_PATH="$REPO/venv_stonks/bin/python"

# Fallback for macOS if Linux venv doesn't exist
if [[ ! -f "$VENV_PATH" ]]; then
    VENV_PATH="python"
fi
RUN_MC=false              # ← Set to true to run Monte Carlo after backtest

# Run backtest
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Databento Data Backtest"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Date range: $START_DATE to $END_DATE"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

MC_FLAG=""
[ "$RUN_MC" = "true" ] && MC_FLAG="--mc"

cd "$SCRIPT_DIR/.."
$VENV_PATH main_runner/run_backtest_db.py "$START_DATE" "$END_DATE" $MC_FLAG
