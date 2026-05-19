"""Live trading runner — IBKR data streaming + IBKR paper execution.

Loads historical warmup bars from IBKR (5-min bars via reqHistoricalData),
then streams real-time 1-min bars from IB Gateway, aggregates them to 5-min bars,
runs the existing SMI+WR signal pipeline, and places options orders on the IBKR
paper trading account.

Requirements:
  - IB Gateway (or TWS) running and logged in with paper account credentials
  - API socket enabled: Settings → API → Enable Active X and Socket Clients
  - Socket port: 4002 (IB Gateway paper) or 7497 (TWS paper)
  - Allow connections from localhost only
  - Read-Only API must be UNCHECKED

Usage:
    python live_runner/run_live_ibkr.py

IBKR connection settings are read from config/strategy_params.yaml under the
'live' key (ibkr_host, ibkr_port, ibkr_streamer_client_id, ibkr_trader_client_id).
No environment variables are required — IBKR uses a local socket connection.
"""

import argparse
import logging
import os
import sys
import datetime
import yaml
import copy

import pandas as pd
from ib_insync import IB, Stock
from zoneinfo import ZoneInfo

# Ensure project root is on the path when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.logging_config import setup_logging
from src.live.ibkr_streamer import IBKRStreamer
from src.live.ibkr_trader import IBKRTrader
from src.live.simulated_trader import SimulatedTrader
from src.live.live_engine import LiveEngine

logger = logging.getLogger(__name__)

_EST = ZoneInfo("America/New_York")


def load_ibkr_warmup(
    host: str,
    port: int,
    client_id: int,
    symbol: str = "SYMBOL",
    duration: str = "5 D",
    warmup_bars: int = 200,
    bars_csv: str | None = None,
    max_retries: int = 3,
    retry_wait: float = 10.0,
) -> pd.DataFrame:
    """Fetch historical 5-min bars from IBKR for indicator warmup.

    Connects to IB Gateway, requests 5-min bars for the given duration,
    and returns a DataFrame with DatetimeIndex (America/New_York) and
    OHLCV columns matching the Databento loader format.

    Retries up to max_retries times if IBKR returns no data (can happen
    pre-market when the historical data service is not yet available).

    Parameters
    ----------
    host, port, client_id : IBKR connection parameters.
    symbol : Ticker to fetch (default "SYMBOL").
    duration : IBKR duration string (e.g. "5 D" = last 5 trading days).
    warmup_bars : Number of most recent bars to return.
    bars_csv : If set, append warmup bars to this CSV path (creating it if needed).
    max_retries : Number of attempts before giving up.
    retry_wait : Seconds to wait between retries.

    Returns
    -------
    pd.DataFrame with OHLCV columns and DatetimeIndex.
    """
    from pathlib import Path
    import time as _time

    bars = None
    for attempt in range(1, max_retries + 1):
        ib = IB()
        try:
            ib.connect(host, port, clientId=client_id)
            contract = Stock(symbol, "SMART", "USD")
            ib.qualifyContracts(contract)

            bars = ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr=duration,
                barSizeSetting="5 mins",
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
                keepUpToDate=False,
            )

            if bars:
                break

            logger.warning(
                "IBKR warmup attempt %d/%d returned no bars — retrying in %.0fs",
                attempt, max_retries, retry_wait,
            )
        finally:
            ib.disconnect()

        if attempt < max_retries:
            _time.sleep(retry_wait)

    if not bars:
        raise RuntimeError(
            f"IBKR returned no 5-min bars for {symbol} after {max_retries} attempts"
        )

    rows = []
    for b in bars:
        ts = pd.Timestamp(b.date)
        if ts.tzinfo is None:
            ts = ts.tz_localize(_EST)
        else:
            ts = ts.tz_convert(_EST)
        rows.append({
            "timestamp": ts,
            "open": float(b.open),
            "high": float(b.high),
            "low": float(b.low),
            "close": float(b.close),
            "volume": int(b.volume),
        })

    df = pd.DataFrame(rows).set_index("timestamp")
    df = df.iloc[-warmup_bars:]
    logger.info(
        "IBKR warmup: %d bars (%s → %s)",
        len(df), df.index[0], df.index[-1],
    )

    # Save warmup bars to the daily bars CSV if path provided.
    # If the file doesn't exist, write the full warmup. If it does exist,
    # merge warmup + existing and rewrite so the file has a complete
    # timeline (warmup history + any bars from earlier sessions).
    if bars_csv:
        path = Path(bars_csv)
        if not path.exists():
            df.to_csv(path)
            logger.info("Saved %d warmup bars to %s", len(df), path)
        else:
            existing = pd.read_csv(path, index_col=0, parse_dates=True)
            combined = pd.concat([df[~df.index.isin(existing.index)], existing])
            combined = combined.sort_index()
            combined.to_csv(path)
            logger.info(
                "Merged %d warmup bars with %d existing bars in %s (%d total)",
                len(df), len(existing), path, len(combined),
            )

    return df


def load_config(path: str = "config/strategy_params.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def main():
    # Allow nested event loop calls (nest_asyncio) so that ib.sleep() works
    # from within callbacks — required for option quote snapshots during
    # signal processing inside the streamer's event loop.
    from ib_insync import util
    util.startLoop()

    parser = argparse.ArgumentParser(description="IBKR live runner")
    parser.add_argument(
        "--sim", action="store_true",
        help="Use SimulatedTrader (Black-Scholes pricing, no real orders)",
    )
    args = parser.parse_args()

    setup_logging(log_dir="results/live")

    config = load_config()

    # --- IBKR connection settings (from config, with sensible defaults) ---
    live_cfg = config.get("live", {})
    ibkr_host         = live_cfg.get("ibkr_host", "127.0.0.1")
    ibkr_port         = live_cfg.get("ibkr_port", 4002)
    streamer_cid      = live_cfg.get("ibkr_streamer_client_id", 1)
    trader_cid        = live_cfg.get("ibkr_trader_client_id", 2)
    warmup_bars       = live_cfg.get("warmup_bars", 200)

    logger.info(
        "IBKR config: host=%s port=%s streamer_cid=%s trader_cid=%s mode=%s",
        ibkr_host, ibkr_port, streamer_cid, trader_cid,
        "SIMULATED" if args.sim else "LIVE",
    )

    # --- Load warmup bars from IBKR historical data ---
    # Uses a temporary clientId (99) to avoid conflicting with streamer/trader.
    # Warmup bars are saved to the daily 5-min bars CSV so the full day is in one file.
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    daily_dir = Path("results/live") / today_str
    daily_dir.mkdir(parents=True, exist_ok=True)
    bars_csv = daily_dir / "live_bars_5m.csv"
    logger.info("Loading warmup data from IBKR...")
    df_warmup = load_ibkr_warmup(
        host=ibkr_host, port=ibkr_port, client_id=99,
        warmup_bars=warmup_bars, bars_csv=bars_csv,
    )

    # --- Initialise trader ---
    if args.sim:
        sigma = config.get("options", {}).get("sigma", 0.20)
        trader = SimulatedTrader(sigma=sigma)
    else:
        trader = IBKRTrader(host=ibkr_host, port=ibkr_port, client_id=trader_cid)

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

    # --- Streamer (uses a different clientId) ---
    eod_cutoff_time = config.get("backtest", {}).get("eod_cutoff_time", "15:55")
    warmup_end_ts = df_warmup.index[-1] if len(df_warmup) > 0 else None
    streamer = IBKRStreamer(
        on_bar_close=engine.on_bar,
        on_1min_bar=engine.on_1min_bar,
        host=ibkr_host,
        port=ibkr_port,
        client_id=streamer_cid,
        eod_cutoff_time=eod_cutoff_time,
        warmup_end_ts=warmup_end_ts,
    )

    # --- Stream (blocks until Ctrl+C or fatal error) ---
    logger.info("Starting IBKR live stream. Press Ctrl+C to stop.")
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
