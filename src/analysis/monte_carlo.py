"""Monte Carlo simulation for backtesting — trade bootstrap with replacement.

Resamples closed trade P&Ls N times to produce a distribution of equity curves,
revealing how much of the result depends on trade sequencing vs. strategy edge.
"""

import logging
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.analysis.metrics import compute_drawdown_pct, compute_profit_factor

logger = logging.getLogger(__name__)


def run_monte_carlo(
    results_dir: str,
    config: dict,
    n_simulations: int = 1000,
    seed: int = 42,
    ruin_floor_pct: float = 0.5,
) -> None:
    """Run Monte Carlo simulation on a completed backtest and save outputs.

    Reads ``{results_dir}/backtest.csv``, resamples trade P&Ls with replacement,
    and writes 4 files to ``{results_dir}/monte_carlo/``.

    Parameters
    ----------
    results_dir:
        Path to a results folder containing ``backtest.csv`` and ``config.yaml``.
    config:
        Strategy config dict (used for ``initial_capital``).
    n_simulations:
        Number of bootstrap resamples.
    seed:
        Random seed for reproducibility.
    ruin_floor_pct:
        Fraction of initial capital defining "ruin" (default 0.5 = 50% loss).
    """
    backtest_csv = os.path.join(results_dir, "backtest.csv")
    trade_log = pd.read_csv(backtest_csv)

    if len(trade_log) < 5:
        n = len(trade_log)
        logger.error("MC aborted: only %d trades (need at least 5).", n)
        raise ValueError(f"Monte Carlo requires at least 5 trades, got {n}")

    initial_capital = config.get("strategy", {}).get("initial_capital", 100_000)
    pnl_array = trade_log["pnl"].to_numpy(dtype=float)

    logger.info("MC: running %d simulations on %d trades...", n_simulations, len(pnl_array))

    mc_dir = os.path.join(results_dir, "monte_carlo")
    os.makedirs(mc_dir, exist_ok=True)

    equity_curves = _simulate_equity_curves(pnl_array, initial_capital, n_simulations, seed)
    mc_metrics = _compute_mc_metrics(equity_curves, pnl_array, initial_capital, ruin_floor_pct)

    # Actual metrics from the original trade sequence
    original_equity = initial_capital + np.cumsum(pnl_array)
    actual_total_return = (original_equity[-1] / initial_capital - 1) * 100
    actual_max_dd = float((compute_drawdown_pct(original_equity) * 100).min())
    actual_pf = compute_profit_factor(pnl_array)
    actual_calmar = (actual_total_return / abs(actual_max_dd)) if actual_max_dd < 0 else float("inf")
    actual_consec_losses = int(_max_consecutive_losses(pnl_array.reshape(1, -1))[0])
    actual_ruined = float(original_equity.min() < initial_capital * ruin_floor_pct)
    actual_metrics = {
        "total_return_pct": actual_total_return,
        "max_drawdown_pct": actual_max_dd,
        "win_rate": float(np.mean(pnl_array > 0) * 100),
        "profit_factor": actual_pf,
        "calmar_ratio": actual_calmar,
        "max_consec_losses": float(actual_consec_losses),
        "ruined": actual_ruined,
    }

    _plot_mc_fan(equity_curves, pnl_array, initial_capital, os.path.join(mc_dir, "mc_equity_fan.png"))
    _plot_mc_distributions(mc_metrics, actual_metrics, os.path.join(mc_dir, "mc_distributions.png"))
    _save_mc_report(mc_metrics, actual_metrics, n_simulations, os.path.join(mc_dir, "mc_report.md"), ruin_floor_pct)

    mc_df = pd.DataFrame(mc_metrics)
    mc_df.to_csv(os.path.join(mc_dir, "mc_metrics.csv"), index=False)

    logger.info("MC: results saved to %s/", mc_dir)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _max_consecutive_losses(sim_pnls: np.ndarray) -> np.ndarray:
    """Return max consecutive losing trades per simulation row.

    Parameters
    ----------
    sim_pnls:
        Shape (n_sim, n_trades). Each row is one simulation's per-trade P&Ls.

    Returns
    -------
    1-D int array of length n_sim.
    """
    is_loss = (sim_pnls < 0).astype(np.int32)  # (n_sim, n_trades)
    n_sim, n_trades = is_loss.shape
    result = np.zeros(n_sim, dtype=np.int32)
    current = np.zeros(n_sim, dtype=np.int32)
    for t in range(n_trades):
        current = np.where(is_loss[:, t] == 1, current + 1, 0)
        result = np.maximum(result, current)
    return result


def _simulate_equity_curves(
    pnl_array: np.ndarray,
    initial_capital: float,
    n_sim: int,
    seed: int,
    ruin_floor: float = 0.0,
) -> np.ndarray:
    """Bootstrap trade P&Ls and cumsum into equity curves.
    If equity drops at or below ruin_floor, it stays flat at that value.

    Returns
    -------
    ndarray of shape (n_sim, n_trades) — equity level after each trade.
    """
    rng = np.random.default_rng(seed)
    n_trades = len(pnl_array)
    samples = rng.choice(pnl_array, size=(n_sim, n_trades), replace=True)
    raw_paths = initial_capital + np.cumsum(samples, axis=1)

    # Vectorized ruin floor application:
    # 1. Identify where each path first hits or crosses ruin_floor
    is_ruined = raw_paths <= ruin_floor
    # 2. Cumulative max along time axis (once ruined, always ruined)
    ever_ruined = np.maximum.accumulate(is_ruined, axis=1)
    # 3. For each simulation, find the first ruin value (if any)
    # We can use argmax to find the first True index
    first_ruin_idx = np.argmax(is_ruined, axis=1)
    # 4. Get the actual ruin values (the value at the moment of first ruin)
    # We use np.take_along_axis to pick the first ruin value from each row
    ruin_values = np.take_along_axis(raw_paths, first_ruin_idx[:, np.newaxis], axis=1)
    # 5. Replace all post-ruin values with the first ruin value
    # We only apply this to rows that actually hit ruin (argmax returns 0 if no True exists, 
    # so we need to filter using np.any)
    any_ruined = np.any(is_ruined, axis=1)
    raw_paths = np.where(ever_ruined & any_ruined[:, np.newaxis], ruin_values, raw_paths)

    return raw_paths


def _compute_mc_metrics(
    equity_curves: np.ndarray,
    pnl_array: np.ndarray,
    initial_capital: float,
    ruin_floor_pct: float = 0.5,
) -> dict:
    """Compute per-simulation scalar metrics.

    Parameters
    ----------
    equity_curves:
        Shape (n_sim, n_trades).
    pnl_array:
        Original trade P&Ls (unused here but kept for API consistency).
    initial_capital:
        Starting portfolio value.
    ruin_floor_pct:
        Fraction of initial capital below which a simulation is considered
        "ruined" (default 0.5 = 50% capital loss).

    Returns
    -------
    dict of metric_name -> 1-D array of length n_sim.
    """
    final_equity = equity_curves[:, -1]
    total_return_pct = (final_equity / initial_capital - 1) * 100

    # Max drawdown per simulation
    running_max = np.maximum.accumulate(equity_curves, axis=1)
    dd = (equity_curves - running_max) / running_max * 100
    max_drawdown_pct = dd.min(axis=1)

    # Win rate: fraction of sampled trades with pnl > 0 per simulation
    # equity_curves doesn't store individual pnls; recompute from diffs
    n_sim, n_trades = equity_curves.shape
    # Prepend initial_capital column to compute per-trade pnls
    ec_with_start = np.hstack([np.full((n_sim, 1), initial_capital), equity_curves])
    sim_pnls = np.diff(ec_with_start, axis=1)
    win_rate = np.mean(sim_pnls > 0, axis=1) * 100

    # Profit factor per simulation
    gross_profit = np.where(sim_pnls > 0, sim_pnls, 0.0).sum(axis=1)
    gross_loss = np.abs(np.where(sim_pnls < 0, sim_pnls, 0.0).sum(axis=1))
    with np.errstate(divide="ignore", invalid="ignore"):
        profit_factor = np.where(gross_loss > 0, gross_profit / gross_loss, np.inf)

    # Calmar ratio: annualized total return / abs(max drawdown)
    # Use raw return / abs(max_dd) as a ratio — annualization requires dates,
    # which aren't available here; the ratio still ranks simulations correctly.
    with np.errstate(divide="ignore", invalid="ignore"):
        calmar = np.where(
            max_drawdown_pct < 0,
            total_return_pct / np.abs(max_drawdown_pct),
            np.inf,
        )

    # Max consecutive losing trades per simulation
    max_consec_losses = _max_consecutive_losses(sim_pnls)

    # Risk of ruin: 1 if equity ever dropped below ruin_floor_pct * initial_capital
    ruin_floor = initial_capital * ruin_floor_pct
    ruined = (equity_curves.min(axis=1) < ruin_floor).astype(float)

    return {
        "total_return_pct": total_return_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "calmar_ratio": calmar,
        "max_consec_losses": max_consec_losses,
        "ruined": ruined,
    }


def _plot_mc_fan(
    equity_curves: np.ndarray,
    pnl_array: np.ndarray,
    initial_capital: float,
    save_path: str,
) -> None:
    """Fan chart: percentile bands + original equity path."""
    fig, ax = plt.subplots(figsize=(12, 5))

    x = np.arange(1, equity_curves.shape[1] + 1)

    p5  = np.percentile(equity_curves, 5,  axis=0)
    p25 = np.percentile(equity_curves, 25, axis=0)
    p50 = np.percentile(equity_curves, 50, axis=0)
    p75 = np.percentile(equity_curves, 75, axis=0)
    p95 = np.percentile(equity_curves, 95, axis=0)

    ax.fill_between(x, p5,  p95, alpha=0.15, color="steelblue", label="5–95th pct")
    ax.fill_between(x, p25, p75, alpha=0.30, color="steelblue", label="25–75th pct")
    ax.plot(x, p50, color="steelblue", linewidth=1.5, label="Median")

    original_equity = initial_capital + np.cumsum(pnl_array)
    ax.plot(x, original_equity, color="darkorange", linewidth=1.5, label="Actual path")

    ax.axhline(initial_capital, color="gray", linewidth=0.8, linestyle="--", alpha=0.6)
    ax.set_title("Monte Carlo — Equity Fan Chart")
    ax.set_xlabel("Trade #")
    ax.set_ylabel("Portfolio Value ($)")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(save_path, dpi=150)
    plt.close(fig)


def _plot_mc_distributions(
    mc_metrics: dict,
    actual_metrics: dict,
    save_path: str,
) -> None:
    """3x2 histogram grid for key MC metrics with actual-value vertical lines."""
    metric_labels = {
        "total_return_pct": "Total Return (%)",
        "max_drawdown_pct": "Max Drawdown (%)",
        "win_rate": "Win Rate (%)",
        "profit_factor": "Profit Factor",
        "calmar_ratio": "Calmar Ratio",
        "max_consec_losses": "Max Consecutive Losses",
    }

    fig, axes = plt.subplots(3, 2, figsize=(12, 12))
    axes = axes.flatten()

    for ax, (key, label) in zip(axes, metric_labels.items()):
        data = mc_metrics[key].astype(float)
        # Clip infinite values for plotting
        finite_mask = np.isfinite(data)
        if finite_mask.any():
            cap = np.nanmax(data[finite_mask]) * 1.1
        else:
            cap = 10.0
        data = np.where(np.isinf(data), cap, data)

        ax.hist(data, bins=50, color="steelblue", alpha=0.7, edgecolor="none")
        actual_val = float(actual_metrics[key])
        if np.isfinite(actual_val):
            ax.axvline(actual_val, color="darkorange", linewidth=1.5, label=f"Actual: {actual_val:.2f}")
        ax.set_title(label)
        ax.set_xlabel(label)
        ax.set_ylabel("Count")
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)

    fig.suptitle("Monte Carlo — Metric Distributions", fontsize=13)
    fig.tight_layout()
    fig.savefig(save_path, dpi=150)
    plt.close(fig)


def _percentile_rank(data: np.ndarray, value: float) -> float:
    """Return the percentile rank of ``value`` within ``data`` (0–100).

    Uses only finite values. Returns NaN if no finite values exist.
    """
    finite = data[np.isfinite(data)]
    if len(finite) == 0 or not np.isfinite(value):
        return float("nan")
    return float(np.mean(finite <= value) * 100)


def _save_mc_report(
    mc_metrics: dict,
    actual_metrics: dict,
    n_simulations: int,
    save_path: str,
    ruin_floor_pct: float = 0.5,
) -> None:
    """Markdown report: percentile table + risk-of-ruin + interpretation."""
    metric_labels = {
        "total_return_pct": "Total Return (%)",
        "max_drawdown_pct": "Max Drawdown (%)",
        "win_rate": "Win Rate (%)",
        "profit_factor": "Profit Factor",
        "calmar_ratio": "Calmar Ratio",
        "max_consec_losses": "Max Consec. Losses",
    }

    def fmt(v):
        if isinstance(v, float) and (np.isinf(v) or np.isnan(v)):
            return "∞" if np.isinf(v) else "—"
        return f"{v:.2f}"

    lines = [
        "# Monte Carlo Report",
        "",
        f"**Simulations:** {n_simulations}",
        f"**Method:** Trade bootstrap with replacement",
        f"**Ruin floor:** {ruin_floor_pct * 100:.0f}% of initial capital",
        "",
        "## Metric Percentiles",
        "",
        "| Metric | P5 | P25 | P50 | P75 | P95 | Actual | Pct Rank |",
        "|--------|-----|-----|-----|-----|-----|--------|----------|",
    ]

    for key, label in metric_labels.items():
        data = mc_metrics[key].astype(float)
        finite_data = data[np.isfinite(data)]
        if len(finite_data) > 0:
            p5, p25, p50, p75, p95 = np.percentile(finite_data, [5, 25, 50, 75, 95])
        else:
            p5 = p25 = p50 = p75 = p95 = float("nan")
        actual = float(actual_metrics[key])
        rank = _percentile_rank(data, actual)
        rank_str = f"{rank:.0f}th" if np.isfinite(rank) else "—"
        lines.append(
            f"| {label} | {fmt(p5)} | {fmt(p25)} | {fmt(p50)} | {fmt(p75)} | {fmt(p95)} | {fmt(actual)} | {rank_str} |"
        )

    # Risk of ruin
    ror_pct = float(np.mean(mc_metrics["ruined"]) * 100)
    lines += [
        "",
        "## Risk of Ruin",
        "",
        f"**{ror_pct:.1f}%** of simulations hit the ruin floor "
        f"({ruin_floor_pct * 100:.0f}% capital loss at any point).",
    ]

    # Interpretation block
    pf_actual = float(actual_metrics["profit_factor"])
    pf_rank = _percentile_rank(mc_metrics["profit_factor"].astype(float), pf_actual)
    ret_rank = _percentile_rank(mc_metrics["total_return_pct"].astype(float), float(actual_metrics["total_return_pct"]))
    dd_rank  = _percentile_rank(mc_metrics["max_drawdown_pct"].astype(float),  float(actual_metrics["max_drawdown_pct"]))

    interp_lines = ["", "## Interpretation", ""]

    # Sequencing luck verdict (profit factor rank)
    if np.isfinite(pf_rank):
        if pf_rank >= 75:
            interp_lines.append(
                f"- **Sequencing luck (high):** Actual profit factor sits at the "
                f"{pf_rank:.0f}th percentile. The strategy likely benefited from "
                f"favorable trade ordering — the true edge may be weaker than reported."
            )
        elif pf_rank <= 25:
            interp_lines.append(
                f"- **Sequencing luck (low):** Actual profit factor sits at the "
                f"{pf_rank:.0f}th percentile. The strategy was *hurt* by trade ordering; "
                f"median resampling suggests stronger underlying edge."
            )
        else:
            interp_lines.append(
                f"- **Sequencing luck (neutral):** Actual profit factor sits at the "
                f"{pf_rank:.0f}th percentile — near the median of resampled paths. "
                f"The result appears representative of the strategy's true edge."
            )

    # Return percentile
    if np.isfinite(ret_rank):
        interp_lines.append(
            f"- **Return rank:** Actual total return is at the {ret_rank:.0f}th percentile "
            f"of MC simulations."
        )

    # Drawdown percentile (lower rank = worse drawdown than most sims)
    if np.isfinite(dd_rank):
        if dd_rank <= 25:
            interp_lines.append(
                f"- **Drawdown:** Actual max drawdown is worse than {100 - dd_rank:.0f}% of "
                f"simulations — the backtest experienced an unusually severe drawdown."
            )
        elif dd_rank >= 75:
            interp_lines.append(
                f"- **Drawdown:** Actual max drawdown is better than {dd_rank:.0f}% of "
                f"simulations — the backtest was fortunate with drawdown."
            )

    # Risk of ruin commentary
    if ror_pct >= 10:
        interp_lines.append(
            f"- **Risk of ruin:** {ror_pct:.1f}% of simulations hit the ruin floor — "
            f"position sizing or stop-loss rules warrant review."
        )
    elif ror_pct > 0:
        interp_lines.append(
            f"- **Risk of ruin:** {ror_pct:.1f}% of simulations hit the ruin floor "
            f"(tail risk present but not dominant)."
        )
    else:
        interp_lines.append(
            f"- **Risk of ruin:** 0% of simulations hit the ruin floor — "
            f"capital preservation appears robust under bootstrap resampling."
        )

    lines += interp_lines
    lines += [
        "",
        "## Charts",
        "",
        "![Equity Fan](mc_equity_fan.png)",
        "![Distributions](mc_distributions.png)",
    ]

    with open(save_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    logger.info("MC: report saved to %s", save_path)


# ---------------------------------------------------------------------------
# Position sizing validation
# ---------------------------------------------------------------------------

def _scale_pnl(pnl_array: np.ndarray, n_contracts: int) -> np.ndarray:
    """Scale per-trade P&Ls to N contracts.

    Both costs are strictly per-contract, so net_pnl at N contracts = pnl_1c * N.
    """
    return pnl_array * n_contracts


def _plot_sizing_chart(
    contracts: list,
    p95_dd: list,
    p50_dd: list,
    tolerance_pct: float,
    recommended_n: int,
    save_path: str,
) -> None:
    """Line chart: P95 and P50 worst-case drawdown magnitude vs contract count."""
    fig, ax = plt.subplots(figsize=(10, 5))

    # Convert to positive magnitudes for readability
    p95_mag = [abs(v) for v in p95_dd]
    p50_mag = [abs(v) for v in p50_dd]

    ax.plot(contracts, p95_mag, color="tomato", linewidth=2, marker="o", markersize=4, label="P95 worst DD%")
    ax.plot(contracts, p50_mag, color="steelblue", linewidth=2, marker="o", markersize=4, label="P50 DD%")
    ax.axhline(tolerance_pct, color="red", linewidth=1.5, linestyle="--", label=f"Tolerance ({tolerance_pct}%)")

    if recommended_n > 0:
        ax.axvline(recommended_n, color="green", linewidth=1.5, linestyle="--",
                   label=f"Max safe: {recommended_n}c")
    else:
        ax.annotate("No valid size found", xy=(contracts[0], tolerance_pct),
                    fontsize=9, color="red", ha="left", va="bottom")

    ax.set_title("Position Sizing Validation — P95 Drawdown vs Contract Count")
    ax.set_xlabel("Contracts")
    ax.set_ylabel("Drawdown Magnitude (%)")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(save_path, dpi=150)
    plt.close(fig)


def _save_sizing_report(
    rows: list,
    recommended_n: int,
    tolerance_pct: float,
    initial_capital: float,
    n_simulations: int,
    save_path: str,
) -> None:
    """Write mc_sizing.md with the contract-sweep results table and recommendation."""
    max_contracts = len(rows)
    tol_dollars = int(tolerance_pct / 100 * initial_capital)

    lines = [
        "# Position Sizing Validation",
        "",
        f"**Simulations per level:** {n_simulations} | "
        f"**Tolerance:** {tolerance_pct}% (${tol_dollars:,} on ${int(initial_capital):,}) | "
        f"**Sweep:** 1–{max_contracts} contracts",
        "",
        "## Results",
        "",
        "| Contracts | P95 Worst DD% | P50 DD% | P50 Return% | Risk of Ruin% | Recommended |",
        "|---|---|---|---|---|---|",
    ]

    for row in rows:
        rec = "**YES**" if row["n"] == recommended_n and recommended_n > 0 else ""
        lines.append(
            f"| {row['n']} | {row['p95_dd']:.1f} | {row['p50_dd']:.1f} | "
            f"{row['p50_ret']:+.1f} | {row['ror']:.1f} | {rec} |"
        )

    lines += ["", "## Recommendation", ""]

    if recommended_n > 0:
        rec_row = next(r for r in rows if r["n"] == recommended_n)
        lines.append(f"**Maximum recommended contracts: {recommended_n}**")
        lines.append("")
        rec_dollars = int(abs(rec_row["p95_dd"]) / 100 * initial_capital)
        lines.append(
            f"At {recommended_n}c: P95 worst-case drawdown = {rec_row['p95_dd']:.1f}% "
            f"(${rec_dollars:,}) — within {tolerance_pct}% tolerance."
        )
        if recommended_n < max_contracts:
            next_row = next(r for r in rows if r["n"] == recommended_n + 1)
            next_dollars = int(abs(next_row["p95_dd"]) / 100 * initial_capital)
            lines.append(
                f"At {recommended_n + 1}c: {next_row['p95_dd']:.1f}% (${next_dollars:,}) — exceeds tolerance."
            )
    else:
        lines.append("**No valid size found.**")
        lines.append("")
        row1 = rows[0]
        r1_dollars = int(abs(row1["p95_dd"]) / 100 * initial_capital)
        lines.append(
            f"Even 1 contract produces a P95 worst-case drawdown of {row1['p95_dd']:.1f}% "
            f"(${r1_dollars:,}), which exceeds the {tolerance_pct}% tolerance. "
            f"Consider a tighter stop-loss or wait for a larger trade sample."
        )

    with open(save_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    logger.info("Sizing: report saved to %s", save_path)


def run_sizing_validation(
    results_dir: str,
    config: dict,
    n_simulations: int = 1000,
    seed: int = 42,
    ruin_floor_pct: float = 0.5,
    sizing_tolerance_pct: float = 10.0,
    max_contracts: int = 20,
) -> dict:
    """Sweep 1–max_contracts and find the largest N where P95 worst-case drawdown
    stays within sizing_tolerance_pct of initial capital.

    Parameters
    ----------
    results_dir:
        Path to a results folder containing ``backtest.csv``.
    config:
        Strategy config dict (used for ``initial_capital``).
    n_simulations:
        Bootstrap resamples per contract level.
    seed:
        Base random seed; seed+n is used per contract level for independence.
    ruin_floor_pct:
        Fraction of capital defining ruin (passed to _compute_mc_metrics).
    sizing_tolerance_pct:
        Maximum acceptable P95 worst-case drawdown as % of capital.
    max_contracts:
        Upper bound of the contract sweep.

    Returns
    -------
    dict with keys ``recommended_n`` (int) and ``rows`` (list of dicts), or
    empty dict if fewer than 5 trades are found.
    """
    backtest_csv = os.path.join(results_dir, "backtest.csv")
    trade_log = pd.read_csv(backtest_csv)

    if len(trade_log) < 5:
        logger.warning(
            "Sizing validation skipped: only %d trades (need at least 5).", len(trade_log)
        )
        return {}

    initial_capital = config.get("strategy", {}).get("initial_capital", 100_000)
    pnl_array = trade_log["pnl"].to_numpy(dtype=float)

    logger.info(
        "Sizing: sweeping 1–%d contracts on %d trades (tolerance=%.1f%%)...",
        max_contracts, len(pnl_array), sizing_tolerance_pct,
    )

    mc_dir = os.path.join(results_dir, "monte_carlo")
    os.makedirs(mc_dir, exist_ok=True)

    rows = []
    recommended_n = 0
    contracts_list = list(range(1, max_contracts + 1))
    p95_dd_list = []
    p50_dd_list = []

    for n in contracts_list:
        scaled = _scale_pnl(pnl_array, n)
        # Use seed+n so each contract level gets an independent RNG state
        ruin_floor = initial_capital * ruin_floor_pct
        curves = _simulate_equity_curves(scaled, initial_capital, n_simulations, seed + n, ruin_floor)
        metrics = _compute_mc_metrics(curves, scaled, initial_capital, ruin_floor_pct)

        # np.percentile(max_dd_arr, 5) returns the most-negative value (worst case).
        # This is labeled "P95 worst DD" — the value that 95% of orderings stay *above*.
        p95_dd = float(np.percentile(metrics["max_drawdown_pct"], 5))
        p50_dd = float(np.percentile(metrics["max_drawdown_pct"], 50))
        p50_ret = float(np.percentile(metrics["total_return_pct"], 50))
        ror = float(np.mean(metrics["ruined"]) * 100)

        rows.append({"n": n, "p95_dd": p95_dd, "p50_dd": p50_dd, "p50_ret": p50_ret, "ror": ror})
        p95_dd_list.append(p95_dd)
        p50_dd_list.append(p50_dd)

        if abs(p95_dd) <= sizing_tolerance_pct:
            recommended_n = n

    _save_sizing_report(rows, recommended_n, sizing_tolerance_pct, initial_capital, n_simulations,
                        os.path.join(mc_dir, "mc_sizing.md"))
    _plot_sizing_chart(contracts_list, p95_dd_list, p50_dd_list, sizing_tolerance_pct, recommended_n,
                       os.path.join(mc_dir, "mc_sizing.png"))

    logger.info("Sizing: recommended contracts = %d", recommended_n)
    return {"recommended_n": recommended_n, "rows": rows}
