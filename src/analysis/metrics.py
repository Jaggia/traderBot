import logging
import math
import os

import numpy as np
import pandas as pd
import yaml

logger = logging.getLogger(__name__)

RFR_MONTHLY = 0.02 / 12  # 2% annual risk-free rate, matching TradingView default

EULER_MASCHERONI = 0.5772156649015329  # Euler-Mascheroni constant


def _sharpe(returns: pd.Series, rfr: float = RFR_MONTHLY) -> float:
    """Sharpe ratio on monthly returns (no annualization), matching TradingView."""
    if len(returns) < 2:
        logger.warning("Sharpe: insufficient monthly returns (%d periods) — returning None", len(returns))
        return None
    if returns.std() == 0:
        return 0.0
    return round((returns.mean() - rfr) / returns.std(), 2)


def _sortino(returns: pd.Series, rfr: float = RFR_MONTHLY) -> float:
    """Sortino ratio on monthly returns, matching TradingView's formula.

    DD = sqrt(mean(min(0, r)^2)) across all N returns (not just negatives).
    """
    if len(returns) < 2:
        logger.warning("Sortino: insufficient monthly returns (%d periods) — returning None", len(returns))
        return None
    dd = np.sqrt((np.minimum(returns.values, 0) ** 2).mean())
    if dd == 0:
        return float("inf") if returns.mean() > rfr else 0.0
    return round((returns.mean() - rfr) / dd, 2)


def _skewness(x: np.ndarray) -> float:
    """Fisher-Pearson skewness of a sample."""
    if len(x) < 3: return 0.0
    mu = np.mean(x)
    sigma = np.std(x, ddof=0)
    if sigma == 0: return 0.0
    return np.mean(((x - mu) / sigma) ** 3)


def _kurtosis(x: np.ndarray) -> float:
    """Pearson kurtosis (absolute, not excess)."""
    if len(x) < 4: return 3.0
    mu = np.mean(x)
    sigma = np.std(x, ddof=0)
    if sigma == 0: return 3.0
    return np.mean(((x - mu) / sigma) ** 4)


def _norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution function."""
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def _psr(
    sharpe: float,
    n_trades: int,
    skewness: float,
    kurt: float,
    benchmark_sharpe: float = 0.0,
) -> float:
    """Probabilistic Sharpe Ratio (López de Prado).

    Estimates the probability that the true Sharpe Ratio is greater than
    a benchmark. Corrects for skewness, kurtosis, and sample size.
    """
    if sharpe is None or n_trades < 3:
        return None

    # Standard deviation of the Sharpe Ratio estimate
    # σ_SR = sqrt( (1 - γ₁*SR + (γ₂-1)/4 * SR²) / (T - 1) )
    denom = 1.0 - skewness * sharpe + (kurt - 1.0) / 4.0 * sharpe**2
    if denom <= 0:
        return 1.0 if sharpe > benchmark_sharpe else 0.0

    sr_std = np.sqrt(denom / (n_trades - 1.0))

    if sr_std == 0:
        return 1.0 if sharpe > benchmark_sharpe else 0.0

    z = (sharpe - benchmark_sharpe) / sr_std
    return round(_norm_cdf(z), 4)


def _norm_ppf(p: float) -> float:
    """Inverse standard normal CDF (quantile function).

    Uses Acklam's rational approximation, accurate to ~1e-9.
    Valid for 0 < p < 1.
    """
    if p <= 0 or p >= 1:
        raise ValueError(f"p must be in (0, 1), got {p}")

    # Rational approximation coefficients (Acklam)
    a = [
        -3.969683028665376e+01,
         2.209460984245205e+02,
        -2.759285104469687e+02,
         1.383577518672690e+02,
        -3.066479806614716e+01,
         2.506628277459239e+00,
    ]
    b = [
        -5.447609879822406e+01,
         1.615858368580409e+02,
        -1.556989798598866e+02,
         6.680131188771972e+01,
        -1.328068155288572e+01,
    ]
    c = [
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e+00,
        -2.549732539343734e+00,
         4.374664141464968e+00,
         2.938163982698783e+00,
    ]
    d = [
         7.784695709041462e-03,
         3.224671290700398e-01,
         2.445134137142996e+00,
         3.754408661907416e+00,
    ]

    p_low = 0.02425
    p_high = 1.0 - p_low

    if p < p_low:
        # Rational approximation for lower region
        q = math.sqrt(-2.0 * math.log(p))
        x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    elif p <= p_high:
        # Rational approximation for central region
        q = p - 0.5
        r = q * q
        x = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
            (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    else:
        # Rational approximation for upper region
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        numer = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
        denom = ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
        x = -(numer / denom)

    return x


def _expected_max_sharpe(n_trials: int, sr_std: float = 1.0) -> float:
    """Expected maximum Sharpe Ratio under the null (all strategies have zero true SR).

    Uses the Bailey & López de Prado (2014) formula:
        SR* = σ_SR · [(1 - γ) · Φ⁻¹(1 - 1/N) + γ · Φ⁻¹(1 - 1/(N·e))]

    where γ is the Euler-Mascheroni constant and σ_SR is the estimated
    standard deviation of the Sharpe estimate. The default sr_std=1.0 returns
    the unscaled expected maximum of standard-normal trials.
    """
    if n_trials <= 1:
        return 0.0
    if sr_std < 0:
        raise ValueError(f"sr_std must be non-negative, got {sr_std}")
    g = EULER_MASCHERONI
    term1 = _norm_ppf(1.0 - 1.0 / n_trials)
    term2 = _norm_ppf(1.0 - 1.0 / (n_trials * math.e))
    return sr_std * ((1.0 - g) * term1 + g * term2)


def _dsr(
    sharpe: float | None,
    n_trials: int,
    n_observations: int,
    skewness: float,
    kurtosis: float,
) -> float | None:
    """Deflated Sharpe Ratio (Bailey & López de Prado).

    Extends PSR by replacing the benchmark Sharpe (0) with the expected maximum
    Sharpe under N independent trials. Corrects for multiple testing bias.

    Returns None if sharpe is None or n_observations < 3 (delegates to PSR logic).
    """
    if sharpe is None or n_observations < 3:
        return None

    denom = 1.0 - skewness * sharpe + (kurtosis - 1.0) / 4.0 * sharpe**2
    if denom <= 0:
        benchmark = _expected_max_sharpe(n_trials)
    else:
        sr_std = math.sqrt(denom / (n_observations - 1.0))
        benchmark = _expected_max_sharpe(n_trials, sr_std=sr_std)
    return _psr(sharpe, n_observations, skewness, kurtosis, benchmark_sharpe=benchmark)


def count_trials(run_key_path: str = "results/run_key.yaml", current_tag: str | None = None) -> int:
    """Count distinct strategy variants in run_key.yaml.

    Each key represents a unique strategy configuration that was tested.
    If current_tag is provided, include it in the count even before the run key
    is updated, so the current variant is part of the multiple-testing penalty.
    Returns 1 if the file doesn't exist or is empty (conservative default).
    """
    if not os.path.exists(run_key_path):
        return 1
    try:
        with open(run_key_path) as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError):
        return 1
    if not data or not isinstance(data, dict):
        return 1
    n_trials = len(data)
    if current_tag is not None and current_tag not in data:
        n_trials += 1
    return max(n_trials, 1)


def compute_drawdown_pct(equity):
    """Return drawdown as a fraction (e.g. -0.05 = -5%). Works with both pd.Series and np.ndarray."""
    if isinstance(equity, np.ndarray):
        running_max = np.maximum.accumulate(equity)
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(running_max != 0, (equity - running_max) / running_max, 0.0)
    else:
        running_max = equity.cummax()
        return (equity - running_max) / running_max.replace(0, np.nan)


def compute_profit_factor(pnl) -> float:
    """Gross profit / gross loss. Returns inf if no losses."""
    arr = np.asarray(pnl, dtype=float)
    gross_profit = arr[arr > 0].sum()
    gross_loss = abs(arr[arr < 0].sum())
    with np.errstate(divide="ignore", invalid="ignore"):
        return float(np.where(gross_loss > 0, gross_profit / gross_loss, np.inf))


def _compute_monthly_returns(equity: pd.Series) -> pd.Series:
    """Resample equity curve to monthly returns, matching TradingView."""
    if equity.empty:
        return pd.Series(dtype=float)
    monthly_equity = equity.resample("ME").last().dropna()
    
    first_idx = equity.index[0]
    if first_idx not in monthly_equity.index:
        monthly_equity = pd.concat([pd.Series({first_idx: equity.iloc[0]}), monthly_equity])
        
    return monthly_equity.pct_change().dropna()


def compute_metrics(trade_log: pd.DataFrame, equity_curve: pd.DataFrame, n_trials: int = 1) -> dict:
    """Compute performance metrics from a backtest.

    Parameters
    ----------
    trade_log : DataFrame with at least 'pnl' and 'pnl_pct' columns
    equity_curve : DataFrame with 'equity' column indexed by timestamp
    n_trials : Number of independent strategy variants tested (for DSR).
        Defaults to 1 (no multiple testing correction).

    Returns
    -------
    dict of metric name -> value
    """
    metrics = {}

    # -- Trade statistics --
    metrics["total_trades"] = len(trade_log)
    if trade_log.empty:
        return metrics

    wins = trade_log[trade_log["pnl"] > 0]
    losses = trade_log[trade_log["pnl"] < 0]

    metrics["winning_trades"] = len(wins)
    metrics["losing_trades"] = len(losses)
    metrics["win_rate"] = round(len(wins) / len(trade_log) * 100, 2) if len(trade_log) > 0 else 0
    metrics["avg_win"] = round(wins["pnl"].mean(), 2) if len(wins) > 0 else 0
    metrics["avg_loss"] = round(losses["pnl"].mean(), 2) if len(losses) > 0 else 0
    metrics["total_pnl"] = round(trade_log["pnl"].sum(), 2)
    metrics["avg_pnl_pct"] = round(trade_log["pnl_pct"].mean(), 2)

    pf = compute_profit_factor(trade_log["pnl"])
    metrics["profit_factor"] = round(min(pf, 999.99), 2)

    # -- Trade-level Sharpe & PSR --
    pnl_pcts = trade_log["pnl_pct"].values / 100.0  # normalize to fraction for math
    if len(pnl_pcts) >= 2:
        # Determine duration for annualization
        if not equity_curve.empty:
            duration = equity_curve.index[-1] - equity_curve.index[0]
            days = max(duration.total_seconds() / 86400.0, 1.0)
            trades_per_year = len(pnl_pcts) * (365.0 / days)
            metrics["trades_per_year"] = round(trades_per_year, 1)

            mean_pnl = pnl_pcts.mean()
            std_pnl = pnl_pcts.std()
            if std_pnl > 0:
                # raw_sr is trade-level (per trade)
                raw_sr = mean_pnl / std_pnl
                metrics["trade_sharpe"] = round(raw_sr * np.sqrt(trades_per_year), 2)

                # PSR uses the raw_sr (per-observation SR)
                sk = _skewness(pnl_pcts)
                kt = _kurtosis(pnl_pcts)
                metrics["psr"] = _psr(raw_sr, len(pnl_pcts), sk, kt)

                # DSR (Deflated Sharpe Ratio) — only when multiple trials are tracked
                if n_trials > 1:
                    metrics["dsr"] = _dsr(raw_sr, n_trials, len(pnl_pcts), sk, kt)

    # -- Exit reason breakdown --
    if "exit_reason" in trade_log.columns:
        metrics["exit_reasons"] = trade_log["exit_reason"].value_counts().to_dict()

    # -- Equity curve statistics --
    if not equity_curve.empty and "equity" in equity_curve.columns:
        equity = equity_curve["equity"]

        returns = _compute_monthly_returns(equity)

        metrics["sharpe_ratio"] = _sharpe(returns)
        metrics["sortino_ratio"] = _sortino(returns)

        # Max drawdown
        metrics["max_drawdown_pct"] = round(compute_drawdown_pct(equity).min() * 100, 2)

        metrics["final_equity"] = round(equity.iloc[-1], 2)
        metrics["total_return_pct"] = round(
            (equity.iloc[-1] / equity.iloc[0] - 1) * 100, 2
        )

    return metrics


def compute_buy_hold_benchmark(
    price_series: pd.Series,
    initial_capital: float,
    strategy_final_equity: float,
    first_trade_price: float = None,
) -> dict:
    """Compute buy-and-hold benchmark matching TradingView's methodology.

    TV defines B&H as: invest entire initial capital at the close of the bar
    where the strategy's first trade fires (same process_orders_on_close fill),
    hold until the last bar close of the test period.

    Parameters
    ----------
    price_series : Close price series over the trading period (must have >= 2 bars).
    initial_capital : Initial portfolio value (e.g. 100_000).
    strategy_final_equity : Strategy's final equity for outperformance calculation.
    first_trade_price : Entry price of the strategy's first trade. If None, falls
        back to the first bar's close price.

    Returns
    -------
    dict with bh_return_usd, bh_return_pct, bh_outperformance_usd.
    """
    if price_series.empty or len(price_series) < 2:
        return {}

    start_price = first_trade_price if first_trade_price is not None else float(price_series.iloc[0])
    end_price = float(price_series.iloc[-1])

    shares = initial_capital / start_price  # fractional shares
    bh_pnl = shares * (end_price - start_price)
    bh_pct = (end_price / start_price - 1) * 100
    outperformance = strategy_final_equity - (initial_capital + bh_pnl)

    return {
        "bh_return_usd": round(bh_pnl, 2),
        "bh_return_pct": round(bh_pct, 2),
        "bh_outperformance_usd": round(outperformance, 2),
    }


def print_metrics(metrics: dict):
    """Pretty-print backtest metrics."""
    print("\n" + "=" * 50)
    print("BACKTEST RESULTS")
    print("=" * 50)
    for key, value in metrics.items():
        if key == "exit_reasons":
            print(f"\nExit Reasons:")
            for reason, count in value.items():
                print(f"  {reason}: {count}")
        else:
            print(f"  {key}: {'N/A' if value is None else value}")
    print("=" * 50)


def print_benchmark(bh: dict):
    """Pretty-print buy-and-hold benchmark comparison."""
    if not bh:
        return
    print("\n" + "=" * 50)
    print("BENCHMARK COMPARISON (Buy & Hold)")
    print("=" * 50)
    sign = "+" if bh["bh_return_usd"] >= 0 else ""
    print(f"  Buy & Hold Return:      {sign}{bh['bh_return_usd']:,.2f} USD  ({sign}{bh['bh_return_pct']:.2f}%)")
    sign_out = "+" if bh["bh_outperformance_usd"] >= 0 else ""
    print(f"  Strategy Outperformance:{sign_out}{bh['bh_outperformance_usd']:,.2f} USD")
    print("=" * 50)


def save_report_md(metrics: dict, config: dict, data_range: str, save_path: str, bh: dict = None):
    """Save a markdown report with backtest results and config summary."""
    lines = [
        "# Backtest Report",
        "",
        f"**Data Range:** {data_range}",
        f"**Trade Mode:** {config.get('strategy', {}).get('trade_mode', 'N/A')}",
        f"**Initial Capital:** ${config.get('strategy', {}).get('initial_capital', 100000):,.0f}",
        "",
        "## Performance Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
    ]

    metric_labels = {
        "total_trades": "Total Trades",
        "winning_trades": "Winning Trades",
        "losing_trades": "Losing Trades",
        "win_rate": "Win Rate (%)",
        "avg_win": "Avg Win ($)",
        "avg_loss": "Avg Loss ($)",
        "total_pnl": "Total P&L ($)",
        "avg_pnl_pct": "Avg P&L (%)",
        "profit_factor": "Profit Factor",
        "trades_per_year": "Trades per Year",
        "trade_sharpe": "Trade-level Sharpe",
        "psr": "Probabilistic Sharpe (PSR)",
        "dsr": "Deflated Sharpe (DSR)",
        "sharpe_ratio": "Sharpe Ratio (assumes always-invested)",
        "sortino_ratio": "Sortino Ratio",
        "max_drawdown_pct": "Max Drawdown (%)",
        "final_equity": "Final Equity ($)",
        "total_return_pct": "Total Return (%)",
    }

    for key, label in metric_labels.items():
        if key in metrics:
            val = metrics[key]
            if val is None:
                lines.append(f"| {label} | N/A |")
            elif isinstance(val, float):
                lines.append(f"| {label} | {val:,.2f} |")
            else:
                lines.append(f"| {label} | {val} |")

    if "exit_reasons" in metrics:
        lines += [
            "",
            "## Exit Reasons",
            "",
            "| Reason | Count |",
            "|--------|-------|",
        ]
        for reason, count in metrics["exit_reasons"].items():
            lines.append(f"| {reason} | {count} |")

    if bh:
        sign = "+" if bh["bh_return_usd"] >= 0 else ""
        sign_out = "+" if bh["bh_outperformance_usd"] >= 0 else ""
        lines += [
            "",
            "## Benchmark Comparison (Buy & Hold)",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Buy & Hold Return | {sign}{bh['bh_return_usd']:,.2f} USD ({sign}{bh['bh_return_pct']:.2f}%) |",
            f"| Strategy Outperformance | {sign_out}{bh['bh_outperformance_usd']:,.2f} USD |",
        ]

    sig_system = config.get("strategy", {}).get("signal_system", "indicator_pair")
    lines += [
        "",
        "## Strategy Config",
        "",
        "| Parameter | Value |",
        "|-----------|-------|",
        f"| Signal System | {sig_system} |",
    ]
    if sig_system == "ema_233":
        ema_cfg = config.get("signals_ema", {})
        lines += [
            f"| EMA Period | {ema_cfg.get('ema_period', 233)} |",
            f"| Entry Offset (cents) | {ema_cfg.get('entry_offset_cents', 0.02)} |",
        ]
    elif sig_system == "indicator_pair":
        sig = config.get("signals", {})
        if "indicator_1" in sig or "indicator_2" in sig:
            lines += [
                f"| Indicator 1 | {sig.get('indicator_1', 'N/A')} |",
                f"| Indicator 2 | {sig.get('indicator_2', 'N/A')} |",
                f"| Pair Mode | {sig.get('pair_mode', 'either')} |",
                f"| Sequential | {sig.get('sequential', False)} |",
                f"| Sync Window | {sig.get('sync_window')} |",
                f"| VWAP Filter | {sig.get('vwap_filter')} |",
            ]
        else:
            lines += [
                f"| SMI Fast | ({sig.get('smi_fast', {}).get('period')}, {sig.get('smi_fast', {}).get('smooth1')}, {sig.get('smi_fast', {}).get('smooth2')}) |",
                f"| SMI Slow | ({sig.get('smi_slow', {}).get('period')}, {sig.get('smi_slow', {}).get('smooth1')}, {sig.get('smi_slow', {}).get('smooth2')}) |",
                f"| Williams %R | {sig.get('williams_r', {}).get('period')} |",
                f"| Sync Window | {sig.get('sync_window')} |",
                f"| Pair Mode | {sig.get('pair_mode', 'indicator_2_then_indicator_1')} |",
                f"| Sequential | {sig.get('sequential', False)} |",
                f"| VWAP Filter | {sig.get('vwap_filter')} |",
            ]
    elif sig_system == "trigger_chain":
        chain_cfg = config.get("signals", {}).get("trigger_chain", config.get("signals", {}))
        triggers = chain_cfg.get("triggers", [])
        trigger_names = [t.get("indicator", "N/A") for t in triggers]
        lines += [
            f"| Triggers | {' -> '.join(trigger_names)} |",
            f"| Sequential | {chain_cfg.get('sequential', True)} |",
            f"| Sync Window | {chain_cfg.get('sync_window', 5)} |",
            f"| VWAP Filter | {chain_cfg.get('vwap_filter', False)} |",
        ]
    lines += [
        f"| TP / SL | {config.get('exits', {}).get('profit_target_pct')}% / {config.get('exits', {}).get('stop_loss_pct')}% |",
        f"| Position Sizing | {config.get('position', {}).get('sizing_mode', 'fixed')} @ {config.get('position', {}).get('sizing_pct', 'N/A')}% |",
        "",
        "## Charts",
        "",
    ]

    lines += [
        "![Equity Curve](equity_curve.png)",
        "![Drawdown](drawdown.png)",
        "![Signals](signals.png)",
    ]

    with open(save_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    logger.info("Report saved to %s", save_path)


def save_config_snapshot(config: dict, path: str):
    """Dump full config YAML alongside results for dashboard replay."""
    with open(path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    logger.info("Config snapshot saved to %s", path)
