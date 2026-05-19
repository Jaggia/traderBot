---
tags: [options, strike-selection, occ, greeks, black-scholes]
---
# Deep Dive: Options Pipeline

How the engine selects a strike, builds an OCC symbol, computes Greeks, prices the contract, and manages exits. Covers the full lifecycle from signal to closed trade.

> Part of the [02-code-walkthrough.md](02-code-walkthrough.md) — covers Phase 5.

---

## Options Lifecycle

```
Signal (+1/-1)
    │
    ▼
build_option_position()                 src/options/entry_logic.py:12
    ├─ option_type = "C" if +1 else "P"
    ├─ select_strike()                   src/options/strike_selector.py:70
    │   ├─ get_target_expiry()           → nearest Friday (or Thursday if holiday)
    │   ├─ round_to_strike()             → $1 increments
    │   ├─ Apply offset (ATM/ITM/OTM)   or target_delta search
    │   └─ build_occ_symbol()            → "SYMBOL   260221C00451000"
    │
    ├─ dte_years(expiry, ts)             src/options/utils.py:4
    ├─ get_price_fn(raw_symbol, ...)     → observed market price from broker/Databento
    ├─ compute_greeks(S, K, T, sigma)    src/options/greeks.py:78
    └─ Position(...)                     src/options/position.py:7
         │
         ▼
    portfolio.open_position(pos)         → cash -= premium + modeled costs
         │
    [ ... bars pass, price updates each bar via _get_option_price() ... ]
         │
         ▼
    check_option_exit()                  src/options/exit_rules.py:9
         │ Returns exit reason or None
         ▼
    portfolio.close_position(pos, ...)   → P&L = (exit - entry) * qty * 100 - costs
```

---

## Strike Selection

**File:** `src/options/strike_selector.py:70`

```python
def select_strike(underlying_price, current_time, option_type, config) -> dict:
    # Returns: {"strike": float, "expiry": datetime, "raw_symbol": str}
```

### Step 1: Find expiry

```python
def get_target_expiry(current_date, target_dte):
    target = current_date + timedelta(days=target_dte)
    days_to_friday = (4 - target.weekday()) % 7
    expiry = target + timedelta(days=days_to_friday)
    if _is_nyse_holiday(expiry):
        expiry -= timedelta(days=1)  # roll back to Thursday
    return expiry
```

- `target_dte: 0` (current default) means same-day expiry, advancing to the next valid trading day if today is a weekend or NYSE holiday
- For `target_dte > 0`, finds the nearest Friday on or after `current_date + target_dte`
- Checks NYSE holiday calendar — if that Friday is Good Friday or similar, rolls to Thursday
- The holiday calendar (`_NYSEHolidayCalendar`) includes: New Year's, MLK, Presidents' Day, Good Friday, Memorial Day, Juneteenth, Independence Day, Labor Day, Thanksgiving, Christmas

### Step 2: Determine strike

**ATM / ITM / OTM modes:**

```python
atm_strike = round_to_strike(underlying_price, tick=1.0)  # SYMBOL uses $1 increments

# Offset logic (offset direction depends on option_type):
# ATM:   offset = 0
# 1_ITM: offset = +1 for calls, -1 for puts
# 1_OTM: offset = -1 for calls, +1 for puts
# 2_ITM: offset = +2 for calls, -2 for puts
# 2_OTM: offset = -2 for calls, +2 for puts

strike = atm_strike - offset if option_type == "C" else atm_strike + offset
```

The direction inversion is because ITM means **lower** strike for calls (below the underlying) and **higher** strike for puts.

**Target delta mode:**

```python
if selection == "target_delta":
    for off in range(-20, 21):
        candidate = atm_strike + off
        g = compute_greeks(S=underlying_price, K=candidate, T=dte_years, sigma=sigma, option_type=option_type)
        diff = abs(abs(g["delta"]) - target)
        if diff < best_diff:
            best_strike = candidate
```

Brute-force search over 101 strikes (ATM +/- 50), picks the one whose absolute delta is closest to `target_delta` (default 0.33).

### Step 3: Build OCC symbol

```python
def build_occ_symbol(underlying, expiry, option_type, strike):
    # ROOT(6) + YYMMDD + C/P + Strike*1000(8 digits)
    # "SYMBOL   260221C00451000"
    root = underlying.ljust(6)  # "SYMBOL   "
    exp = expiry.strftime("%y%m%d")  # "260221"
    strike_int = int(round(strike * 1000))  # 451000
    return f"{root}{exp}{option_type}{strike_int:08d}"
```

This is the standard OCC/OSI format. The symbol is used as a cache key for Databento option bar lookups.

---

## Greeks Computation (Black-Scholes)

**File:** `src/options/greeks.py:78`

```python
def compute_greeks(S, K, T, sigma, r=0.05, option_type="C") -> dict:
    # Returns: {"delta", "gamma", "theta", "vega", "price"}
```

Parameters:
- `S`: underlying price (e.g. 500.0)
- `K`: strike price (e.g. 499.0)
- `T`: time to expiry in years (e.g. 7/365 ≈ 0.019)
- `sigma`: implied volatility, annualized (default 0.25)
- `r`: risk-free rate (default 0.05)

Edge case: if `T <= 0` or `sigma <= 0`, returns intrinsic value and binary delta (1.0 if ITM, 0.0 if OTM).

The implementation:
1. Computes d1 and d2: `d1 = (ln(S/K) + (r + 0.5σ²)T) / (σ√T)`
2. Delta: `N(d1)` for calls, `N(d1) - 1` for puts
3. Gamma: `n(d1) / (Sσ√T)` (same for calls and puts)
4. Theta: daily (divided by 365)
5. Vega: per 1% vol move (divided by 100)

Greeks are snapshotted at entry and stored on the `Position` object. They are **not** updated during the trade — they appear in the trade log as entry Greeks for analysis.

---

## Option Pricing

**File:** `src/backtest/engine.py:67`

```python
def _get_option_price(self, underlying_price, strike, dte_years, option_type, raw_symbol, bar_time):
```

### Backtest pricing: market data only

1. Try `DatabentoOptionsLoader.load_option_bars(raw_symbol, start, end)`
2. For normal entry/marking, use the close of the most recent 1-minute bar at or before the bar timestamp
3. For intrabar stop/target checks, scan the 1-minute bars inside the 5-minute window and use the min low or max high
4. Reject stale data if the most recent bar is more than 30 minutes old

Backtests do **not** fall back to Black-Scholes anymore. Missing `raw_symbol`, no loader, empty data, or stale data all raise a hard error because the backtest is expected to use observed option prices only.

**Operational fix:** pre-download options data before running:
```bash
python scripts_py/download_options_databento.py <start> <end>
```

---

## The 100x Multiplier

Standard equity options represent 100 shares per contract. This multiplier is centralized in `Portfolio._notional()`:

```python
@staticmethod
def _notional(price, contracts, trade_mode):
    n = price * contracts
    if trade_mode == "options":
        n *= 100
    return n
```

All portfolio accounting methods (`open_position()`, `close_position()`, `mark_to_market()` via `_positions_value()`) delegate to `_notional()` — no inline `* 100` duplication.

Example: Buy 1 call at $5.00 → notional = $5.00 × 1 × 100 = $500. Sell at $6.00 → P&L = (6 - 5) × 1 × 100 = $100.

---

## Exit Priority Chain (Options)

**File:** `src/options/exit_rules.py:9`

```python
def check_option_exit(pos, signal, ts, profit_target_pct, stop_loss_pct, eod_close, opposite_signal_enabled):
```

Checked in order — first match wins:

| Priority | Condition | Reason String |
|----------|-----------|---------------|
| 1 | `pnl_pct <= -stop_loss_pct` | `"stop_loss"` |
| 2 | `pnl_pct >= profit_target_pct` | `"profit_target"` |
| 3 | Opposite signal (signal flipped) | `"opposite_signal"` |
| 4 | EOD (`hour >= 15 and minute >= 55`) | `"eod_close"` |
| 5 | Expiration (`ts.date() > expiry.date()`) | `"expiration"` |

The P&L percentage is computed on the **option price**, not the underlying:
```python
pnl_pct = (current_price - entry_price) / entry_price * 100
```

With `profit_target_pct: 20` and `stop_loss_pct: 20`, the option itself must move ±20% from its entry price.

---

## Position Dataclass

**File:** `src/options/position.py:7`

```python
@dataclass
class Position:
    direction: int          # +1 long, -1 short
    entry_price: float
    entry_time: datetime
    contracts: int
    trade_mode: str         # "equities" or "options"

    # Options-specific
    option_type: Optional[str]    # "C" or "P"
    strike: Optional[float]
    expiry: Optional[datetime]
    raw_symbol: Optional[str]     # OCC symbol for cache lookup

    # Greeks snapshot at entry
    delta, gamma, theta, vega: Optional[float]

    # Fixed stop/limit (equities only)
    stop_price: Optional[float]
    limit_price: Optional[float]

    # Live tracking
    current_price: float = 0.0
    high_water: float = 0.0
```

Methods:
- `unrealized_pnl()` — dollar P&L accounting for 100x multiplier and direction
- `pnl_pct()` — percentage P&L relative to entry (`direction` included)
- `update_price(price)` — updates `current_price` and `high_water`

The same `Position` dataclass is used for both equities and options. Options-specific fields are `None` for equity positions.
