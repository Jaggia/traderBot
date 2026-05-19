---
tags: [engine, backtest-loop, exit-logic, position-sizing, numpy]
---
# Deep Dive: Engine Hot Loop

The bar-by-bar backtest loop inside `BacktestEngine.run()`. Covers numpy extraction, the exit→entry→MTM cycle, equity vs options exit differences, position sizing, and mark-to-market accounting.

> Part of the [02-code-walkthrough.md](02-code-walkthrough.md) — covers Phase 4.

**File:** `src/backtest/engine.py:106`

---

## Why Numpy?

The engine processes thousands of bars in a tight loop. Using `df.iloc[i]` or `df.iterrows()` on each bar would be dominated by pandas indexing overhead. Instead:

```python
timestamps = data.index.to_numpy()
closes = data["close"].to_numpy(dtype=np.float64)
highs = data["high"].to_numpy(dtype=np.float64)
lows = data["low"].to_numpy(dtype=np.float64)
signals = data["signal"].to_numpy(dtype=np.int64)
hours = data.index.hour.to_numpy()
minutes = data.index.minute.to_numpy()
```

Numpy array indexing (`closes[i]`) is ~10x faster than pandas `.iloc[i]`. The arrays are extracted once before the loop starts, and the loop reads them by integer index.

---

## Index Resolution

```python
trade_start_idx = data.index.searchsorted(trade_start_ts)  # binary search
oos_start_idx = data.index.searchsorted(oos_start_ts)
```

These translate timestamp boundaries into integer indices. Timezone is handled explicitly:
- If `trade_start` is tz-naive and data is tz-aware → localize
- If both are tz-aware but different zones → convert

Bars `0` to `trade_start_idx - 1` are warm-up only: indicators are computed but no trades are taken, and any pending entry is discarded at the boundary.

---

## The Main Loop Flowchart

```
before loop:
    portfolio.record_initial_equity(first_trade_bar_ts)

for i in range(total_bars):
    │
    ├─ i < trade_start_idx? ──yes──→ discard pending entry → continue
    │
    ├─ 0. EXECUTE PENDING ENTRY AT THIS BAR'S OPEN ───────────────────────┐
    │   signal fired on bar[i-1], fill happens at open[i]                 │
    │                                                                     │
    ├─ 1. EXIT CHECK ─────────────────────────────────────────────────────┤
    │   update current_price for each open position                       │
    │   equities: stop/limit first, then opposite signal, then EOD        │
    │   options: intrabar high/low checks, then close-based exit rules    │
    │                                                                     │
    ├─ 2. QUEUE NEW ENTRY FROM THIS BAR'S SIGNAL ────────────────────────┤
    │   signal on close[i] becomes a pending fill for open[i+1]           │
    │                                                                     │
    ├─ 3. MARK TO MARKET ────────────────────────────────────────────────┤
    │   portfolio.mark_to_market(ts)                                      │
    └─────────────────────────────────────────────────────────────────────┘
```

---

## Exit Logic Details

### Equities: Fixed Stop/Limit Levels

At entry, the engine computes fixed price levels (TradingView `strategy.exit()` style):

```python
if signal == 1:  # long
    stop_px = close * (1 - sl_pct)      # e.g. 500 * 0.80 = 400
    limit_px = close * (1 + tp_pct)     # e.g. 500 * 1.20 = 600
else:  # short
    stop_px = close * (1 + sl_pct)      # e.g. 500 * 1.20 = 600
    limit_px = close * (1 - tp_pct)     # e.g. 500 * 0.80 = 400
```

These are stored on the `Position` object and checked against the bar's `high` and `low` (not just close). This simulates intrabar fills — if the bar's low touched the stop level, the exit happened even if the close recovered.

**Exit priority for equities:**
1. **Stop loss** — `low <= stop_price` (long) or `high >= stop_price` (short) → fills at `stop_price`
2. **Profit target** — `high >= limit_price` (long) or `low <= limit_price` (short) → fills at `limit_price`
3. **Opposite signal** — signal flipped direction
4. **EOD close** — `hour >= 15 and minute >= 55` (last bar of trading day)

Note: stop loss is checked before profit target. If both could trigger on the same bar (wide-range bar), the stop takes priority — a conservative assumption.

### Options: Percentage-Based Exits

Options use `check_option_exit()` from `src/options/exit_rules.py`. The key difference: **options use percentage P&L**, not fixed price levels.

```python
pnl_pct = (current_price - entry_price) / entry_price * 100
```

Why? Option prices are nonlinear — a $1 move in the underlying might cause a $0.50 move in the option at entry but a $3 move near expiry. Fixed dollar stops don't translate well.

**Exit priority for options:**
1. **Stop loss** — `pnl_pct <= -stop_loss_pct`
2. **Profit target** — `pnl_pct >= profit_target_pct`
3. **Opposite signal** — signal flipped direction
4. **EOD close** — `hour >= 15 and minute >= 55`
5. **Expiration** — `ts.date() > pos.expiry.date()`

Same-day expiry is normally handled by `eod_close`; the explicit expiration check is a next-day safety net.

---

## Entry Logic Details

### Position Sizing

```python
def _compute_shares(self, price: float) -> int:
    if self._sizing_mode == "percent_of_equity":
        equity = self.portfolio.get_equity()
        return int((equity * self._sizing_pct) / price)
    return self._fixed_contracts
```

- **`fixed`** (current default): always uses `contracts_per_trade` (currently 1)
- **`percent_of_equity`**: computes `floor(equity * sizing_pct / price)` using the underlying price. This is primarily sensible for equities; for options it maps underlying-share sizing into contract count

### Equities Entry

```python
pos = Position(
    direction=signal,       # +1 or -1
    entry_price=fill_open,
    entry_time=fill_ts,
    contracts=contracts,
    trade_mode="equities",
    stop_price=stop_px,     # Fixed at entry
    limit_price=limit_px,   # Fixed at entry
)
portfolio.open_position(pos)
```

### Options Entry

Delegated to `build_option_position()` — see [05-options-pipeline-deep-dive.md](05-options-pipeline-deep-dive.md).

### `can_open()` Gate

```python
def can_open(self) -> bool:
    return len(self.positions) < max_concurrent_positions  # default: 1
```

With the default of 1, signals that arrive while a position is already open are ignored. The limit applies to the total positions in the portfolio; `"both"` mode no longer exists.

---

## Mark-to-Market

Called on trading bars after the initial equity baseline has been recorded:

```python
portfolio.mark_to_market(ts)
```

Both equity and options position prices are already updated during the exit check loop (step 1): equities get `pos.current_price = close`, options get `pos.current_price = _get_option_price(...)`. The MTM step just records the snapshot.

`mark_to_market()` delegates to `_positions_value()`:
```python
equity = self.cash + self._positions_value()
# _positions_value() = sum(direction * _notional(current_price, contracts, trade_mode))
```

This continuous equity series is essential for computing Sharpe/Sortino ratios on monthly returns (not just trade-level P&L).

---

## End-of-Backtest Cleanup

```python
if total_bars > 0:
    last_ts = timestamps[-1]
    for pos in list(portfolio.positions):
        portfolio.close_position(pos, pos.current_price, last_ts, "backtest_end")
```

Any positions still open at the last bar are force-closed with reason `"backtest_end"`. This ensures all positions are reflected in the trade log.

---

## Options Price Updates in the Loop

When options positions exist, the engine must price them on every bar (not just at entry/exit):

```python
pos.current_price = self._get_option_price(
    close, pos.strike, dte_years(pos.expiry, ts),
    pos.option_type, pos.raw_symbol, ts
)
```

This uses observed Databento option bars and raises if no usable market data is available. The `dte_years()` utility (from `src/options/utils.py`) computes:
```python
return max((expiry - current_time).total_seconds(), 0) / (365.0 * 86400)
```

This preserves intraday precision for 0-DTE contracts, so time decay is reflected throughout the session.

---

## Performance Considerations

The loop is O(n) where n = number of bars. For a typical 3-month backtest at 5-min resolution:
- ~78 trading days × 78 bars/day ≈ 6,000 bars
- With 3 months warm-up: ~12,000 bars total

The numpy extraction makes this fast enough that a full run completes in seconds for equities-only mode. Options mode is slower due to the `_get_option_price()` disk I/O per bar per position.

### Lazy Options Loader

```python
@property
def options_loader(self) -> Optional[DatabentoOptionsLoader]:
    if self._options_loader is None and self.trade_mode == "options":
        api_key = os.getenv("DATA_BENTO_PW")
        self._options_loader = DatabentoOptionsLoader(api_key=api_key, cache_dir=opts_dir)
    return self._options_loader
```

The `DatabentoOptionsLoader` is only created when first needed. In equities mode it is never initialized; in options mode it can run with API access or in cache-only mode.
