#!/bin/bash

# Live Runner — IBKR streaming + SimulatedTrader (paper)
# Loops with a 30s restart delay so Gateway blips don't kill the session.
# Requires IB Gateway running on port 4002 (paper account).
#
# Usage:
#   ./scripts_bash/run_live_ibkr.sh           # sim mode (default, safe)
#   ./scripts_bash/run_live_ibkr.sh --no-sim  # real IBKR orders (when ready)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$SCRIPT_DIR/.."
VENV_PATH="$REPO/venv_stonks/bin/python"

# Fallback for macOS path if Linux venv doesn't exist
if [[ ! -f "$VENV_PATH" ]]; then
    VENV_PATH="python"
fi

SIM_FLAG="--sim"

if [[ "$1" == "--no-sim" ]]; then
    SIM_FLAG=""
fi

cd "$REPO"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Live Runner — IBKR Paper  $SIM_FLAG"
echo "  $(date)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

while true; do
    echo "[$(date)] Starting run_live_ibkr.py $SIM_FLAG"
    $VENV_PATH live_runner/run_live_ibkr.py $SIM_FLAG
    EXIT_CODE=$?
    echo "[$(date)] Process exited (code $EXIT_CODE) — restarting in 30s …"
    sleep 30
done
