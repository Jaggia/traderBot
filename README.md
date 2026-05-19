# Options & Equities Backtesting System

A sophisticated Python-based backtesting framework for SYMBOL options and equities trading strategies. This system generates trading signals using technical indicators (Stochastic Momentum Index + Williams %R), executes position management with Greek calculations, and generates comprehensive performance analytics.

## Features

- **Multi-instrument Trading**: Trade equities, options (calls/puts), or both simultaneously
- **Technical Signal Generation**: SMI (Stochastic Momentum Index) + Williams %R with configurable synchronization windows
- **Armed Mode**: First indicator arms the system; second fires and disarms — prevents stacking signals from a single event
- **Options Greeks Calculation**: Compute and track delta, theta, gamma, vega for options positions
- **Portfolio Management**: Track multiple concurrent positions with P&L calculation including costs/slippage
- **Flexible Exit Logic**: Profit target, stop loss, opposite signal, EOD close, and options expiration
- **Monte Carlo Simulation**: Bootstrap trade P&Ls to produce equity curve distributions and percentile bands
- **Live Paper Trading**: Databento streaming → Alpaca paper orders (`live_runner/run_live_db.py`) or IBKR streaming → IBKR paper orders (`live_runner/run_live_ibkr.py`)
- **Comprehensive Metrics**: Sharpe, Sortino, max drawdown, win rate, profit factor, and more
- **Visualization**: Equity curves, drawdown charts, and signal overlays on price
- **YAML Configuration**: Adjust all strategy parameters without code changes
- **Trade Logging**: Detailed CSV output of all trades with entry/exit reasons

## Project Structure

```
backTestingTraderBot/
├── requirements.txt
├── config/
│   └── strategy_params.yaml          # All strategy parameters
├── data/
│   ├── Alpaca/equities/SYMBOL/[1min|5min]/[YYYY]/
│   ├── DataBento/equities/SYMBOL/[1min|5min]/[YYYY]/
│   ├── DataBento/options/SYMBOL/1min/   # Cached option contract bars
│   └── TV/equities/SYMBOL/5min/
├── main_runner/
│   ├── run_backtest_db.py            # Databento backtest entry point
│   ├── run_backtest_with_alpaca.py   # Alpaca backtest entry point
│   ├── run_backtest_tv.py            # TradingView backtest entry point
│   └── run_monte_carlo.py            # Monte Carlo post-processor
├── live_runner/
│   ├── run_live_db.py                # Databento stream → Alpaca paper orders
│   └── run_live_ibkr.py              # IBKR stream → IBKR paper orders
├── scripts_bash/
│   ├── run_backtest_db.sh
│   ├── run_backtest_alpaca.sh
│   ├── run_backtest_tv.sh
│   └── run_mc.sh
├── scripts_py/
│   ├── dashboard.py                  # Streamlit results dashboard
│   ├── download_and_aggregate_databento.py
│   ├── download_options_databento.py # Options cache pre-warmer
│   └── armed_mode_comparison.py
├── src/
│   ├── data/
│   │   ├── alpaca_loader.py
│   │   ├── databento_loader.py
│   │   ├── aggregator.py             # 1-min → 5-min resampler
│   │   └── tradingview_loader.py
│   ├── indicators/
│   │   ├── smi.py
│   │   ├── williams_r.py
│   │   └── vwap.py
│   ├── signals/
│   │   └── smi_wr_generator.py
│   ├── options/
│   │   ├── greeks.py
│   │   ├── option_pricer.py
│   │   ├── strike_selector.py
│   │   └── position.py
│   ├── backtest/
│   │   ├── engine.py
│   │   └── portfolio.py
│   ├── analysis/
│   │   ├── metrics.py
│   │   ├── monte_carlo.py
│   │   └── visualize.py
│   ├── live/
│   └── utils/
│       └── logging_config.py
├── journal/
│   ├── INDEX.md
│   ├── log/                          # Chronological dev log
│   ├── decisions/                    # Key design rationale docs
│   ├── runbooks/                     # Operational how-to guides
│   ├── concepts/                     # Reference / educational docs
│   └── docs/                         # _state.md, _modules.md (living state)
├── tests/
│   ├── test_indicators.py
│   ├── test_greeks.py
│   ├── test_signals.py
│   └── test_portfolio.py
└── results/
    ├── db/                           # Databento results
    ├── alpaca/                       # Alpaca results
    ├── tv/                           # TradingView results
    └── others/                       # Comparison reports
```

## Installation

1. **Clone and navigate to the project:**
   ```bash
   cd backTestingTraderBot
   ```

2. **Create a virtual environment (recommended):**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # macOS/Linux
   # or
   venv\Scripts\activate  # Windows
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Set up environment variables (if using live data sources):**

   See `.env.example` for the full list. The app reads from your shell environment (not a `.env` file), so export these in `~/.zshrc` or equivalent:
   ```bash
   export DATA_BENTO_PW=your_databento_api_key_here
   export ALPACA_UN=your_alpaca_api_key_here
   export ALPACA_PW=your_alpaca_secret_key_here
   ```

## Quick Start

### 1. Configure Your Strategy

Edit `config/strategy_params.yaml` to customize:

```yaml
strategy:
  timeframe: "5min"              # 1min or 5min bars
  trade_mode: "equities"         # equities, options, or both
  initial_capital: 100000        # Starting portfolio value

signals:
  smi_fast: {period: 5, smooth1: 8, smooth2: 8}      # Fast SMI settings
  smi_slow: {period: 13, smooth1: 8, smooth2: 8}     # Slow SMI settings
  williams_r: {period: 13}                           # Williams %R lookback
  sync_window: 20                                    # Bars to sync indicators
  lookforward_mode: "wr_then_smi"    # wr_then_smi, smi_then_wr, or either
  vwap_filter: true                                 # Filter signals by VWAP

options:
  target_dte: 0                  # Days to expiration
  strike_selection: "ATM"        # ATM, 1_ITM, 1_OTM

exits:
  profit_target_pct: XX.X        # Take profit at +20%
  stop_loss_pct: XX.X            # Stop loss at -20%
  eod_close: false               # Close all positions at market close
  opposite_signal: true          # Exit on opposite signal

position:
  sizing_mode: "percent_of_equity"  # fixed or percent_of_equity
  sizing_pct: 50                    # % of equity per trade
  max_concurrent_positions: 1

costs:
  commission_per_contract: 0     # Per contract commission
  slippage_pct: 0                # Slippage as % of price
```

### 2. Run a Backtest

#### Data Source Selection

**Databento Data** (Default — closest to Interactive Brokers):
```bash
./scripts_bash/run_backtest_db.sh
# Or directly:
python main_runner/run_backtest_db.py 2025-11-10 2026-02-13
```
- Uses: `data/DataBento/equities/SYMBOL/5min/`
- Results: `results/db/`
- Includes: Automatic indicator warm-up (3 months prior)

**Alpaca Data**:
```bash
./scripts_bash/run_backtest_alpaca.sh
# Or directly:
python main_runner/run_backtest_with_alpaca.py 2025-11-10 2026-02-13
```
- Uses: `data/Alpaca/equities/SYMBOL/5min/`
- Results: `results/alpaca/`
- Includes: Automatic indicator warm-up (3 months prior)

**TradingView Data**:
```bash
./scripts_bash/run_backtest_tv.sh
# Or directly:
python main_runner/run_backtest_tv.py 2025-11-10 2026-02-13
```
- Uses: `data/TV/equities/SYMBOL/5min/2025-11-10-TO-2026-02-13.csv`
- Results: `results/tv/`
- No warm-up period (data already filtered to trading hours)

**Databento Equities Setup** (API Download + Aggregate):
```bash
# Download from API and aggregate to 5-min
python scripts_py/download_and_aggregate_databento.py 2018-05-01 2026-02-14
```
- **Setup required:** Export `DATA_BENTO_PW` environment variable
- **Process:** Downloads 1-min from API → aggregates to 5-min → saves monthly CSVs
- **Output:** `data/DataBento/equities/SYMBOL/1min/YYYY/` (1-min cache) and `data/DataBento/equities/SYMBOL/5min/YYYY/SYMBOL_5min_YYYYMM.csv` (aggregated)
- **Why aggregation:** Databento only offers 1-min bars for equities (XNAS.ITCH), aggregates to 5-min using validated rules
- **Validation:** Run `python scripts_py/validate_aggregator.py` (100% match vs Alpaca)

#### Data Source Comparison

| Source | Timeframe | Instrument | Status | Best For |
|--------|-----------|-----------|--------|----------|
| Databento (Default) | 1min → 5min (aggregated) | Equities | Requires download | Production, closest to IB |
| Alpaca (Cached) | 1min, 5min | Equities | ✓ Ready | Testing, development |
| TradingView (Cached) | 5min | Equities | ✓ Ready (2025-11 to 2026-02) | Chart validation |

### 3. Review Results

After backtesting, check `results/{db,alpaca,tv}/{Month-DD-YYYY}/{mode}/{timeframe}/`:

- **backtest.csv**: Detailed trade log with entry/exit prices, P&L, and exit reasons
- **report.md**: Summary of strategy configuration and performance metrics
- **config.yaml**: Full config snapshot at run time
- **equity_curve.png**: Portfolio value over time
- **drawdown.png**: Underwater plot showing drawdown periods
- **signals.png**: SYMBOL price with entry/exit signals overlaid
- **equity_data.csv**: Equity curve data used by the interactive dashboard
- **price_data.csv**: Close prices for the trading period (dashboard signals overlay)

### 4. Launch the Dashboard

```bash
streamlit run scripts_py/dashboard.py
```

Browse results across data sources and dates with 4 views:
- **Overview**: Metric cards, charts, config used, full report
- **Trade Explorer**: Interactive table with filters, P&L histogram, cumulative P&L
- **Comparison**: Armed mode comparison tables (from `results/others/`)
- **Cross-Run**: Metrics across all runs plotted over time

## Signal Generation Logic

### What Triggers a Long Signal (+1)?
1. **SMI Fast crosses above SMI Slow** AND
2. **Williams %R crosses above -80** AND
3. Both occur within `sync_window` bars
4. (Optional) Price > VWAP if `vwap_filter: true`

### What Triggers a Short Signal (-1)?
1. **SMI Fast crosses below SMI Slow** AND
2. **Williams %R crosses below -20** AND
3. Both occur within `sync_window` bars
4. (Optional) Price < VWAP if `vwap_filter: true`

### Lookforward Modes
- **`wr_then_smi`**: Williams %R must fire first; SMI crossover must follow within `sync_window` bars
- **`smi_then_wr`**: SMI crossover must fire first; Williams %R must follow within `sync_window` bars
- **`either`**: Either can fire first; the second must follow within `sync_window` bars

## Position Management

### Position Sizing
- **`fixed`**: Trade a fixed number of contracts per trade
- **`percent_of_equity`**: Size each trade as a % of current portfolio value

### Exit Rules
1. **Profit Target**: Close when position reaches +X% gain (equities: intrabar high/low; options: current market price)
2. **Stop Loss**: Close when position reaches -X% loss (equities: intrabar high/low; options: current market price)
3. **Opposite Signal**: Close when opposite trading signal fires
4. **End-of-Day**: Auto-close all positions at market close (if enabled)
5. **Expiration**: Options automatically closed when `current_date >= expiry` (0-DTE closes at EOD)

### Options Greeks
For options trades, the system calculates:
- **Delta**: Directional sensitivity to underlying price
- **Theta**: Daily time decay value
- **Gamma**: Rate of delta change
- **Vega**: Sensitivity to volatility

Realized P&L includes time decay (theta) for accurate options P&L.

## Data Sources

The system supports multiple equity and options data sources:

| Source | Timeframe | Instrument | Format | Location |
|--------|-----------|-----------|--------|----------|
| Alpaca API | 1min, 5min | Equities | OHLCV | `data/Alpaca/equities/SYMBOL/[timeframe]/[year]/[month].csv` |
| TradingView | 5min | Equities | OHLCV | `data/TV/equities/SYMBOL/5min/2025-11-10-TO-2026-02-13.csv` |
| Databento | 1min (aggregated to 5min) | Equities | OHLCV | `data/DataBento/equities/SYMBOL/[timeframe]/[year]/[month].csv` |
| Databento API | 1min | Options | OHLCV | `data/DataBento/options/SYMBOL/1min/{OCC_SYMBOL}.csv` (one file per contract) |

**CSV caching:** All data is cached locally to avoid repeated API calls and reduce Databento credits usage.

### Setting Up Databento Equities

To download and use Databento equity data via API:

**1. Get an API key:**
   - Sign up at [databento.com](https://databento.com)
   - Create a Historical API key for equities

**2. Set environment variable:**
   ```bash
   export DATA_BENTO_PW="your_api_key_here"
   ```

**3. Download and aggregate (single command):**
   ```bash
   # Downloads 1-min bars from XNAS.ITCH API and aggregates to 5-min
   python scripts_py/download_and_aggregate_databento.py 2018-05-01 2026-02-14
   ```
   **What happens:**
   - Downloads 1-min OHLCV bars via API (cached in `data/DataBento/equities/SYMBOL/1min/YYYY/`)
   - Aggregates using validated aggregator (`src/data/aggregator.py`)
   - Saves as monthly CSVs (`data/DataBento/equities/SYMBOL/5min/YYYY/SYMBOL_5min_YYYYMM.csv`)

**4. Validate aggregator (optional but recommended):**
   ```bash
   # Compares aggregated Databento data to native Alpaca 5-min
   python scripts_py/validate_aggregator.py
   # Should exit 0 (100% match within $0.01 tolerance)
   ```

**5. Use in backtests:**
   ```yaml
   # Edit config/strategy_params.yaml
   data:
     data_source: "databento"
     databento_equities_dir: "data/DataBento/equities/SYMBOL/5min"
   ```
   Then run: `./scripts_bash/run_backtest_db.sh` or `python main_runner/run_backtest_db.py 2018-05-01 2026-02-14`

**Script Usage:**

```bash
# Download for a specific date range
python scripts_py/download_and_aggregate_databento.py 2018-05-01 2026-02-14

# Download with defaults (2018-05-01 to 2026-02-14)
python scripts_py/download_and_aggregate_databento.py

# You can also update defaults in the script if running frequently
```

**Note:** The API-only approach ensures proper timestamp handling and data consistency. Browser downloads of Databento CSV files may have timestamp issues (timezone mishandling, formatting inconsistencies). Using the API directly guarantees reliable, correctly-formatted data.

**Why aggregate?** Databento's XNAS.ITCH feed only offers 1-minute bars for equities. The aggregator resamples to 5-minute bars using standard OHLCV rules (open: first, high: max, low: min, close: last, volume: sum). The aggregator is **production-validated** against Alpaca data with perfect matching.

**Library reference:**
- `src/data/databento_loader.py` — API functions (download, load, cache)
- `src/data/aggregator.py` — Pure aggregator function (no I/O)
- `scripts_py/download_and_aggregate_databento.py` — User-facing CLI

## Performance Metrics

The backtest engine computes:

- **Win Rate (%)**: % of trades that finished with profit
- **Sharpe Ratio**: Risk-adjusted return (annualized)
- **Max Drawdown (%)**: Largest peak-to-trough decline
- **Profit Factor**: Gross profit / gross loss
- **Average Win/Loss**: Mean profit/loss per trade
- **Total P&L**: Sum of all trade P&L
- **Total Return (%)**: Final equity / initial capital - 1

## Testing

Run the test suite to validate indicators and logic:

```bash
# Run all tests
pytest tests/

# Run specific test file
pytest tests/test_indicators.py -v

# Run with coverage
pytest --cov=src tests/
```

Test coverage includes:
- SMI, Williams %R, VWAP calculations
- Greeks calculations
- Signal generation logic
- Portfolio tracking
- Position sizing

## Common Workflows

### Options Data: Pre-Download vs On-the-Fly

The pre-download script (`scripts_py/download_options_databento.py`) is **optional**. If you skip it, the backtest downloads option contract bars from Databento on the fly during the loop and caches them — so it still works correctly.

```bash
# Optional: pre-warm the cache before a backtest
python scripts_py/download_options_databento.py 2025-11-10 2026-02-13

# Then run the backtest (cache-only, no API calls during the loop)
python main_runner/run_backtest_db.py 2025-11-10 2026-02-13
```

**What the pre-download does:**
- Computes signals for the date range (same logic as the backtest)
- For each signal bar, resolves the exact OCC contract that would be traded
- Downloads the full trading day (09:30–16:00) of 1-min bars for that contract
- Deduplicates: one download per (contract, date) regardless of how many signals hit it

**Cost comparison:** identical — Databento charges for data downloaded, not API calls. Pre-download just moves the same network work before the backtest starts.

**When to bother:**
- New date range you haven't run before → pre-download if you want speed/clean output
- Re-running same range with tweaked config → skip it, everything's already cached

### Monte Carlo Simulation

Bootstrap trade P&Ls to get a distribution of equity curve outcomes:

```bash
# Inline — runs automatically after the backtest
python main_runner/run_backtest_db.py 2025-11-10 2026-02-13 --mc

# Post-hoc — run on any existing results folder
python main_runner/run_monte_carlo.py results/db/February-27-2026/options/5min
python main_runner/run_monte_carlo.py results/db/February-27-2026/options/5min --n 2000
```

Outputs to `{results_dir}/monte_carlo/`: `mc_equity_fan.png`, `mc_distributions.png`, `mc_report.md`, `mc_metrics.csv`.

### Optimize Parameters
1. Edit `config/strategy_params.yaml` (e.g., change indicator periods)
2. Run `./scripts_bash/run_backtest_db.sh` (or any source runner)
3. Review metrics in `results/db/{Month-DD-YYYY}/{mode}/{timeframe}/report.md` or via dashboard
4. Repeat with different configs

### Validate on Recent Data
```bash
python main_runner/run_backtest_db.py 2025-11-01 2026-02-14
```

### Compare Timeframes
```bash
# Edit strategy_params.yaml: timeframe: "1min" or "5min"
# Then run with any data source
./scripts_bash/run_backtest_db.sh
# Compare via dashboard Cross-Run view
```

### Analyze Single Trade
Open the generated `backtest.csv` in the results folder or use the Trade Explorer dashboard view. Filter by timestamp to find:
- Entry price and time
- Exit price, time, and reason
- P&L and P&L %
- Position type (equity/call/put)

## Architecture Overview

```
Data Loading
    ↓
Indicator Calculation (SMI, Williams %R, VWAP)
    ↓
Signal Generation (crossovers within sync window)
    ↓
Backtesting Loop (bar-by-bar)
    ├── Entry Logic: Signal + sizing
    ├── Price Update: Mark-to-market positions
    ├── Exit Logic: Profit target / stop loss / opposite signal
    └── P&L Tracking: Realized + unrealized
    ↓
Portfolio Analytics
    ├── Trade-level metrics
    ├── Equity-curve statistics
    └── Risk metrics (Sharpe, drawdown)
    ↓
Reporting & Visualization
```

## Troubleshooting

### No trades generated?
- Check `config/strategy_params.yaml` signal parameters
- Verify indicator values exist (check warmup period)
- Increase `sync_window` to allow more flexibility
- Disable `vwap_filter` to remove that constraint

### Data not loading?
- **Alpaca:** Ensure CSV files exist in `data/Alpaca/equities/SYMBOL/5min/`
- **TradingView:** Ensure `data/TV/equities/SYMBOL/5min/2025-11-10-TO-2026-02-13.csv` exists
- **Options:** Ensure CSV files exist in `data/DataBento/options/SYMBOL/[timeframe]/`
- Check file naming: `SYMBOL_5min_YYYYMM.csv` (Alpaca) or `2025-11-10-TO-2026-02-13.csv` (TradingView)
- Verify timestamps are timezone-aware

### Memory issues with large date ranges?
- Reduce date range and run multiple backtests
- Use 5-minute bars instead of 1-minute
- Clear `__pycache__/` directories

### Options data unavailable?
- Ensure `DATA_BENTO_PW` is exported in your shell (`~/.zshrc`) — the app does not load `.env` files
- Check API key has options data permissions
- System will fall back to Black-Scholes pricing

## Dependencies

Key libraries:
- **pandas/numpy**: Data manipulation and numerical computation
- **scipy**: Black-Scholes Greeks calculation (`norm.cdf`, `norm.pdf`)
- **pyyaml**: Configuration file parsing
- **matplotlib/plotly**: Visualization
- **pytest**: Testing framework
- **streamlit**: Results dashboard UI
- **alpaca-py**: Alpaca paper trading (live runner)
- **databento**: Market data streaming and historical options bars

See `requirements.txt` for complete list with versions.

## Contributing

To extend the system:

1. **New Indicators**: Add to `src/indicators/` and import in `smi_wr_generator.py`
2. **New Signal Logic**: Modify `generate_signals()` in `src/signals/smi_wr_generator.py`
3. **New Exit Rules**: Update `BacktestEngine.run()` in `src/backtest/engine.py`
4. **New Metrics**: Add to `compute_metrics()` in `src/analysis/metrics.py`

Always add tests in `tests/` for new functionality.

## License

This project is provided as-is for educational and research purposes.

## Author Notes

The name "backTestingTraderBot" means "What are we doing first?" in French—a fitting name for a backtesting framework that explores "what if" scenarios before risking real capital.