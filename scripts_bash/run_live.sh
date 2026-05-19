#!/bin/zsh
source ~/.zshrc

# Live Runner — Databento streaming + Alpaca paper trading
# Run at 6:25 AM PT (= 9:25 AM ET, 5 min before NYSE open)
# Usage: ./scripts_bash/run_live.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$SCRIPT_DIR/.."
VENV_PATH="$REPO/venv_stonks/bin/python"

# Fallback for macOS if Linux venv doesn't exist
if [[ ! -f "$VENV_PATH" ]]; then
    VENV_PATH="python"
fi
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Live Runner — Databento + Alpaca Paper"
echo "  $(date)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

cd "$SCRIPT_DIR/.."
$VENV_PATH live_runner/run_live_db.py
