---
tags: [research, autoresearch, optimization, literature-review]
created: 2026-05-13
---

# Research Memo: Literature Review for Autoresearch Strategy Optimization

## Hypothesis
Academic literature can identify the highest-value optimization direction for the SMI + Williams %R options backtesting strategy, reducing wasted autoresearch cycles on approaches with no academic backing.

## Key Findings by Theme

---

### Theme 1: Walk-Forward Validation is Non-Negotiable

**[2603.09219] AlgoXpert Alpha Research Framework (2026)**
The most directly relevant paper. Proposes a rigorous In-Sample → Walk-Forward Analysis → Out-of-Sample protocol specifically for mitigating overfitting in quantitative strategies. Key insight: **the IS/WFA/OOS split must be enforced at the experiment level, not just the final validation.**

**[2602.10785] Double Out-of-Sample Walk-Forward (Mroziewicz & Ślepaczuk, 2026)**
Introduces parameterizing walk-forward window lengths as an optimization variable itself. Found that walk-forward window size is a first-order parameter — the strategy performance varies more by window choice than by indicator parameters. Uses a "single-time OOS test" (train once, test once) to prevent data leakage. **Key insight for autoresearch: instead of fixed 3-month window, sweep walk-forward window lengths (1d, 5d, 10d, 20d) as a primary variable.**

**[10.69882/adba.iteb.2026013] GA + Walk-Forward Robustness Analysis (Kör & Zengin, 2026)**
Uses genetic algorithms with a Calmar-like fitness ratio and walk-forward validation. Simultaneously optimizes which indicators to use (genetic switch between EMA, MACD, RSI, Momentum) AND their parameters. Achieved Sortino 1.98 vs buy-hold 1.21 with -18.5% vs -35.1% drawdown. **Key insight: indicator selection and parameter tuning should be co-optimized, not sequential.**

### Theme 2: Stochastic Oscillator + Williams %R Directly Validated

**[Semantic Scholar] Algorithm-Based Low-Frequency Trading Using Stochastic Oscillator, Williams%R, and Trading Volume for S&P 500 (2024, 1 cite)**
This paper is almost identical to our setup — it pairs Stochastic Oscillator with Williams %R AND adds Trading Volume as a third filter. Published confirmation that this specific indicator pair works for S&P 500 (SYMBOL's parent index). **Key insight: adding a volume filter (like KVO or simple volume threshold) could reduce false signals without adding complexity.**

**[Semantic Scholar] Machine Learning + StochRSI + Price Volume (2024, 11 cites)**
Uses Stochastic RSI (which we have in `src/indicators/`) combined with volume analysis via ML. Highest-cited paper found. **Key insight: StochRSI + volume features is the most academically supported alternative to SMI + W%R.**

### Theme 3: Intraday Momentum Structure

**[1202.2447] Ensemble Properties of High Frequency Data and Intraday Trading Rules (2012)**
Studies scaling properties of intraday S&P returns. Found that intraday momentum has a specific half-life structure — signals decay faster than daily. **Key insight: on 5-min bars, the optimal indicator lookback is likely shorter than daily-equivalent. Current SMI(5,8,3) may be in the right range but should be tested against shorter periods.**

### Theme 4: Overfitting Detection & Multiple Testing

The Probabilistic Sharpe Ratio (PSR) and Deflated Sharpe Ratio (DSR) already in our composite score come from Bailey & Lopez de Prado's work. The literature strongly validates using these. **No papers found suggesting a better composite formula — our `trade_sharpe × profit_factor × PSR` is defensible.**

---

## Rankings: Which Direction Has Most Academic Backing?

| Rank | Direction | Academic Support | Expected Autoresearch Impact | Risk |
|------|-----------|-----------------|------------------------------|------|
| **#1** | **Walk-forward window sweep** | 3 papers (2026), strong consensus | HIGH — may find current fixed window is suboptimal | Low — just changes how we validate |
| **#2** | **Add volume filter to SMI+W%R** | 2 papers, direct validation of current pair | MEDIUM — could reduce false signals | Low — additive change |
| **#3** | **Shorter indicator periods (intraday decay)** | 1 paper + general intraday theory | MEDIUM — might improve signal timing | Medium — risk of noise |
| **#4** | **Switch to StochRSI + volume** | 1 paper (11 cites, highest impact) | HIGH — but requires more code changes | High — full strategy swap |
| **#5** | **TSI + StochRSI (from future_strategies.md)** | 0 direct papers | UNKNOWN — theoretically sound but untested | High — no empirical backing found |
| **#6** | **Fisher + CRSI / KVO + CCI** | 0 papers found | UNKNOWN | High — purely practitioner literature |

---

## Recommendation for Autoresearch

### Phase 1: Walk-Forward Window Optimization (HIGHEST PRIORITY)
Based on Mroziewicz & Ślepaczuk (2026), the walk-forward window length is a first-order parameter that's currently FIXED in our setup. The autoresearch loop should:
1. First establish baseline with current params
2. Sweep `sync_window` (the arm→fire delay) across [5, 10, 15, 20, 25, 30, 40]
3. This is the "walk-forward window" equivalent for our signal system
4. Look for a **plateau** (robust range) not a single peak

### Phase 2: Volume Confirmation Filter
Based on the Stochastic Oscillator + W%R + Volume paper (2024), add a simple volume filter:
1. Only take signals when volume > 20-bar SMA of volume
2. This is a single boolean parameter change (`vwap_filter: true` or add volume threshold)
3. Should reduce false breakouts with minimal complexity

### Phase 3: StochRSI as Williams %R Replacement
If Phases 1-2 plateau, the StochRSI paper (11 cites) suggests replacing W%R with StochRSI — which is already implemented in `src/indicators/`. This is the "seamless drop-in" from `future_strategies.md` Strategy 1, now with academic backing.

---

## Papers NOT to Pursue (Low Value for Autoresearch)
- Fisher Transform / Klinger / Connors RSI — no academic literature found for systematic trading
- MACD Histogram + ROC — too generic, no specific advantage over current system
- ML-based approaches — autoresearch loop modifies `strategy_params.yaml`, not model architectures

## Sources
- arXiv: 5 papers retrieved
- Semantic Scholar: 8 papers retrieved
- Total unique: ~12 relevant papers
