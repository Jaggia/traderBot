"""Live trading runner — Databento streaming + Alpaca paper execution.

Loads historical warmup bars from the local Databento cache (no credits spent),
then streams real-time 1-min bars from Databento XNAS.ITCH, aggregates them to
5-min bars, runs the configured signal strategy, and places options orders
on the Alpaca paper trading account.

Usage:
    python live_runner/run_live_db.py

Required env vars (already in ~/.zshrc):
    DATA_BENTO_PW   — Databento Live API key
    ALPACA_UN            — Alpaca paper API key
    ALPACA_PW            — Alpaca paper secret key
"""

import logging
import os
import sys
import datetime
import yaml
import copy

# Ensure project root is on the path when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.logging_config import setup_logging
from src.data.databento_loader import load_databento_equities
from src.live.databento_streamer import DatabentoStreamer
from src.live.alpaca_trader import AlpacaTrader
from src.live.live_engine import LiveEngine

logger = logging.getLogger(__name__)


def load_config(path: str = "config/strategy_params.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def main():
    setup_logging()

    config = load_config()

    # --- Credentials from shell environment ---
    db_key     = os.environ.get("DATA_BENTO_PW")
    alpaca_key = os.environ.get("ALPACA_UN")
    alpaca_sec = os.environ.get("ALPACA_PW")

    missing = [k for k, v in {
        "DATA_BENTO_PW": db_key,
        "ALPACA_UN":     alpaca_key,
        "ALPACA_PW":     alpaca_sec,
    }.items() if not v]
    if missing:
        logger.error("Missing env vars: %s", ", ".join(missing))
        sys.exit(1)

    # --- Load warmup bars from local cache (free — no Databento credits) ---
    warmup_bars = config.get("live", {}).get("warmup_bars", 200)
    cache_dir   = config["data"]["databento_equities_dir"]

    end   = datetime.date.today()
    start = end - datetime.timedelta(days=90)

    logger.info("Loading warmup data from cache (%s)...", cache_dir)
    df_warmup = load_databento_equities(
        cache_dir=cache_dir,
        start=str(start),
        end=str(end),
    )
    df_warmup = df_warmup.iloc[-warmup_bars:]
    logger.info(
        "Warmup: %d bars (%s → %s)",
        len(df_warmup), df_warmup.index[0], df_warmup.index[-1],
    )

    # --- Initialise components ---
    trader = AlpacaTrader(alpaca_key, alpaca_sec)

    # --- Prepare multiple strategy configurations ---
    # Strategy 1: Refactored SMI+WR Trigger Chain
    config_smi_wr = copy.deepcopy(config)
    config_smi_wr["strategy"]["signal_system"] = "trigger_chain"
    # Canonical SMI+WR trigger order: SMI then Williams %R
    config_smi_wr["signals"]["triggers"] = [
        {"indicator": "smi"},
        {"indicator": "williams_r"}
    ]

    # Strategy 2: EMA 233
    config_ema = copy.deepcopy(config)
    config_ema["strategy"]["signal_system"] = "ema_233"

    engine = LiveEngine([config_smi_wr, config_ema], df_warmup, trader)

    # Resume tracking any orphaned position from a previous crash
    engine.reconcile_positions()

    eod_cutoff_time = config.get("backtest", {}).get("eod_cutoff_time", "15:55")
    streamer = DatabentoStreamer(
        api_key=db_key,
        on_bar_close=engine.on_bar,
        on_1min_bar=engine.on_1min_bar,
        eod_cutoff_time=eod_cutoff_time,
    )


    # --- Stream (blocks until Ctrl+C) ---
    logger.info("Starting live stream. Press Ctrl+C to stop.")
    try:
        streamer.run()
    except KeyboardInterrupt:
        logger.info("Interrupted — closing any open position...")
        engine.force_close(reason="manual_stop")
    except Exception as e:
        logger.error("Unhandled error in live stream: %s", e)
        engine.force_close(reason="error_stop")
        raise

    # --- EOD summary ---
    closed = engine.get_closed_trades()
    logger.info("Session complete. %d trade(s) closed.", len(closed))
    for t in closed:
        logger.info(
            "  %s %s %.0f exp=%s | entry=%.4f exit=%.4f pnl=$%.2f (%.1f%%) reason=%s",
            t["entry_time"], t["option_type"], t["strike"],
            t["expiry"], t["entry_price"], t["exit_price"],
            t["pnl"], t["pnl_pct"], t["reason"],
        )


if __name__ == "__main__":
    main()
