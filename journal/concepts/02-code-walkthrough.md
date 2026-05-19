---
tags: [code-walkthrough, execution-trace, phases]
---
# Concept: Code Walkthrough - Shell Script to Results Folder

A current end-to-end trace of the backtest path, following the Databento runner from shell entry point to saved results.

> For a quick module map, see [00-system-overview.md](00-system-overview.md).
> For deeper dives on specific subsystems, see [03-data-loading-deep-dive.md](03-data-loading-deep-dive.md), [04-engine-loop-deep-dive.md](04-engine-loop-deep-dive.md), [05-options-pipeline-deep-dive.md](05-options-pipeline-deep-dive.md), and [06-analytics-deep-dive.md](06-analytics-deep-dive.md).

## Pipeline Overview

```
scripts_bash/run_backtest_db.sh
  -> main_runner/run_backtest_db.py
  -> BaseBacktestRunner.run()
  -> load/trim/split data
  -> BacktestEngine.__init__()
  -> BacktestEngine.run()
  -> Portfolio trade log + equity curve
  -> metrics / benchmark / charts / report
  -> results/{source}/{date_folder}/{mode}/{timeframe}/
```

## Phase 0: Shell Script

**File:** `scripts_bash/run_backtest_db.sh`

The shell wrapper:
1. Sets `START_DATE` and `END_DATE`
2. Optionally enables Monte Carlo via `RUN_MC`
3. Changes into project root
4. Calls `python main_runner/run_backtest_db.py ...`

Sibling launchers:
- `run_backtest_alpaca.sh`
- `run_backtest_tv.sh`

## Phase 1: Entry Point

**File:** `main_runner/run_backtest_db.py`

`DatabentoRunner` subclasses `BaseBacktestRunner` and supplies:
- `source_name = "db"`
- `warmup_months = 3`
- `pre_load_check()` -> `ensure_equity_data(...)`
- `load_data()` -> `load_databento_equities(...)`

Other current runner variants:
- `run_backtest_with_alpaca.py`
- `run_backtest_tv.py`

## Phase 2: Template Method

**File:** `main_runner/base_runner.py`

`BaseBacktestRunner.run()` is the fixed orchestration path:

1. `_load_config()`
   Reads `config/strategy_params.yaml`.

2. `_validate_date_args()`
   Parses positional CLI dates while ignoring flags such as `--mc`.

3. `_warmup_start()`
   Shifts the requested start back by `warmup_months` using `pd.DateOffset`.
   Example: `2025-11-10` with `warmup_months=3` becomes `2025-08-10`.

4. `pre_load_check()`
   Databento only. Ensures monthly 5-minute equity files exist and are fresh enough.

5. `load_data()`
   Loads OHLCV bars into a single `DataFrame` with a tz-aware New York index.

6. `trim_end_date()`
   Applies an inclusive end-date trim when needed.

7. IS/OOS split
   If `backtest.is_fraction > 0`, computes an OOS boundary inside the trading window.

8. Data completeness check
   Hard-fails when the loaded data ends more than 3 days before the requested end date.

9. `BacktestEngine(...)`
   Hands control to the engine with `trade_start` and `oos_start`.

## Phase 3: Engine Initialization

**File:** `src/backtest/engine.py`

`BacktestEngine.__init__()` does four important things:

1. Validates `trade_mode`
   Only `"equities"` and `"options"` are allowed. `"both"` has been removed.

2. Creates the `Portfolio`
   Uses `strategy.initial_capital` unless an explicit `initial_cash` was passed.

3. Computes indicators and signals
   Calls:
   - `compute_indicators(...)`
   - `generate_signals(...)`

4. Caches hot-loop config
   Pulls exits, sizing, and live option-pricing helpers into instance fields.

### Signal generation

The pipeline supports multiple lookforward modes (`wr_then_smi`, `smi_then_wr`, `either`), armed vs non-armed behavior, and an optional VWAP trend filter. All are configurable in `strategy_params.yaml` — the user selects which combination to run.

## Phase 4: Engine Run Loop

**File:** `src/backtest/engine.py`

The current hot loop is:

1. Extract numpy arrays
   Pulls `open`, `high`, `low`, `close`, `signal`, `hour`, `minute`, and timestamps into arrays.

2. Resolve `trade_start_idx` and `oos_start_idx`
   Uses `searchsorted()` after timezone alignment.

3. Record initial equity baseline
   `portfolio.record_initial_equity(...)` writes the first equity curve point before the loop.

4. For each bar:
   - skip warmup bars
   - execute any pending entry at the current bar's **open**
   - check exits
   - queue a new pending entry from the current bar's signal
   - mark to market

### Important behavioral detail: next-bar-open fills

Signals are generated on the close of bar `i`, but fills occur on the open of bar `i+1`.

This is the biggest execution-model detail to remember when comparing:
- raw signals
- underlying bars
- trade log timestamps and prices

### Exit order

Equities:
1. stop loss
2. profit target
3. opposite signal
4. EOD close

Options:
1. intrabar stop-loss check using option-bar lows
2. intrabar profit-target check using option-bar highs
3. close-based `check_option_exit()` fallback:
   - stop loss
   - profit target
   - opposite signal
   - EOD close
   - expiration safety check

## Phase 5: Entry Construction

**Files:** `src/backtest/trade_logic.py`, `src/options/entry_logic.py`

The backtest engine no longer builds positions inline. It delegates to:
- `build_entry(...)`
- `build_option_position(...)`

### Equities entry

Uses the pending fill bar's open as `entry_price`, then computes fixed stop and limit levels from that price.

### Options entry

Build path:
1. map signal to option type (`C` for long, `P` for short signal meaning long put)
2. `select_strike(...)`
3. compute `dte_years(...)`
4. price the option from observed market data
5. back-solve implied vol from the market entry price when possible
6. compute Greeks snapshot from that IV
7. return `Position(...)`

## Phase 6: Option Pricing

**Files:** `src/backtest/engine.py`, `src/data/databento_loader.py`

Backtests use observed option prices only.

`_get_option_price(...)`:
- requires a `raw_symbol`
- uses `DatabentoOptionsLoader`
- loads 1-minute OPRA bars for the full session
- returns:
  - the latest close at or before the requested time, or
  - the intrabar high/low across the 5-minute window when stop/target logic asks for `field="high"` or `field="low"`

Hard failures:
- no symbol
- no options loader
- no market data returned
- stale market data older than 30 minutes

There is no backtest Black-Scholes fallback anymore.

## Phase 7: Portfolio Accounting

**File:** `src/backtest/portfolio.py`

`Portfolio` owns:
- cash
- open positions
- closed trades
- equity curve

### Current accounting behavior

- Options are always long premium positions in this framework
- Options use the 100x contract multiplier
- Costs are modeled:
  - `commission_per_contract`
  - `slippage_pct`
  - `slippage_per_contract`
- Short equities are cash-secured; no margin model is used

### Trade log contents

Closed trades include:
- entry/exit timestamps
- direction
- trade mode
- prices
- contracts
- P&L and P&L %
- exit reason
- strike / expiry / option type
- Greeks snapshot

## Phase 8: Results And Output

**Files:** `main_runner/base_runner.py`, `src/analysis/*`

After the engine returns:

1. Split IS/OOS trade log and equity curve
2. Compute OOS metrics
3. Compute buy-and-hold benchmark on the underlying
4. Build a run tag
5. Save CSVs, charts, report, and config snapshot
6. Optionally run Monte Carlo when `--mc` is present

### Current outputs

```
results/{source}/{date_folder}/{mode}/{timeframe}/
  backtest.csv
  equity_data.csv
  price_data.csv
  equity_curve.png
  drawdown.png
  signals.png
  report.md
  config.yaml
  backtest_IS.csv              # only when IS/OOS split is active
  equity_data_IS.csv           # only when IS/OOS split is active
  equity_curve_IS.png          # only when IS/OOS split is active
  drawdown_IS.png              # only when IS/OOS split is active
  signals_IS.png               # only when IS/OOS split is active
  report_IS.md                 # only when IS/OOS split is active
  monte_carlo/                 # only when --mc and >= 5 OOS trades
```

## Config Structure

`config/strategy_params.yaml` controls all strategy behaviour — no code changes needed to swap indicators, modes, or exit rules. Key sections:

```yaml
strategy:
  timeframe: "5min"
  trade_mode: "options"   # or "equities"
  signal_system: "..."    # "smi_wr", "ema_233", "armed_mode"
  initial_capital: 100000

signals:       # used by signal_system: smi_wr
  armed_mode: ...
  lookforward_mode: ...
  sync_window: ...
  vwap_filter: ...

armed_mode:    # used by signal_system: armed_mode
  arm_indicator: ...
  fire_indicator: ...
  sync_window: ...

options:
  target_dte: ...
  strike_selection: ...   # "ATM", "1_OTM", "1_ITM", "target_delta"
  target_delta: ...

exits:
  profit_target_pct: ...
  stop_loss_pct: ...
  eod_close: true
  opposite_signal: true

costs:
  commission_per_contract: 0.65   # IB rate
  slippage_per_contract: 0.10
```

See `config/strategy_params.yaml` for all current values.
