---
tags: [living-state, config, todo, live-runner, current]
---
# Project State

Last updated: 2026-04-12

## Active Config (`config/strategy_params.yaml`)
- `trade_mode: "options"` (0-DTE)
- Signal system, lookforward mode, armed mode, VWAP filter: see `config/strategy_params.yaml`
- TP/SL, `eod_close`, `opposite_signal`: see `config/strategy_params.yaml`
- `initial_capital: 100000`, `sizing_mode: "fixed"`, `contracts_per_trade: 1`
- `data_source: "databento"` (default)
- Strike selection, target delta, BS fallback sigma: see `config/strategy_params.yaml`
- `commission_per_contract: 0.65` (IB rate), `slippage_per_contract: 0.10` (flat per contract, models 0-DTE spread)
- `live: warmup_bars: 200`, `ibkr_host: "127.0.0.1"`, `ibkr_port: 4002`, `ibkr_streamer_client_id: 1`, `ibkr_trader_client_id: 2`

## Results Directory Structure
`results/{db,alpaca,tv}/{start}_to_{end}_run-{date}/{mode}/{timeframe}/`
e.g. `results/db/February-24-2026_to_February-28-2026_run-February-27-2026/equities/5min/`
Primary files: `backtest.csv`, `report.md`, `equity_curve.png`, `drawdown.png`, `signals.png`, `config.yaml`, `equity_data.csv`, `price_data.csv`
IS files (when `is_fraction > 0`): `backtest_IS.csv`, `report_IS.md`, `equity_curve_IS.png`, `drawdown_IS.png`, `signals_IS.png`, `equity_data_IS.csv`

## TODO Items
1. [ ] **Options end-to-end integration** — run full backtest with mocked price data; verify trade log P&L against hand calculations.
2. [ ] **Pre-download hold period** — expand `download_options_databento.py` to cover multi-day holds (entry date + expected hold duration).
3. [x] ~~DSR (Deflated Sharpe Ratio)~~ DONE (journal 029) — implemented `_dsr()`, `_norm_ppf()`, `_expected_max_sharpe()`, `count_trials()`.
4. [ ] **Deep Module: Options Lifecycle** — consolidate shallow options files into one module owning the lifecycle from selection to expiry.
5. [x] ~~Deep Module: Data Provider~~ DONE (journal 028) — unified behind `DataProviderProtocol` with `create_provider()` factory.

Full history of completed items: see `TODOs/TODO_DONE.md`.

## Done Items
1. ~~Lookforward mode~~ DONE
2. ~~Target-delta strike selection~~ DONE (`strike_selection: "target_delta"`)
3. ~~Forward test with Alpaca paper account~~ DONE (`live_runner/run_live_db.py`, journal 008)
4. ~~IS/OOS split architecture~~ DONE (`is_fraction` config, journal 012)
5. ~~Indicator Primitives Refactor~~ DONE (journal 027) — consolidated `rolling_high_low` and `double_ema_smooth` into `base.py`.
6. ~~Advanced Analytics~~ DONE (journal 027) — added Trade-level Sharpe Ratio and Probabilistic Sharpe Ratio (PSR).
7. ~~DSR (Deflated Sharpe Ratio)~~ DONE (journal 029) — multiple-testing-corrected PSR via `_dsr()`.
8. ~~Deep Module: Data Provider~~ DONE (journal 028) — `DataProviderProtocol` + `create_provider()` factory.

## Venv Path
Local — set your own path (e.g. `./venv/bin/activate` or `VENV_PYTHON` env var)
Current project venv: `./venv_stonkerino/`

## Live Runners

### Databento + Alpaca (original)
- Entry point: `python live_runner/run_live_db.py` or `./scripts_bash/run_live.sh`
- Streams Databento XNAS.ITCH ohlcv-1m → aggregates to 5-min → same signal pipeline → Alpaca paper orders
- Env vars: `DATA_BENTO_PW`, `ALPACA_UN`, `ALPACA_PW` (already in ~/.zshrc)

### IBKR (new — journal 021, tested 2026-04-05)
- Entry point: `python live_runner/run_live_ibkr.py` or `./scripts_bash/run_live_ibkr.sh`
- Streams 1-min bars from IB Gateway → aggregates to 5-min → same signal pipeline → IBKR paper orders
- No env vars required — uses local socket connection (IB Gateway must be running on port 4002)
- Requires: IB Gateway installed, logged in with paper account, API socket enabled
- Tested working: 2026-04-05 (socket connection, warmup load, streaming confirmed)

### Common to both
- Warmup bars loaded from local Databento cache (free); EOD auto-close at 15:55; Ctrl+C closes position cleanly
- Intrabar polling: daemon thread polls option mid-price every 30s, exits on stop/target breach (journal 017)
- Crash recovery: `reconcile_positions()` queries broker on startup, resumes tracking orphaned positions (journal 017)
- **Live data logging (journal 023):** All 5-min bars and closed trades automatically saved to `results/live/{YYYY-MM-DD_HHMMSS}/` (live_bars.csv, live_trades.csv) for later analysis and re-backtesting

## Data Source Architecture (Final Decision — journal 023)

**Backtesting (strategy development):** Databento XNAS.ITCH (highest quality, full options support, bulk download efficient)
**Live trading (paper/forward-test):** IBKR (live feed, zero API cost for paper, socket-based no rate limits)
**Trade-off:** Backtest ≠ live due to different data sources — this is expected and acceptable. Validates edge robustness. Recommended next step: forward-test IBKR-only once system is proven (requires building IBKR historical loader, currently not priority).

## Options Data Flow
- `select_strike()` constructs OCC symbol directly (no API) → `DatabentoOptionsLoader` fetches 1-min bars (cached locally) → `_get_option_price()` finds nearest bar by timestamp
- Pre-download script: `scripts_py/download_options_databento.py START END`
- Cache dir: `data/DataBento/options/{SYMBOL}/1min/` (default: SYMBOL)
- API key env var: `DATA_BENTO_PW`

## Signal Generation
- Four signal systems available: `indicator_pair`, `ema_233`, `armed_mode`, `trigger_chain` — selected via `strategy.signal_system` in config
- `indicator_pair` (System 1): SMI + Williams %R (or any two indicators)
- `ema_233` (System 2): EMA crossover on resampled 15-min bars
- `armed_mode` (Legacy System 3): Arm/fire logic for indicator pairs
- `trigger_chain` (New System 3): Sequential trigger chain (1..N indicators)
- All parameters configurable in `config/strategy_params.yaml`

## Backtest Engine
- Pre-extracts numpy arrays for hot loop (speed)
- `trade_start_idx` separates warm-up from trading period (3-month warm-up)
- `oos_start_idx`: bar index where OOS begins; used by runner to split IS vs OOS trade log
- **Next-bar-open fill (A-1):** signal at bar[i] → `pending_entry` buffer → fills at bar[i+1].open (no lookahead bias)
- **Initial equity baseline (L-4):** `record_initial_equity()` inserts t=0 point before first bar
- **Final re-record (L-1):** `mark_to_market(last_ts)` after `backtest_end` closes → final curve point reflects realized cash
- Exit priority: stop/limit intrabar hi/lo (equities) → `check_option_exit()` (options) → opposite_signal → eod_close
- Entry: `build_option_position()` injects pricing fn as lambda — data-source agnostic
- Options entered with `direction=signal` (+1 call, -1 put); `option_type` encodes bullish/bearish
- Exit logic: `src/options/exit_rules.check_option_exit()`; entry: `src/options/entry_logic.build_option_position()`
- Options P&L: 100× multiplier (standard contract)
- Equity curve length = `len(trading_bars) + 2` (initial baseline + per-bar MTM + final re-record)

## IS/OOS Split (`backtest.is_fraction`)
- `is_fraction: 0.0` (default) → no split, all output is single OOS run (identical to before)
- `is_fraction: 0.7` → first 70% of trading period is IS, last 30% is OOS
- Runner prints both IS (labeled "not for evaluation") and OOS (labeled "valid performance") metrics
- Primary output files always reflect OOS; IS files saved with `_IS` suffix when split is active
- `oos_start` passed to engine; `engine.oos_start_idx` available for downstream callers

## Indicator Pipeline
- `compute_indicators()` in `src/signals/indicator_pair_pipeline.py` calls standalone functions:
  - `compute_smi(df, period, smooth1, smooth2)`
  - `compute_williams_r(df, period)`
  - `compute_vwap(df)`
  - `compute_rsi(df, period)`
  - `compute_macd(df, fast, slow, signal)`
- **`src/indicators/base.py`** provides shared primitives: `rolling_high_low` and `double_ema_smooth`.

## Signal Strategy Pattern (`src/signals/strategy.py`)
- `SignalStrategy` ABC with two abstract methods: `compute_indicators(df, config)` and `generate_signals(df, config)`
- `IndicatorPairStrategy`: delegates to `indicator_pair_pipeline.compute_indicators` / `generate_signals` (System 1)
- `Ema233Strategy`: delegates to `indicator_pair_pipeline.compute_indicators` / `generate_signals` (System 2)
- `ArmedModeStrategy`: delegates to `indicator_pair_pipeline.compute_indicators` / `generate_signals` (legacy compatibility)
- `TriggerChainStrategy`: delegates to `indicator_pair_pipeline.compute_indicators` / `generate_signals` (System 3)
- `create_strategy(config)`: factory function; reads `config["strategy"]["signal_system"]` (default `"indicator_pair"`)

## Testing
- Total: **911 tests, all passing** (journal 027).
- **VWAP manual-calculation verification (journal 027):** added `tests/indicators/test_vwap_manual.py` asserting daily reset and zero-volume ffill logic.
