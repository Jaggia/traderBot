#!/usr/bin/env python3
"""Abstract base class for backtest runner entry points (Template Method pattern)."""

import logging
import os
import sys
from abc import ABC
from datetime import datetime
from typing import Optional

import pandas as pd
import yaml

from src.backtest.engine import BacktestEngine
from src.data.provider import create_provider
from src.analysis.metrics import (
    compute_metrics,
    compute_buy_hold_benchmark,
    count_trials,
    print_metrics,
    print_benchmark,
    save_report_md,
    save_config_snapshot,
)
from src.analysis.visualize import plot_equity_curve, plot_drawdown, plot_signals_on_price

logger = logging.getLogger(__name__)


def _load_config(path: str = "config/strategy_params.yaml") -> dict:
    # Resolve relative to project root (two levels up from main_runner/)
    if not os.path.isabs(path):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, path)
    with open(path, "r") as f:
        return yaml.safe_load(f)


def _build_run_tag(config: dict) -> str:
    """Build a short human-readable tag from key strategy params."""
    sig_system = config.get("strategy", {}).get("signal_system", "smi_wr")

    if sig_system == "ema_233":
        return _build_ema_run_tag(config)

    return _build_smi_wr_run_tag(config)


def _build_ema_run_tag(config: dict) -> str:
    """Tag for System 2 (EMA 233 intrabar cross)."""
    ema_cfg = config.get("signals_ema", {})
    exits   = config.get("exits", {})
    pos     = config.get("position", {})
    opt     = config.get("options", {})

    period = ema_cfg.get("ema_period", 233)
    offset = ema_cfg.get("entry_offset_cents", 0.02)
    sk     = opt.get("strike_selection", "ATM").replace("_", "")

    tag = f"EMA{period}_OFF{int(offset * 100)}_{sk}"

    pt = exits.get("profit_target_pct", 20.0)
    sl = exits.get("stop_loss_pct", 20.0)
    sz = pos.get("sizing_pct", 50)
    if pt != 20.0:
        tag += f"_PT{int(pt) if pt == int(pt) else pt}"
    if sl != 20.0:
        tag += f"_SL{int(sl) if sl == int(sl) else sl}"
    if sz != 50:
        tag += f"_SZ{int(sz) if sz == int(sz) else sz}"

    return tag


def _build_smi_wr_run_tag(config: dict) -> str:
    """Tag for System 1 (SMI + Williams %R)."""
    sig   = config.get("signals", {})
    opt   = config.get("options", {})
    exits = config.get("exits", {})
    pos   = config.get("position", {})

    pm_map = {
        "indicator_2_then_indicator_1": "WR",
        "indicator_1_then_indicator_2": "SM",
        "either": "EI",
    }
    lf  = pm_map.get(sig.get("pair_mode", "indicator_2_then_indicator_1"), "WR")
    ar  = "1" if sig.get("armed_mode", False) else "0"
    vp  = "1" if sig.get("vwap_filter", False) else "0"
    syn = str(sig.get("sync_window", 20))
    sk  = opt.get("strike_selection", "ATM").replace("_", "")  # "1_OTM" → "1OTM"

    tag = f"{lf}_AR{ar}_VP{vp}_SYN{syn}_{sk}"

    # Append exit/sizing params only when they differ from defaults (20, 20, 50)
    pt = exits.get("profit_target_pct", 20.0)
    sl = exits.get("stop_loss_pct", 20.0)
    sz = pos.get("sizing_pct", 50)
    if pt != 20.0:
        tag += f"_PT{int(pt) if pt == int(pt) else pt}"
    if sl != 20.0:
        tag += f"_SL{int(sl) if sl == int(sl) else sl}"
    if sz != 50:
        tag += f"_SZ{int(sz) if sz == int(sz) else sz}"

    return tag


def _update_run_key(config: dict, tag: str, run_dt: datetime) -> None:
    """Write/update results/run_key.yaml — newest tag first."""
    key_path = "results/run_key.yaml"
    os.makedirs("results", exist_ok=True)

    existing: dict = {}
    if os.path.exists(key_path):
        with open(key_path, "r") as f:
            existing = yaml.safe_load(f) or {}

    sig_system = config.get("strategy", {}).get("signal_system", "smi_wr")
    opt   = config.get("options", {})
    exits = config.get("exits", {})
    pos   = config.get("position", {})

    entry: dict = {
        "first_seen":        existing.get(tag, {}).get("first_seen", run_dt.strftime("%Y-%m-%d %H:%M")),
        "signal_system":     sig_system,
        "strike_selection":  opt.get("strike_selection"),
        "profit_target_pct": exits.get("profit_target_pct"),
        "stop_loss_pct":     exits.get("stop_loss_pct"),
        "sizing_pct":        pos.get("sizing_pct"),
    }

    if sig_system == "ema_233":
        ema_cfg = config.get("signals_ema", {})
        entry["ema_period"] = ema_cfg.get("ema_period", 233)
        entry["entry_offset_cents"] = ema_cfg.get("entry_offset_cents", 0.02)
    else:
        sig = config.get("signals", {})
        entry["pair_mode"]        = sig.get("pair_mode")
        entry["armed_mode"]       = sig.get("armed_mode")
        entry["vwap_filter"]      = sig.get("vwap_filter")
        entry["sync_window"]      = sig.get("sync_window")

    # Rebuild dict with this tag first, preserve order of others
    updated = {tag: entry}
    for k, v in existing.items():
        if k != tag:
            updated[k] = v

    with open(key_path, "w") as f:
        yaml.dump(updated, f, default_flow_style=False, sort_keys=False)


def _validate_date_args():
    """Validate CLI date args; exit with error if malformed (but not if absent)."""
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    start_arg = positional[0] if len(positional) > 0 else None
    end_arg = positional[1] if len(positional) > 1 else None
    for label, val in (("start", start_arg), ("end", end_arg)):
        if val is not None:
            try:
                pd.Timestamp(val)
            except Exception:
                logger.error("Invalid %s date %r — expected YYYY-MM-DD", label, val)
                sys.exit(1)
    return start_arg, end_arg


def _warmup_start(start_arg: str, warmup_months: int) -> str:
    """Shift a YYYY-MM-DD date back by warmup_months for indicator warm-up.

    Uses pd.DateOffset so end-of-month dates are clamped correctly
    (e.g. 2025-05-31 - 3 months → 2025-02-28, not the invalid 2025-02-31).
    """
    warmup_ts = pd.Timestamp(start_arg) - pd.DateOffset(months=warmup_months)
    return warmup_ts.strftime("%Y-%m-%d")


class BaseBacktestRunner(ABC):
    source_name: str
    data_source: Optional[str] = None
    warmup_months: int = 3

    def _config_with_runner_source(self, config: dict) -> dict:
        """Apply the entry-point's data source without mutating shared config."""
        if self.data_source is None:
            return config
        updated = dict(config)
        data_cfg = dict(updated.get("data", {}))
        data_cfg["data_source"] = self.data_source
        updated["data"] = data_cfg
        return updated

    def load_data(
        self,
        config: dict,
        load_start: Optional[str],
        end_arg: Optional[str],
    ) -> pd.DataFrame:
        """Load equity data via the configured data provider.

        Subclasses rarely need to override this — the provider handles
        source-specific loading. Override only for custom behavior.
        """
        provider = create_provider(config)
        return provider.load_equity_data(load_start, end_arg)

    def pre_load_check(
        self,
        config: dict,
        start_arg: Optional[str],
        end_arg: Optional[str],
    ) -> None:
        provider = create_provider(config)
        provider.ensure_data(start_arg, end_arg, self.warmup_months)

    def trim_end_date(
        self, equity_data: pd.DataFrame, end_arg: Optional[str], *, config: dict = None,
    ) -> pd.DataFrame:
        """Trim data to end_arg (inclusive).

        Uses the provider's ``should_trim_end()`` to decide whether trimming
        is needed (TradingView skips it because its loader already filters).
        """
        if not end_arg:
            return equity_data
        if config is not None:
            provider = create_provider(config)
            if not provider.should_trim_end():
                return equity_data
        end_ts = pd.Timestamp(end_arg, tz=equity_data.index.tz) + pd.Timedelta(days=1)
        return equity_data[:end_ts]

    def run(self) -> None:
        config = self._config_with_runner_source(_load_config())
        
        # Early configuration and environment validation
        from src.utils.config_utils import validate_config
        validate_config(config)

        start_arg, end_arg = _validate_date_args()

        load_start = (
            _warmup_start(start_arg, self.warmup_months)
            if start_arg and self.warmup_months > 0
            else start_arg
        )

        self.pre_load_check(config, start_arg, end_arg)
        equity_data = self.load_data(config, load_start, end_arg)
        equity_data = self.trim_end_date(equity_data, end_arg, config=config)

        trade_start = (
            pd.Timestamp(start_arg, tz=equity_data.index.tz) if start_arg else None
        )

        # Compute OOS start from is_fraction config
        is_fraction = config.get("backtest", {}).get("is_fraction", 0.0)
        if is_fraction > 0.0 and trade_start is not None:
            end_ts = equity_data.index[-1]
            oos_start_raw = trade_start + (end_ts - trade_start) * is_fraction
            oos_idx = equity_data.index.get_indexer([oos_start_raw], method="bfill")[0]
            oos_start = equity_data.index[oos_idx] if oos_idx >= 0 else oos_start_raw
            logger.info(
                "IS/OOS split active: is_fraction=%.2f, OOS starts at %s",
                is_fraction,
                oos_start,
            )
        else:
            oos_start = trade_start  # no split: all trading is OOS

        logger.info("Data loaded: %s to %s", equity_data.index[0], equity_data.index[-1])

        # Hard fail if loaded data doesn't reach the requested end date.
        # Allow 3 days slack for weekends and market holidays.
        if end_arg:
            actual_last = equity_data.index[-1].date()
            requested_end = pd.Timestamp(end_arg).date()
            days_short = (requested_end - actual_last).days
            if days_short > 3:
                logger.error(
                    "Data ends at %s but %s was requested (%d days short). "
                    "Re-run to trigger a fresh download, or check your date range.",
                    actual_last, requested_end, days_short,
                )
                sys.exit(1)
        if trade_start:
            if self.warmup_months > 0:
                logger.info(
                    "Trading starts: %s (warm-up: %d months prior)",
                    trade_start,
                    self.warmup_months,
                )
            else:
                logger.info("Trading starts: %s", trade_start)

        engine = BacktestEngine(
            config=config,
            equity_data=equity_data,
            trade_start=trade_start,
            oos_start=oos_start,
        )
        portfolio = engine.run()

        trade_log = portfolio.get_trade_log()
        equity_curve = portfolio.get_equity_df()

        if trade_start and not equity_curve.empty:
            equity_curve = equity_curve[trade_start:]

        # Split trade log and equity curve into IS and OOS
        has_split = is_fraction > 0.0 and trade_start is not None and oos_start != trade_start
        if has_split:
            is_trade_log = trade_log[trade_log["entry_time"] < oos_start] if not trade_log.empty else trade_log
            oos_trade_log = trade_log[trade_log["entry_time"] >= oos_start] if not trade_log.empty else trade_log
            is_equity_curve  = equity_curve[equity_curve.index < oos_start]  if not equity_curve.empty else equity_curve
            oos_equity_curve = equity_curve[equity_curve.index >= oos_start] if not equity_curve.empty else equity_curve
        else:
            is_trade_log = pd.DataFrame()
            oos_trade_log = trade_log
            is_equity_curve = pd.DataFrame()
            oos_equity_curve = equity_curve

        run_dt = datetime.now()
        run_tag = _build_run_tag(config)

        # Print metrics before updating run_key, but include the current tag in
        # the trial count so a newly tested variant is still deflated.
        n_trials = count_trials(current_tag=run_tag)
        if has_split:
            is_metrics = compute_metrics(is_trade_log, is_equity_curve, n_trials=n_trials)
            print("\n" + "=" * 50)
            print("IN-SAMPLE RESULTS (not for evaluation)")
            print("=" * 50)
            print_metrics(is_metrics)

        oos_metrics = compute_metrics(oos_trade_log, oos_equity_curve, n_trials=n_trials)
        if has_split:
            print("\n" + "=" * 50)
            print("OUT-OF-SAMPLE RESULTS (valid performance)")
            print("=" * 50)
        print_metrics(oos_metrics)

        # Buy & hold benchmark (matches TradingView methodology:
        # enter at the underlying's close when the first trade fires, hold to last bar close)
        initial_capital = config.get("strategy", {}).get("initial_capital", 100_000)
        oos_price_data = engine.data[oos_start:] if oos_start else engine.data
        oos_close = oos_price_data["close"] if "close" in oos_price_data.columns else pd.Series(dtype=float)
        strategy_final_equity = oos_metrics.get("final_equity", initial_capital)
        # Always use the underlying's close price at first trade time (not option premium)
        first_trade_price = None
        if not oos_trade_log.empty:
            first_entry_time = pd.Timestamp(oos_trade_log.iloc[0]["entry_time"])
            idx = oos_close.index.get_indexer([first_entry_time], method="bfill")[0]
            first_trade_price = float(oos_close.iloc[idx])
        bh_benchmark = compute_buy_hold_benchmark(oos_close, initial_capital, strategy_final_equity, first_trade_price)
        print_benchmark(bh_benchmark)

        start_dt = pd.Timestamp(start_arg) if start_arg else equity_data.index[0]
        end_dt   = pd.Timestamp(end_arg)   if end_arg   else equity_data.index[-1]
        _update_run_key(config, run_tag, run_dt)
        run_date_folder = run_dt.strftime("%Y-%m-%d")
        date_folder = (
            f"{start_dt.strftime('%B-%d-%Y')}_to_{end_dt.strftime('%B-%d-%Y')}"
            f"_{run_tag}"
        )
        mode = config["strategy"]["trade_mode"]
        timeframe = config["strategy"]["timeframe"]
        results_dir = f"results/{self.source_name}/{run_date_folder}/{date_folder}/{mode}/{timeframe}"
        os.makedirs(results_dir, exist_ok=True)

        # Save OOS results (primary output)
        oos_trade_log.to_csv(f"{results_dir}/backtest.csv", index=False)
        logger.info("OOS trade log saved to %s/backtest.csv", results_dir)

        oos_trade_data = engine.data[oos_start:] if oos_start else engine.data
        if not oos_equity_curve.empty:
            plot_equity_curve(oos_equity_curve, save_path=f"{results_dir}/equity_curve.png")
            plot_drawdown(oos_equity_curve, save_path=f"{results_dir}/drawdown.png")
            oos_equity_curve.to_csv(f"{results_dir}/equity_data.csv")
        if not oos_trade_log.empty:
            plot_signals_on_price(oos_trade_data, oos_trade_log, save_path=f"{results_dir}/signals.png")

        oos_trade_data[["close"]].to_csv(f"{results_dir}/price_data.csv")

        oos_data_range = f"{oos_start or trade_start or equity_data.index[0]} to {equity_data.index[-1]}"
        save_report_md(oos_metrics, config, oos_data_range, f"{results_dir}/report.md", bh=bh_benchmark)
        save_config_snapshot(config, f"{results_dir}/config.yaml")

        # Save IS results only when a split is active
        if has_split:
            is_trade_log.to_csv(f"{results_dir}/backtest_IS.csv", index=False)
            logger.info("IS trade log saved to %s/backtest_IS.csv", results_dir)

            is_trade_data = engine.data[trade_start:oos_start] if trade_start else engine.data[:oos_start]
            if not is_equity_curve.empty:
                plot_equity_curve(is_equity_curve, save_path=f"{results_dir}/equity_curve_IS.png")
                plot_drawdown(is_equity_curve, save_path=f"{results_dir}/drawdown_IS.png")
                is_equity_curve.to_csv(f"{results_dir}/equity_data_IS.csv")
            if not is_trade_log.empty:
                plot_signals_on_price(
                    is_trade_data, is_trade_log, save_path=f"{results_dir}/signals_IS.png"
                )

            is_data_range = f"{trade_start or equity_data.index[0]} to {oos_start}"
            save_report_md(is_metrics, config, is_data_range, f"{results_dir}/report_IS.md")

        run_mc = "--mc" in sys.argv
        run_sizing = "--sizing" in sys.argv
        if run_mc or run_sizing:
            from src.analysis.monte_carlo import run_monte_carlo, run_sizing_validation
            if len(oos_trade_log) < 5:
                logger.warning(
                    "Inline MC/sizing skipped: only %d OOS trades (need at least 5)", len(oos_trade_log)
                )
            else:
                if run_mc:
                    run_monte_carlo(results_dir, config)
                if run_sizing:
                    tol = 10.0
                    max_c = 20
                    for i, arg in enumerate(sys.argv):
                        if arg == "--sizing-tolerance" and i + 1 < len(sys.argv):
                            try:
                                tol = float(sys.argv[i + 1])
                            except ValueError:
                                pass
                        if arg == "--sizing-max-contracts" and i + 1 < len(sys.argv):
                            try:
                                max_c = int(sys.argv[i + 1])
                            except ValueError:
                                pass
                    run_sizing_validation(results_dir, config,
                        sizing_tolerance_pct=tol, max_contracts=max_c)
