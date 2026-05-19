---
tags: [analytics, metrics, sharpe, sortino, visualization, monte-carlo]
---
# Deep Dive: Analytics & Output Pipeline

How backtest results are measured, visualized, and optionally stress-tested via Monte Carlo. Covers `compute_metrics()`, Sharpe/Sortino/PSR methodology, chart generation, and the MC bootstrap.

> Part of the [02-code-walkthrough.md](02-code-walkthrough.md) — covers Phase 7.

---

## Metrics Computation

**File:** `src/analysis/metrics.py:100`

```python
def compute_metrics(trade_log: pd.DataFrame, equity_curve: pd.DataFrame) -> dict:
```

### Trade Statistics

| Metric | How |
|--------|-----|
| `total_trades` | `len(trade_log)` |
| `winning_trades` | `trade_log[pnl > 0]` count |
| `losing_trades` | `trade_log[pnl < 0]` count |
| `win_rate` | `winning / total * 100` |
| `avg_win` | Mean P&L of winners |
| `avg_loss` | Mean P&L of losers |
| `total_pnl` | Sum of all trade P&L |
| `avg_pnl_pct` | Mean percentage P&L across all trades |
| `profit_factor` | Gross profit / gross loss (∞ if no losses) |
| `trade_sharpe` | Trade-level annualized Sharpe (see below) |
| `psr` | Probabilistic Sharpe Ratio (see below) |
| `exit_reasons` | Value counts of `exit_reason` column |

### Equity Curve Statistics

| Metric | How |
|--------|-----|
| `sharpe_ratio` | Monthly returns Sharpe (always-invested assumption) |
| `sortino_ratio` | Monthly returns Sortino |
| `max_drawdown_pct` | `min((equity - running_max) / running_max) * 100` |
| `final_equity` | Last equity value |
| `total_return_pct` | `(final / initial - 1) * 100` |

---

## Advanced Quant Metrics

### Trade-level Sharpe Ratio

Unlike the monthly Sharpe which assumes continuous market exposure, the **Trade Sharpe** measures the risk-adjusted return of individual trade episodes.

```python
trade_sharpe = (mean(pnl_pct) / std(pnl_pct)) * sqrt(trades_per_year)
```

This is a more "honest" metric for strategies that trade infrequently or have long flat periods.

### Probabilistic Sharpe Ratio (PSR)

Implemented from Marcos López de Prado's research, the PSR estimates the probability that the true Sharpe Ratio is greater than a benchmark (default 0.0). It corrects for:
1. **Sample size**: Number of trades.
2. **Skewness**: Asymmetry of returns.
3. **Kurtosis**: Fat tails (extreme outliers).

A PSR > 0.95 indicates 95% confidence that the strategy has a positive edge.

---

## Sharpe and Sortino Methodology

These ratios are computed on **monthly returns** to match TradingView's strategy tester methodology.

### Monthly Returns

```python
returns = _compute_monthly_returns(equity)
```

`resample("ME")` groups by month-end, `.last()` takes the final equity value for each month, and `.pct_change()` computes month-over-month returns.

### Risk-Free Rate

```python
RFR_MONTHLY = 0.02 / 12  # 2% annual → ~0.00167 monthly
```

Matches the TradingView default assumption.

### Sharpe Ratio (Monthly)

```python
def _sharpe(returns, rfr=RFR_MONTHLY):
    return (returns.mean() - rfr) / returns.std()
```

No annualization — this is the **monthly Sharpe**, consistent with TradingView.

### Sortino Ratio

```python
def _sortino(returns, rfr=RFR_MONTHLY):
    dd = sqrt(mean(min(0, r)^2))  # downside deviation across ALL returns
    return (returns.mean() - rfr) / dd
```

---

## Visualization

**File:** `src/analysis/visualize.py`

### `plot_equity_curve(equity_df, save_path)`
Simple line chart of portfolio value over time.

### `plot_drawdown(equity_df, save_path)`
Filled area chart showing drawdown periods.

### `plot_signals_on_price(price_df, trade_log, save_path)`
Price line with trade markers overlaid (Green ▲ = long, Red ▼ = short, Black × = exit).

---

## Monte Carlo Simulation

**File:** `src/analysis/monte_carlo.py`

Bootstraps trade P&Ls with replacement to produce a distribution of possible equity curves. This reveals how much of the result depends on trade ordering vs. actual strategy edge.

### Bootstrap Method

```python
def _simulate_equity_curves(pnl_array, initial_capital, n_sim, seed):
    rng = np.random.default_rng(seed)
    samples = rng.choice(pnl_array, size=(n_sim, n_trades), replace=True)
    return initial_capital + np.cumsum(samples, axis=1)
```

### Outputs

| File | Content |
|------|---------|
| `mc_equity_fan.png` | Fan chart with 5/25/50/75/95 percentile bands + actual equity path |
| `mc_distributions.png` | 2×2 histogram grid (return, drawdown, win rate, profit factor) |
| `mc_report.md` | Markdown table with percentile statistics |
