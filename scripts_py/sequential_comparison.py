# Sequential vs Data Source Comparison

import logging
import os
import sys
import copy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml
import pandas as pd

from src.utils.logging_config import setup_logging
from src.data.alpaca_loader import load_cached_csvs
from src.data.tradingview_loader import load_tradingview_csv
from src.data.databento_loader import load_databento_equities
from src.backtest.engine import BacktestEngine
from src.analysis.metrics import compute_metrics

logger = logging.getLogger(__name__)

WARMUP_MONTHS = 3
START = "2025-11-10"
END = "2026-02-13"
TV_CSV = "data/TV/equities/SYMBOL/5min/2025-11-10-TO-2026-02-13.csv"


def load_config(path="config/strategy_params.yaml"):
    with open(path, "r") as f:
        return yaml.safe_load(f)


def warmup_start(start: str) -> str:
    dt = pd.Timestamp(start)
    year, month = dt.year, dt.month - WARMUP_MONTHS
    while month < 1:
        month += 12
        year -= 1
    return f"{year}-{month:02d}"


def run_combo(base_config, equity_data, trade_start, pair_mode, is_sequential):
    config = copy.deepcopy(base_config)
    config["signals"]["pair_mode"] = pair_mode
    config["signals"]["sequential"] = is_sequential

    engine = BacktestEngine(config=config, equity_data=equity_data, trade_start=trade_start)
    portfolio = engine.run()

    trade_log = portfolio.get_trade_log()
    equity_curve = portfolio.get_equity_df()
    if trade_start and not equity_curve.empty:
        equity_curve = equity_curve[trade_start:]

    return compute_metrics(trade_log, equity_curve)


def fmt(val, key):
    if val is None or val == "N/A":
        return "N/A"
    if key in ("total_trades", "winning_trades", "losing_trades"):
        return str(int(val))
    if key in ("win_rate", "total_return_pct", "max_drawdown_pct", "avg_pnl_pct"):
        return f"{val:+.2f}%" if key != "win_rate" else f"{val:.2f}%"
    if key in ("avg_win", "avg_loss", "total_pnl", "final_equity"):
        return f"${val:,.2f}"
    if key in ("sharpe_ratio", "sortino_ratio", "profit_factor"):
        return f"{val:.2f}"
    return str(val)


def build_table(columns: dict, col_labels: dict, col_width=20) -> list[str]:
    """Build a box-drawing table. columns: {key: metrics_dict}, col_labels: {key: header_str}."""
    metrics_order = [
        ("total_trades", "Total Trades"),
        ("winning_trades", "Winning Trades"),
        ("losing_trades", "Losing Trades"),
        ("win_rate", "Win Rate"),
        ("profit_factor", "Profit Factor"),
        ("avg_win", "Avg Win"),
        ("avg_loss", "Avg Loss"),
        ("total_pnl", "Total P&L"),
        ("total_return_pct", "Total Return"),
        ("sharpe_ratio", "Sharpe Ratio"),
        ("sortino_ratio", "Sortino Ratio"),
        ("max_drawdown_pct", "Max Drawdown"),
        ("final_equity", "Final Equity"),
    ]

    metric_width = 18
    keys = list(columns.keys())
    n = len(keys)

    def row_line(left, mid, right, fill="─"):
        s = f"{left}{fill * metric_width}{mid}"
        for i in range(n):
            end = right if i == n - 1 else mid
            s += f"{fill * col_width}{end}"
        return s

    lines = []
    lines.append(row_line("┌", "┬", "┐"))

    hdr = f"│{'Metric':^{metric_width}}│"
    for k in keys:
        hdr += f"{col_labels[k]:^{col_width}}│"
    lines.append(hdr)
    lines.append(row_line("├", "┼", "┤"))

    for mkey, mlabel in metrics_order:
        r = f"│{mlabel:^{metric_width}}│"
        for k in keys:
            val = columns[k].get(mkey, "N/A")
            r += f"{fmt(val, mkey):^{col_width}}│"
        lines.append(r)
        lines.append(row_line("├", "┼", "┤"))

    # Replace last mid-line with bottom
    lines[-1] = row_line("└", "┴", "┘")
    return lines


def build_md(alpaca_results: dict, tv_results: dict = None, db_results: dict = None) -> str:
    signal_labels = {
        ("indicator_1_then_indicator_2", False): "SMI→WR",
        ("indicator_1_then_indicator_2", True): "SMI→WR Sequential",
        ("indicator_2_then_indicator_1", False): "WR→SMI",
        ("indicator_2_then_indicator_1", True): "WR→SMI Sequential",
    }

    all_sources = [("Alpaca", "alpaca", alpaca_results)]
    if tv_results:
        all_sources.append(("TradingView", "tv", tv_results))
    if db_results:
        all_sources.append(("Databento", "db", db_results))

    n_combos = len(all_sources) * 4
    lines = [
        "# Sequential vs Data Source Comparison",
        "",
        f"## Backtest Performance ({START} to {END})",
        "",
        f"{n_combos} combos: `data_source` × `pair_mode` × `sequential`, equities mode.",
        "",
    ]

    # --- One table per data source ---
    for source_name, source_key, source_results in all_sources:
        lines.append(f"### {source_name} Data")
        lines.append("")
        lines.append("```")
        col_data = {}
        col_labels = {}
        for combo in source_results:
            col_data[(source_key, *combo)] = source_results[combo]
            col_labels[(source_key, *combo)] = signal_labels[combo]
        lines.extend(build_table(col_data, col_labels))
        lines.append("```")
        lines.append("")

    # --- Summary: best/worst across all sources ---
    all_results = {}
    for source_name, _, source_results in all_sources:
        for combo, metrics in source_results.items():
            all_results[f"{source_name} {signal_labels[combo]}"] = metrics

    lines.append("## Key Findings")
    lines.append("")

    def _fmt_pct(metrics, key):
        val = metrics.get(key)
        return f"{val:+.2f}%" if val is not None else "N/A"

    best = max(all_results.items(), key=lambda x: x[1].get("total_return_pct", -999))
    worst = min(all_results.items(), key=lambda x: x[1].get("total_return_pct", 999))
    lines.append(f"- **Best Total Return:** {best[0]} at {_fmt_pct(best[1], 'total_return_pct')}")
    lines.append(f"- **Worst Total Return:** {worst[0]} at {_fmt_pct(worst[1], 'total_return_pct')}")
    lines.append("")

    # Cross-source comparison
    lines.append("### Cross-Source Comparison")
    combos = [("indicator_1_then_indicator_2", False), ("indicator_1_then_indicator_2", True), ("indicator_2_then_indicator_1", False), ("indicator_2_then_indicator_1", True)]
    for combo in combos:
        parts = []
        for source_name, _, source_results in all_sources:
            ret = source_results[combo].get("total_return_pct", 0)
            trades = source_results[combo].get("total_trades", 0)
            parts.append(f"{source_name} {ret:+.2f}% ({trades}t)")
        lines.append(f"- **{signal_labels[combo]}:** {' vs '.join(parts)}")
    lines.append("")

    # Sequential vs non-sequential per data source
    lines.append("### Sequential vs Non-Sequential Impact")
    for source_name, _, source_results in all_sources:
        for mode in ("indicator_1_then_indicator_2", "indicator_2_then_indicator_1"):
            sequential = source_results[(mode, True)]
            non_sequential = source_results[(mode, False)]
            mode_label = "SMI→WR" if mode == "indicator_1_then_indicator_2" else "WR→SMI"
            trade_diff = sequential.get("total_trades", 0) - non_sequential.get("total_trades", 0)
            ret_diff = sequential.get("total_return_pct", 0) - non_sequential.get("total_return_pct", 0)
            lines.append(f"- **{source_name} {mode_label}:** Sequential {'reduces' if trade_diff < 0 else 'adds'} "
                         f"{abs(trade_diff)} trades, return delta {ret_diff:+.2f}%")
    lines.append("")

    return "\n".join(lines) + "\n"


def main():
    base_config = load_config()

    # --- Load Alpaca data ---
    equities_dir = base_config.get("data", {}).get("equities_dir", "data/Alpaca/equities/SYMBOL/5min")
    load_start = warmup_start(START)
    logger.info("Loading Alpaca equity data...")
    alpaca_data = load_cached_csvs(equities_dir, start=load_start, end=END)
    end_ts = pd.Timestamp(END, tz=alpaca_data.index.tz) + pd.Timedelta(days=1)
    alpaca_data = alpaca_data[:end_ts]
    alpaca_trade_start = pd.Timestamp(START, tz=alpaca_data.index.tz)
    logger.info("  Alpaca: %s to %s", alpaca_data.index[0], alpaca_data.index[-1])

    # --- Load TradingView data ---
    tv_data = None
    tv_trade_start = None
    if not os.path.exists(TV_CSV):
        logger.warning("TV CSV not found at %s — skipping TradingView source", TV_CSV)
    else:
        logger.info("Loading TradingView equity data...")
        tv_data = load_tradingview_csv(TV_CSV)
        logger.info("  TV: %s to %s", tv_data.index[0], tv_data.index[-1])

    # --- Load Databento data ---
    db_equities_dir = base_config.get("data", {}).get(
        "databento_equities_dir", "data/DataBento/equities/SYMBOL/5min"
    )
    db_data = None
    db_trade_start = None
    try:
        logger.info("Loading Databento equity data...")
        db_data = load_databento_equities(db_equities_dir, start=load_start, end=END)
        end_ts_db = pd.Timestamp(END, tz=db_data.index.tz) + pd.Timedelta(days=1)
        db_data = db_data[:end_ts_db]
        db_trade_start = pd.Timestamp(START, tz=db_data.index.tz)
        logger.info("  Databento: %s to %s", db_data.index[0], db_data.index[-1])
    except FileNotFoundError:
        logger.warning("Databento equity data not found — run scripts_py/download_and_aggregate_databento.py first")
        logger.warning("Continuing with Alpaca + TV only.")

    signal_combos = [
        ("indicator_1_then_indicator_2", False),
        ("indicator_1_then_indicator_2", True),
        ("indicator_2_then_indicator_1", False),
        ("indicator_2_then_indicator_1", True),
    ]

    alpaca_results = {}
    tv_results = {} if tv_data is not None else None
    db_results = {} if db_data is not None else None

    for pair_mode, is_sequential in signal_combos:
        label = f"{pair_mode} | sequential={is_sequential}"

        logger.info("Running Alpaca: %s...", label)
        m = run_combo(base_config, alpaca_data, alpaca_trade_start, pair_mode, is_sequential)
        alpaca_results[(pair_mode, is_sequential)] = m
        logger.info("  -> %d trades, return %+.2f%%", m.get('total_trades', 0), m.get('total_return_pct', 0))

        if tv_data is not None:
            logger.info("Running TV:     %s...", label)
            m = run_combo(base_config, tv_data, tv_trade_start, pair_mode, is_sequential)
            tv_results[(pair_mode, is_sequential)] = m
            logger.info("  -> %d trades, return %+.2f%%", m.get('total_trades', 0), m.get('total_return_pct', 0))

        if db_data is not None:
            logger.info("Running DB:     %s...", label)
            m = run_combo(base_config, db_data, db_trade_start, pair_mode, is_sequential)
            db_results[(pair_mode, is_sequential)] = m
            logger.info("  -> %d trades, return %+.2f%%", m.get('total_trades', 0), m.get('total_return_pct', 0))

    logger.info("Generating comparison report...")
    md = build_md(alpaca_results, tv_results, db_results)
    os.makedirs("results/others", exist_ok=True)
    out_path = "results/others/sequential_comparison.md"
    with open(out_path, "w") as f:
        f.write(md)
    logger.info("Saved to %s", out_path)


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
