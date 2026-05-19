---
tags: [tutorial, hmm, math, forward-algorithm, viterbi, baum-welch]
---
# Part 2: The Math — The Three Components, Inference, and Training

[< Part 1: Intuition](01-intuition.md) | [Part 3: Quant Applications >](03-quant-applications.md)

---

## The Three Defining Components

An HMM is fully specified by three things. Textbooks call them `(π, A, B)`.

### 1. Initial State Distribution — π (pi)

A probability vector: what is the probability of starting in each state?

```
π = [π_1, π_2, ..., π_K]   where K = number of hidden states

Example (3 states: Trending-Up, Choppy, Trending-Down):
π = [0.40, 0.40, 0.20]
    "At the start of a new day/period, we're equally likely to be in
     a trending-up or choppy regime, less likely to start trending down."
```

Sums to 1.0 by definition.

---

### 2. Transition Matrix — A

A K×K matrix. `A[i][j]` = probability of transitioning from state `i` to state `j` on the next bar.

```
           To: Trending-Up  Choppy  Trending-Down
From:
Trending-Up  [   0.85         0.10       0.05    ]
Choppy       [   0.15         0.70       0.15    ]
Trending-Down[   0.05         0.15       0.80    ]
```

Reading row 1: "If the market is currently trending up, there's an 85% chance it's still trending up next bar, 10% chance it goes choppy, 5% chance it reverses to trending down."

Key properties:
- Each row sums to 1.0 (you must be in *some* state next bar)
- High diagonal values = regimes are *persistent* (don't flip every bar)
- This matrix encodes the Markov property: next state depends only on the current state

**The diagonal is the most important.** High diagonal (0.85–0.95) = regimes last tens to hundreds of bars. Low diagonal (0.50–0.60) = rapid regime switching. For 5-min SYMBOL bars, typical intraday regimes persist for 20–100+ bars, so you'd expect high diagonal values.

---

### 3. Emission Distribution — B

For each hidden state, a probability distribution over the possible observations. This is what links the hidden states to what you can actually measure.

For *discrete* observations (a categorical value), B is a matrix. For *continuous* observations (a return, a volatility number), B is a parametric distribution — almost always **Gaussian (normal)** in practice.

```
Continuous case: each state k has its own Gaussian emission

  State "Trending-Up":   observations ~ N(μ=+0.12%, σ=0.5%)
  State "Choppy":        observations ~ N(μ=+0.00%, σ=1.2%)
  State "Trending-Down": observations ~ N(μ=-0.10%, σ=0.6%)
```

Reading this: when the market is trending up, 5-min bar returns are centered around +0.12% with low volatility. When choppy, returns are centered around zero with *high* volatility. When trending down, centered around -0.10%.

**This is the mechanism that makes HMMs useful for trading.** Different regimes have different return distributions. If you observe a bar with return +0.3% and volatility 0.5%, that's much more likely under "Trending-Up" than under "Choppy." The decoder uses this likelihood to update its regime belief.

For multiple features (e.g., return AND SMI AND W%R), you use a **multivariate Gaussian**:

```
Observation at time t: o_t = [return_t, vol_t, smi_t, wr_t]

State k emission: o_t | state=k ~ N(μ_k, Σ_k)

Where:
  μ_k = mean vector (one mean per feature for state k)
  Σ_k = covariance matrix (K×K, captures correlations between features)
```

---

## The Forward Algorithm — Evaluation and Filtering

Given a sequence of observations `O = {o_1, o_2, ..., o_T}` and a trained model `(π, A, B)`, compute the probability of being in each state at each time step.

This is the **filter** — it answers "what state am I in *right now*, given everything I've seen so far?" This is the version you use in live trading and backtesting (no future data).

### Definition

```
α_t(k) = P(o_1, o_2, ..., o_t, state_t = k | model)

"The probability of observing the first t observations AND being in state k at time t."
```

### Recursion

**Initialize (t=1):**
```
α_1(k) = π_k × b_k(o_1)

Where b_k(o_1) = emission probability of observing o_1 from state k
```

**Recurse (t = 2 to T):**
```
α_t(k) = b_k(o_t) × Σ_j [ α_{t-1}(j) × A[j][k] ]
                          ↑ sum over all previous states
```

Plain English: "The probability of being in state k at time t equals (the likelihood of observing o_t from state k) times (the sum of: probability I was in each state j at t-1, times the probability of transitioning from j to k)."

**Normalize** at each step:
```
α_t(k) ← α_t(k) / Σ_j α_t(j)
```

After normalization, `α_t(k)` is the **posterior probability** of being in state k at time t: `P(state_t = k | o_1, ..., o_t)`. This is the number you use for signal gating — "I'm 85% confident we're in state 1 (Trending-Up) right now."

**Numerical note:** Raw α values decay exponentially with T (you're multiplying small probabilities together). Always work in log-space or normalize at each step to avoid floating-point underflow.

---

## Viterbi Decoding — The Most Likely State Sequence

The Forward algorithm gives the *marginal* probability of being in each state at each time step. Viterbi gives the *globally most likely sequence* of states:

```
State sequence* = argmax_{s_1,...,s_T} P(s_1,...,s_T | O, model)
```

This is useful for backtesting analysis ("what regime was each bar in, looking back?"). But it requires the full sequence — you can't run it bar-by-bar in real time without seeing the future. **For live trading and real-time backtesting, use the Forward filter, not Viterbi.**

### The algorithm

```
δ_t(k) = max probability of the best path ending in state k at time t
ψ_t(k) = which state at t-1 led to state k at time t in the best path

Initialize:
  δ_1(k) = π_k × b_k(o_1)
  ψ_1(k) = 0

Recurse:
  δ_t(k) = b_k(o_t) × max_j [ δ_{t-1}(j) × A[j][k] ]
  ψ_t(k) = argmax_j [ δ_{t-1}(j) × A[j][k] ]

Backtrack:
  s*_T = argmax_k δ_T(k)
  s*_t = ψ_{t+1}(s*_{t+1})    for t = T-1 down to 1
```

The backtrack step is what makes Viterbi "global" — it traces backward through the stored best-predecessor matrix to find the single most likely path, not just the most likely state at each step independently.

---

## Baum-Welch Training — Learning Parameters from Data

You need to fit `(π, A, B)` from historical bar data. There's no closed-form solution, so you use **Baum-Welch**, which is the Expectation-Maximization (EM) algorithm applied to HMMs.

### The idea

EM alternates between:
- **E-step (Expectation):** Given the current model parameters, compute the expected number of times we were in each state and made each transition.
- **M-step (Maximization):** Update the parameters to maximize the data likelihood given those expected counts.

Repeat until convergence (log-likelihood stops improving).

### E-step: The Backward Variable

In addition to the forward variable `α_t(k)`, compute a backward variable:

```
β_t(k) = P(o_{t+1}, ..., o_T | state_t = k, model)

"The probability of observing the remaining observations, given I'm in state k at time t."
```

Initialize: `β_T(k) = 1` for all k.

Recurse backward:
```
β_t(k) = Σ_j [ A[k][j] × b_j(o_{t+1}) × β_{t+1}(j) ]
```

Then compute the "smoothed" state probability (using *all* observations, past and future):
```
γ_t(k) = α_t(k) × β_t(k) / Σ_j α_t(j) × β_t(j)

γ_t(k) = P(state_t = k | O, model)   [the full posterior, not just the filter]
```

And the "pairwise" state probability:
```
ξ_t(j,k) = P(state_t = j, state_{t+1} = k | O, model)
           = α_t(j) × A[j][k] × b_k(o_{t+1}) × β_{t+1}(k)
             ─────────────────────────────────────────────
                    Σ_{j,k} [same numerator]
```

`ξ_t(j,k)` = expected number of transitions from state j to state k at time t, given the full observation sequence.

### M-step: Parameter Updates

```
π_k* = γ_1(k)                           ← initial state: posterior at t=1

A[j][k]* = Σ_t ξ_t(j,k) / Σ_t γ_t(j)  ← transition: expected count j→k
                                           ÷ expected time in state j

μ_k* = Σ_t γ_t(k) × o_t / Σ_t γ_t(k)  ← emission mean: weighted average
                                           of observations assigned to state k

Σ_k* = Σ_t γ_t(k) × (o_t - μ_k*)(o_t - μ_k*)^T / Σ_t γ_t(k)
                                         ← emission covariance: weighted outer product
```

Iterate E-step and M-step until `log P(O | model)` converges (typically 10–50 iterations in practice).

### What Baum-Welch Does Not Guarantee

- **Global maximum.** Baum-Welch finds a local maximum of the likelihood. Initialization matters — try multiple random starts and take the best.
- **Semantic meaning.** The model learns states that explain the observations statistically. State 1 might be "high-vol oscillating" and state 2 might be "low-vol trending." It won't label them for you.
- **Stationarity.** The algorithm assumes the regime dynamics (A, B) are constant across the entire training period. If the market structure changed mid-sample, Baum-Welch will fit averaged parameters that don't fully capture either regime.

---

## How Many States?

This is a model selection problem. No formula gives the answer; you have to evaluate:

| States | Typical use |
|--------|-------------|
| 2 | Risk-on vs risk-off. Simplest, most robust. |
| 3 | Trending-up, choppy, trending-down. Meaningful distinction. |
| 4+ | Diminishing returns. Very hard to keep stable across market cycles. |

Criteria for selection:
- **BIC/AIC** (penalized likelihood) — lower is better; penalizes extra states
- **Stability** — do the states persist long enough to be useful? A state that lasts 2 bars on average is noise
- **Interpretability** — do the emission distributions correspond to anything you recognize as a real market condition?

For this project, start with **2 states**. It's harder to overfit, easier to validate, and the primary question is binary: "should I be trading right now or not?"

---

## Continuous Emissions in Practice

For Gaussian emissions, the update formulas above apply directly. The `hmmlearn` library (Python) handles all of this:

```python
from hmmlearn import hmm

model = hmm.GaussianHMM(n_components=2, covariance_type="full", n_iter=100)
model.fit(X_train)   # X_train shape: (n_bars, n_features)

# After training, decode a sequence:
log_prob, state_seq = model.decode(X_test)  # Viterbi
# Or get per-bar state probabilities:
state_probs = model.predict_proba(X_test)   # Forward posteriors
```

`covariance_type="full"` lets each state have its own full covariance matrix (captures feature correlations). `"diag"` is faster but assumes features are independent within each state. Start with `"diag"` for small feature sets.

---

**Key takeaway:** An HMM has three components (initial distribution π, transition matrix A, emission distribution B). The Forward algorithm filters the current regime probability bar-by-bar from past data only — that is what you use in trading. Viterbi decodes the full sequence retrospectively. Baum-Welch trains all three components via iterative EM. In Python, `hmmlearn` does all three.

[< Part 1: Intuition](01-intuition.md) | [Part 3: Quant Applications >](03-quant-applications.md)
