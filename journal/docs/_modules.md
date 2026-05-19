---
tags: [living-state, modules, implementation-notes, current]
---
# Module Notes

## src/indicators/
- **`base.py`**: Shared primitives for indicator calculation.
  - `rolling_high_low(df, period)` → `(high_roll, low_roll)`
  - `double_ema_smooth(series, span1, span2)` → double-smoothed Series
- `smi.py`: `compute_smi(df, period, smooth1, smooth2)` → SMI Series (-100 to +100). Uses `base.py` helpers.
- `williams_r.py`: `compute_williams_r(df, period)` → Williams %R Series (-100 to 0). Uses `rolling_high_low`.
- `vwap.py`: `compute_vwap(df)` → VWAP with daily reset.
- `src/indicators/__init__.py`: exports all core compute functions.

## src/utils/
- **`time_utils.py`**: Time-related helpers.
  - `get_market_hours_window(bar_time)` → `(day_start, day_end)` (09:30–16:00 EST).

## src/signals/strategy.py — Signal Strategy Pattern
- `SignalStrategy` ABC: abstract methods `compute_indicators(df, config)` and `generate_signals(df, config)`.
- `IndicatorPairStrategy`: delegates to `indicator_pair_pipeline`. (System 1).
- `Ema233Strategy`: delegates to `indicator_pair_pipeline`. (System 2).
- `TriggerChainStrategy`: delegates to `indicator_pair_pipeline`. (System 3).
- `create_strategy(config) -> SignalStrategy`: factory; reads `config["strategy"]["signal_system"]`.

## src/signals/indicator_pair_pipeline.py — Unified Signal Flow
- Centralises signal composition for all systems.
- Supports `indicator_pair`, `ema_233`, `armed_mode`, and `trigger_chain` configurations.
- Handles internal resampling (for EMA), event detection (crossovers), and trigger-chain logic.

## src/backtest/trade_logic.py — Shared Entry/Exit Ruleset
- `BarContext` (frozen dataclass): immutable snapshot of one bar.
- `ExitConfig` (frozen dataclass): immutable exit-rule config.
- `check_exit(pos, bar, config, get_option_price=None)`: evaluates all exit conditions in priority order.
- `build_entry(signal, bar, contracts, ...)`: constructs a new `Position`.

## src/backtest/engine.py — BacktestEngine
- Bar-by-bar loop using pre-extracted numpy arrays.
- `_get_option_price()`: fetches option bars from loader, uses `get_market_hours_window()`.
- `run()`: executes `pending_entry` (next-bar-open fill), checks exits, buffers signals.

## src/backtest/portfolio.py — Portfolio
- `_notional()`: handles 100x multiplier for options.
- `_transaction_cost()`: commission + slippage model.
- `record_initial_equity()`: t=0 baseline.

## src/options/strike_selector.py
- `build_occ_symbol()`: OSI-compliant symbols.
- `get_target_expiry()`: Friday roll logic.
- `select_strike()`: ATM tick rounding + moneyness offset.

## src/analysis/metrics.py
- **`_psr()`**: Probabilistic Sharpe Ratio (López de Prado).
- **`_dsr()`**: Deflated Sharpe Ratio — PSR with multiple-testing correction (journal 029).
- **`_norm_ppf(p)`**: Inverse standard normal CDF (Acklam rational approximation, ~1e-9 accuracy).
- **`_expected_max_sharpe(n_trials)`**: E[max SR] under the null (Bailey & López de Prado 2014).
- **`count_trials(run_key_path)`**: Counts distinct variants in `run_key.yaml` for DSR.
- **`trade_sharpe`**: Trade-level Sharpe Ratio (honest for episodic strategies).
- `_compute_monthly_returns()`: Resampling helper for TV-aligned Sharpe/Sortino.
- `compute_metrics()`: primary entry point for analytics. Accepts `n_trials` param for DSR.
- `save_report_md()`: markdown report generation.

## src/data/provider.py — Data Provider (journal 028)
- `DataProviderProtocol`: runtime-checkable Protocol with `load_equity_data`, `ensure_data`, `get_source_name`, `should_trim_end`.
- `_DatabentoProvider`, `_AlpacaProvider`, `_TradingViewProvider`: private concrete wrappers.
- `create_provider(config)`: factory; reads `config["data"]["data_source"]`.

## main_runner/base_runner.py — BaseBacktestRunner
- Results path: `results/{source}/{start}_to_{end}_run-{date}/{mode}/{timeframe}/`.
- IS/OOS split management.
- Dashboard data exports (`price_data.csv`).
- Uses `create_provider()` for data loading (journal 028).
- Calls `count_trials()` to pass `n_trials` to `compute_metrics()` for DSR (journal 029).

## scripts_py/dashboard.py
- Streamlit app for browsing results.
- `view_overview()`: metric cards + charts.
- `view_trade_explorer()`: deep dive into individual fills.
