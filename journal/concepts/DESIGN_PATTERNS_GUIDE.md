---
tags: [design-patterns, template-method, adapter, strategy-pattern, pipeline]
---
# Concept: Design Patterns In This Codebase

This codebase uses a small number of patterns repeatedly. The goal is not pattern purity; the goal is keeping backtest and live behavior aligned while letting data sources and brokers vary.

## Template Method

**Where:** `main_runner/base_runner.py`

`BaseBacktestRunner.run()` owns the fixed orchestration:
- load config
- validate CLI dates
- compute warmup start
- optional pre-load step
- load data
- trim/split data
- run the engine
- compute analytics
- save outputs

Concrete runners only override the source-specific hooks:
- `run_backtest_db.py`
- `run_backtest_with_alpaca.py`
- `run_backtest_tv.py`

Why it fits:
- the workflow is fixed
- only data acquisition differs
- outputs stay uniform across sources

## Adapter / Protocol Boundary

**Where:** `src/live/broker_protocol.py`, `src/live/alpaca_trader.py`, `src/live/ibkr_trader.py`

`LiveEngine` works against a broker-shaped interface instead of broker-specific SDK code. Alpaca and IBKR expose the same operational methods:
- `buy_option()`
- `sell_option()`
- `get_option_mid_price()`
- `get_order_status()`
- `get_option_positions()`
- `cancel_all_orders()`

Why it fits:
- live engine logic stays broker-agnostic
- IBKR support did not require rewriting signal or exit logic
- runtime tests can assert both traders satisfy the same protocol

## Signal Strategy (GoF Strategy)

**Where:** `src/signals/strategy.py`

`SignalStrategy` is an abstract base class with two methods: `compute_indicators(df, config)` and `generate_signals(df, config)`. Concrete strategies implement both:

- `SmiWrStrategy` — delegates to `smi_wr_pipeline.py` (System 1)
- `Ema233Strategy` — delegates to `ema_pipeline.py` (System 2)

`create_strategy(config)` reads `config["strategy"]["signal_system"]` and returns the right instance. The engine receives a strategy object at construction time and calls through it — it never imports individual pipeline functions directly.

Why it fits:
- adding a third signal system requires zero changes to the engine
- tests inject a `MockStrategy` (in `tests/conftest.py`) without patching internal imports
- the dispatch point is explicit and validated (unknown keys raise `ValueError`)

**Config key:** `strategy.signal_system: "smi_wr"` (default) or `"ema_233"`

## Functional Pipeline

**Where:** `src/signals/smi_wr_pipeline.py`, `src/signals/ema_pipeline.py`

Each signal system is written as a pure data pipeline:
1. `compute_indicators(df, config)` — add indicator columns to the DataFrame
2. `generate_signals(df, config)` — derive events and emit a `signal` series

The two-function contract is the same for both systems. `SmiWrStrategy` and `Ema233Strategy` wrap the respective pipelines and expose this interface to the engine.

Why it fits:
- backtest and live both call the same functions
- indicator logic is easy to compare against external references
- stateful behavior is isolated per pipeline (`_apply_armed_logic()` in SMI/WR)

## Shared Domain Logic Module

**Where:** `src/backtest/trade_logic.py`

`check_exit()` and `build_entry()` centralize trade decisions that used to live inline in the backtest loop. Both `BacktestEngine` and `LiveEngine` call into this module.

Why it fits:
- one ruleset for backtest and live
- easier regression testing around fills, exits, and ordering
- hot-loop code stays thinner

## Strategy By Configuration

**Where:** `config/strategy_params.yaml`

Most runtime behavior is selected through config rather than subclassing:
- `signal_system` — which signal strategy to run (System 1 or 2)
- `trade_mode`
- `lookforward_mode`
- `armed_mode`
- `vwap_filter`
- strike selection mode
- exits
- sizing
- costs

Why it fits:
- faster experimentation
- output artifacts can snapshot exact settings
- fewer branches hidden in runner code

## Lazy Resource Initialization

**Where:** `src/backtest/engine.py`

The options loader is initialized only when the run is actually in options mode and the first option price lookup needs it.

Why it fits:
- equities backtests avoid unnecessary setup
- options runs can work in API-backed or cache-only mode
- expensive dependencies are pushed to the edge

## What Is Not Here

Some classic patterns are intentionally absent:
- no DI container
- no event bus inside the backtester
- no abstract factory for brokers or data sources
- no indicator class hierarchy

The codebase mostly prefers direct functions and small adapters over heavier abstraction.
