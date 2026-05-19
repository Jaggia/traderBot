---
tags: [tutorial, hmm, implementation, blueprint, integration]
---
# Part 5: Our Implementation — How to Wire an HMM into This Codebase

[< Part 4: Pitfalls](04-pitfalls.md) | [Index](00-index.md)

---

This part is a blueprint. It does not describe code that currently exists — it describes exactly how you would add HMM regime filtering to the existing architecture, including which files to create, which to modify, the training protocol, and the decision framework for evaluating whether it actually helps.

---

## The Goal

Add a **regime filter** that runs during indicator computation, outputs a per-bar regime label and probability to the DataFrame, and gates signal generation so that SMI + Williams %R signals only fire when the HMM believes the market is in a trending regime.

The design principle: **the HMM is a column added to the DataFrame, not a rewrite of any existing logic.** The engine, trade logic, and strategy pattern remain untouched.

---

## File Map

| New/Modified | File | Role |
|---|---|---|
| New | `src/indicators/regime_hmm.py` | HMM training, forward filtering, state labeling |
| New | `src/indicators/tests/test_regime_hmm.py` | Unit tests |
| Modified | `src/signals/indicator_pair_pipeline.py` | Call regime HMM in `compute_indicators()`, gate signal in `generate_signals()` |
| Modified | `config/strategy_params.yaml` | Add `hmm:` block |

No changes to `src/backtest/engine.py`, `src/backtest/trade_logic.py`, `src/signals/strategy.py`, or any live runner.

---

## Step 1: Feature Selection

Choose 2–4 features that characterize market regimes well and are already computed in the pipeline.

**Recommended feature set (start with these):**

```python
# At each 5-min bar, extract:
features = [
    "bar_return",        # (close - open) / open — direction and magnitude
    "bar_vol",           # high - low (intrabar range) — noise/volatility proxy
    "smi_fast",          # already computed in compute_indicators()
    "williams_r",        # already computed in compute_indicators()
]
```

**Why these four:**
- `bar_return` + `bar_vol` together capture the *character* of price movement (directional low-vol = trending, oscillating high-vol = choppy)
- `smi_fast` and `williams_r` provide the indicator-level view — in a trending regime, these will be correlated with direction; in a choppy regime, they'll be oscillating

**What NOT to include:**
- VWAP (intraday-only, resets each day — this creates a daily discontinuity that confuses the HMM)
- Absolute price levels (the HMM needs stationary features)
- Future information (obviously)

**Normalize your features.** HMMs with Gaussian emissions are sensitive to scale. Apply `StandardScaler` fitted on the training data:

```python
from sklearn.preprocessing import StandardScaler
scaler = StandardScaler()
X_train_scaled = scaler.fit_transform(X_train)
X_live_scaled = scaler.transform(X_live)  # use training scaler — do NOT refit on live data
```

---

## Step 2: Training Protocol (Avoiding Look-Ahead Bias)

```
Full data timeline:
├── Warm-up period (3 months before backtest start)
│    ↑
│    This is already loaded by run_backtest_db.py for indicator warm-up.
│    Use it for HMM training too.
│
└── Backtest period
     ↑
     Apply forward filter bar-by-bar using the trained model.
     Never retrain during the backtest period.
```

**In code:**

```python
# In src/indicators/regime_hmm.py

def train_hmm(df_warmup: pd.DataFrame, n_states: int, features: list[str]) -> tuple:
    """
    Train HMM on warm-up data. Returns (model, scaler, state_labels).
    
    Parameters
    ----------
    df_warmup : pd.DataFrame
        The warm-up DataFrame (bars BEFORE the backtest start date).
    n_states : int
        Number of hidden states (2 or 3).
    features : list[str]
        Column names of features to use.
        
    Returns
    -------
    model : hmmlearn.hmm.GaussianHMM
        Trained model.
    scaler : sklearn.preprocessing.StandardScaler
        Fitted scaler (must be used to transform backtest data).
    state_labels : dict[int, str]
        Maps state index → regime label, e.g. {0: "trending", 1: "choppy"}.
    """
    from hmmlearn import hmm
    from sklearn.preprocessing import StandardScaler
    
    X = df_warmup[features].dropna().values
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    
    model = hmm.GaussianHMM(
        n_components=n_states,
        covariance_type="diag",  # fewer params, more stable
        n_iter=200,
        random_state=42,
    )
    model.fit(X_scaled)
    
    # Label states by mean return of their emission distribution
    # (feature 0 = bar_return — the first feature in your list)
    return_idx = features.index("bar_return")
    state_means = model.means_[:, return_idx]
    # Sort: highest mean return = "trending_up", lowest = "trending_down", middle = "choppy"
    order = np.argsort(state_means)[::-1]  # descending
    if n_states == 2:
        state_labels = {order[0]: "trending", order[1]: "choppy"}
    else:  # 3 states
        state_labels = {order[0]: "trending_up", order[1]: "choppy", order[2]: "trending_down"}
    
    return model, scaler, state_labels


def apply_forward_filter(
    df: pd.DataFrame,
    model,
    scaler,
    state_labels: dict,
    features: list[str],
) -> pd.DataFrame:
    """
    Run the HMM forward filter bar-by-bar (no lookahead).
    Adds columns: hmm_state (string label), hmm_trending_prob (float 0-1).
    """
    X = df[features].values
    X_scaled = scaler.transform(X)
    
    # predict_proba uses the forward-backward algorithm on the full sequence —
    # this has look-ahead bias! Use the manual forward filter instead.
    # hmmlearn's score_samples() also uses forward-backward.
    # We need to implement the forward filter manually or use predict() on 
    # expanding windows (expensive but correct).
    #
    # Practical compromise: use a rolling window forward pass.
    # For each bar t, run model.predict_proba(X_scaled[:t+1]) and take the last row.
    # This is O(T^2) — expensive for large T. For production, implement the 
    # forward recursion directly (see below).
    
    n_bars = len(X_scaled)
    state_probs = np.zeros((n_bars, model.n_components))
    
    for t in range(n_bars):
        # Only use observations up to and including bar t
        probs = _forward_filter_to_t(model, X_scaled[:t+1])
        state_probs[t] = probs
    
    # Assign labels
    state_indices = np.argmax(state_probs, axis=1)
    df = df.copy()
    df["hmm_state"] = [state_labels[i] for i in state_indices]
    
    # "trending probability" = sum of probabilities of all trending states
    trending_states = [k for k, v in state_labels.items() if "trending" in v]
    df["hmm_trending_prob"] = state_probs[:, trending_states].sum(axis=1)
    
    return df


def _forward_filter_to_t(model, X_scaled_up_to_t: np.ndarray) -> np.ndarray:
    """
    Run the forward recursion on a sequence and return the posterior
    probabilities for the *last* bar. Pure forward pass — no lookahead.
    """
    K = model.n_components
    log_startprob = model.startprob_
    log_transmat = model.transmat_
    
    # Compute emission log-probabilities for each state at each bar
    # hmmlearn exposes _compute_log_likelihood for this
    log_emissionprob = model._compute_log_likelihood(X_scaled_up_to_t)  # (T, K)
    
    T = len(X_scaled_up_to_t)
    alpha = np.zeros((T, K))
    
    # Initialize
    alpha[0] = log_startprob + log_emissionprob[0]
    alpha[0] -= np.logaddexp.reduce(alpha[0])  # normalize in log-space
    
    # Recurse
    for t in range(1, T):
        for k in range(K):
            alpha[t, k] = log_emissionprob[t, k] + np.logaddexp.reduce(
                alpha[t-1] + np.log(log_transmat[:, k])
            )
        alpha[t] -= np.logaddexp.reduce(alpha[t])  # normalize
    
    # Convert from log-space to probabilities
    return np.exp(alpha[-1])
```

**Performance note:** The O(T²) loop above is correct but slow for long sequences. For a 3-month warm-up + 3-month backtest at 5-min resolution, T ≈ 15,000 bars. Running the full forward pass for each bar takes `O(T² × K)` time. The standard optimization: only recompute the forward variable incrementally. Since the forward filter is a recursion `α_t = f(α_{t-1}, o_t)`, you only need the previous step — compute it once linearly:

```python
def apply_forward_filter_fast(df, model, scaler, state_labels, features):
    """O(T * K) forward filter — incremental recursion."""
    X_scaled = scaler.transform(df[features].values)
    T, K = X_scaled.shape[0], model.n_components
    log_emissionprob = model._compute_log_likelihood(X_scaled)  # (T, K)
    
    state_probs = np.zeros((T, K))
    log_alpha = np.log(model.startprob_) + log_emissionprob[0]
    log_alpha -= np.logaddexp.reduce(log_alpha)
    state_probs[0] = np.exp(log_alpha)
    
    log_transmat = np.log(model.transmat_ + 1e-300)  # avoid log(0)
    
    for t in range(1, T):
        log_alpha_new = np.zeros(K)
        for k in range(K):
            log_alpha_new[k] = log_emissionprob[t, k] + np.logaddexp.reduce(
                log_alpha + log_transmat[:, k]
            )
        log_alpha_new -= np.logaddexp.reduce(log_alpha_new)
        state_probs[t] = np.exp(log_alpha_new)
        log_alpha = log_alpha_new
    
    # ... rest of labeling as above
```

This runs in milliseconds for 15,000 bars.

---

## Step 3: Integration into indicator_pair_pipeline.py

```python
# In compute_indicators() — add at the end, after all indicators are computed

if config.get("hmm", {}).get("enabled", False):
    from src.indicators.regime_hmm import train_hmm, apply_forward_filter_fast
    
    hmm_cfg = config["hmm"]
    features = hmm_cfg.get("features", ["bar_return", "bar_vol", "smi_fast", "williams_r"])
    n_states = hmm_cfg.get("n_states", 2)
    
    # df contains both warm-up and backtest bars at this point
    # The warm-up/backtest boundary is not explicitly marked in df here,
    # but the engine handles it via is_fraction. For HMM: train on the
    # entire df (the forward filter naturally uses only past data anyway).
    model, scaler, state_labels = train_hmm(df, n_states, features)
    df = apply_forward_filter_fast(df, model, scaler, state_labels, features)


# In generate_signals() — add after existing signal generation

if "hmm_trending_prob" in df.columns:
    hmm_threshold = config.get("hmm", {}).get("trending_threshold", 0.5)
    in_favorable_regime = df["hmm_trending_prob"] >= hmm_threshold
    signals = signals.where(in_favorable_regime, other=0)
```

The `where(in_favorable_regime, other=0)` call zeroes out any signal where the HMM says the regime is not favorable. Clean, non-invasive, and reversible.

---

## Step 4: Config Changes

Add to `config/strategy_params.yaml`:

```yaml
hmm:
  enabled: false           # off by default — opt in
  n_states: 2              # 2 = trending/choppy, 3 = trending-up/choppy/trending-down
  trending_threshold: 0.55 # fire signals only when P(trending) >= this value
  features:
    - bar_return
    - bar_vol
    - smi_fast
    - williams_r
```

With `enabled: false` as the default, all existing backtests remain unaffected. Enable it with a single config change.

---

## Step 5: Diagnostic Output

Add HMM regime labels to the backtest trade log. In `src/backtest/engine.py`, when a trade is closed, record the HMM state at the entry bar:

```python
# In the pending_entry fill logic — bar i+1 fills the signal from bar i
entry_bar_idx = self._pending_entry["signal_bar_idx"]
if "hmm_state" in self.df.columns:
    hmm_state_at_entry = self.df["hmm_state"].iloc[entry_bar_idx]
else:
    hmm_state_at_entry = "n/a"
# Store in trade log alongside other fields
```

This lets you run the regime-labeled analytics from Part 3 (Application 5): split your trade log by `hmm_state` and compare profit factors. This is the most valuable output — it tells you whether the HMM is actually identifying meaningful conditions.

---

## Step 6: Evaluating Whether It Helps

Run three backtests in sequence and compare:

**Baseline:**
```yaml
hmm:
  enabled: false
```

**HMM-filtered:**
```yaml
hmm:
  enabled: true
  n_states: 2
  trending_threshold: 0.55
```

**Regime-labeled only (no filtering — for analytics):**
Run with `enabled: true` but then in your analysis, compare trade outcomes split by regime label. This tells you whether the model is classifying correctly before you decide to filter.

**Metrics to compare:**

| Metric | What it tells you |
|--------|------------------|
| Profit factor (trending regime trades only) | Is the HMM correctly identifying good conditions? |
| Profit factor (choppy regime trades only) | Is the HMM correctly identifying bad conditions? |
| Total trades in filtered backtest | How many signals did filtering remove? |
| Profit factor of filtered backtest vs baseline | Did filtering help the overall result? |
| MC analysis on filtered backtest | Is the filtered result robust to trade ordering? |

**The key test:** If the HMM is working correctly, you should see profit factor significantly above 1.0 in the "trending" regime and significantly below 1.0 in the "choppy" regime. If both regimes show similar profit factors, the HMM is not discriminating — don't use it as a filter.

**Decision framework:**

```
1. Trending regime PF > Choppy regime PF?     → HMM is discriminating
2. Trending regime PF > 1.0?                  → Edge exists in favorable regimes
3. Filtered backtest PF > Baseline PF?        → Filter adds value
4. MC P25 PF of filtered backtest > 1.0?      → Edge survives adverse ordering
5. Enough trades remain after filtering?       → > 30 trades for meaningful stats
```

All five conditions should be true before deploying the HMM filter in live trading.

---

## Installation

One dependency to add:

```bash
pip install hmmlearn
```

Add to `requirements.txt`. The library is pure Python/numpy/scipy — no additional system dependencies.

---

## Where Viterbi Is Useful (Backtest Analysis Only)

After a backtest, you can run Viterbi over the full observation sequence to get the "ideal" regime labels with hindsight. This is *not* for signal generation — it's for understanding what the model thinks happened:

```python
# After backtest, on the full bar sequence:
from hmmlearn import hmm

log_prob, state_seq = model.decode(X_scaled)  # Viterbi
df["hmm_state_viterbi"] = [state_labels[s] for s in state_seq]
```

Compare `hmm_state` (forward filter, what you'd have known in real time) vs. `hmm_state_viterbi` (hindsight-optimal). The discrepancy is your filter's regime detection latency — how many bars behind the forward filter is relative to "ground truth."

If the latency is small (< 5 bars) and the two label sequences agree most of the time, your forward filter is good. If they diverge significantly, your filter has too much lag for the signal timescale.

---

## Relationship to Monte Carlo

Run MC analysis on both the baseline and HMM-filtered results. The question changes subtly:

- **Baseline MC:** "Is my PF sensitive to trade ordering?"
- **HMM-filtered MC:** "Is my (higher, presumably) PF sensitive to trade ordering? And is it based on enough trades?"

If filtering reduces trades from 80 to 50, those 50 trades may be from a less diverse sample of market conditions — the MC distribution will be wider. This is a warning signal: a higher PF on fewer, more homogeneous trades can be a regime-specific result that won't generalize.

---

**Key takeaway:** The integration is minimal — one new module, two modified functions, one config block. The heavy lifting is in the evaluation: does the HMM actually separate good-regime trades from bad-regime trades in your historical data? If yes, the filter is a genuine improvement. If not, it's adding complexity without benefit. Let the data answer that question before deploying.

[< Part 4: Pitfalls](04-pitfalls.md) | [Index](00-index.md)
