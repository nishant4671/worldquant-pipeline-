# Quantitative Alpha Strategy & Modeling

This document details the quantitative philosophy, generative modeling methodology, data field quarantine governance, and deterministic quality gates enforced by the WorldQuant BRAIN Autonomous Pipeline.

---

## 1. Quantitative Foundation & Modeling Universe

The pipeline generates trading signals formulated in WorldQuant's proprietary domain-specific language, **FASTEXPR**. These signals are cross-sectional equities alphas simulated under the following institutional parameters:

| Parameter | Value | Institutional Rationale |
|---|---|---|
| **Instrument Type** | `EQUITY` | Liquid common stocks listed on major US exchanges. |
| **Region** | `USA` | Deepest liquidity pool and standardized regulatory reporting. |
| **Universe** | `TOP3000` | Approximately the Russell 3000 index, balancing liquidity and coverage. |
| **Execution Delay** | `1` | One-day delay ($T+1$) between signal observation and trade execution to eliminate lookahead bias. |
| **Decay** | `0` | Unsmoothed signal output; decay is explicitly formulated inside the expression via `ts_decay_linear`. |
| **Neutralization** | `INDUSTRY` | Enforces dollar-neutral long/short portfolios within each GICS industry, neutralizing systematic factor beta. |
| **Truncation** | `0.08` | Max position weight capped at 8% of gross market value to limit concentration risk. |
| **Pasteurization** | `ON` | Extreme outliers in inputs are clipped to prevent fat-finger distortions. |
| **Unit Handling** | `VERIFY` | Validates dimensional consistency across arithmetic operators. |
| **Test Period** | `P0Y0M` | Simulates across the full historical dataset (typically 5 to 10+ years). |

---

## 2. Multi-Agent Generative Architecture

Alpha hypothesis formulation uses a dual-agent architecture separating **ideation** from **adversarial validation**:

```
                       ┌────────────────────────┐
                       │  KNOWN_FIELDS Whitelist│
                       │  (PV, Fund, Analyst)   │
                       └───────────┬────────────┘
                                   │
                                   ▼
┌──────────────────┐    ┌────────────────────────┐
│ FASTEXPR Advanced│───>│ Proposer Agent (LLM)   │
│ Operators Set    │    │ (gemini-2.5-flash)     │
└──────────────────┘    └───────────┬────────────┘
                                    │ Raw Alpha Hypotheses
                                    ▼
                        ┌────────────────────────┐
                        │ Static AST Gatekeeper  │
                        │ (validator.py)         │
                        └───────────┬────────────┘
                                    │ Validated Syntactic Subset
                                    ▼
                        ┌────────────────────────┐
                        │ PM Critic Agent (LLM)  │
                        │ (Adversarial Persona)  │
                        └───────────┬────────────┘
                                    │ Approved Hypotheses
                                    ▼
                        ┌────────────────────────┐
                        │ 3-Worker AlphaEngine   │
                        │ (Concurrent Backtest)  │
                        └────────────────────────┘
```

### 2.1 The Proposer Agent (`LLMClient.generate_alphas`)
The Proposer formulates alpha hypotheses by synthesizing market microstructure, corporate fundamentals, and analyst consensus into cohesive mathematical representations.

#### System Prompt Specification
```text
You are an elite quantitative researcher. Propose exactly {num_alphas} unique alpha expressions. 
Use ADVANCED operators whenever possible to avoid correlation and reduce turnover. 
Simple rank(close) alphas are rejected. You MUST use operators like ts_decay_linear, group_neutralize, trade_when, and ts_zscore. 
Mix data categories: Price-Volume, Fundamental, Analyst. 
You MUST ONLY use fields from this list: {known_fields}. 
You MUST ONLY use these operators: {operators}. 
Return ONLY a JSON array of strings, e.g. ["ts_decay_linear(rank(close), 5)", "group_neutralize(ts_zscore(volume, 20), sector)"]. 
Do NOT wrap in markdown block or add explanations.
```

#### Strategy Imperatives
1. **Operator Sophistication**: Naive momentum signals (e.g., `rank(close)`) exhibit excessive turnover ($>100\%$) and suffer from high crowding. The prompt mandates temporal smoothing (`ts_decay_linear`), normalization (`ts_zscore`), and cross-sectional neutralization (`group_neutralize`).
2. **Cross-Category Hybridization**: Alpha orthogonalization is achieved by combining price-volume dynamics (e.g., liquidity shocks via `volume` or `vwap`) with slower-moving fundamental value indicators (`equity / (debt + 1e-9)`) or revisions (`rec_buy`).
3. **Turnover Management**: Unsmoothed daily signals generate high slippage. Operators such as `ts_decay_linear(expr, d)` calculate linearly weighted historical averages, drastically reducing daily turnover without sacrificing information coefficient (IC).

---

### 2.2 The PM Critic Agent (`LLMClient.critique_alphas`)
Before simulation credits are consumed on the platform, proposed alphas undergo review by an adversarial Portfolio Manager persona.

#### System Prompt Specification
```text
You are a strict Quantitative Portfolio Manager. Review the following Fast Expression alphas. 
Veto any that are mathematically trivial, redundant (like adding X and subtracting X), 
or lack economic intuition. Return ONLY a JSON array of the approved expressions. 
Do not include explanations.
```

#### Veto Criteria
- **Mathematical Triviality**: Neutralizing an already neutralized term, or adding zero-variance constants.
- **Economic Incoherence**: Comparing unnormalized metrics across unrelated dimensions without ratios (e.g., adding stock price to gross sales).
- **Redundancy**: Self-cancelling operations such as `rank(close) - rank(close)` or $X / X$.

#### Fail-Open Design
If the PM Critic returns an unparseable response, empty string, or API error, `llm_client.py` logs a warning and **fails open**, returning the original candidate list. This ensures temporary LLM parsing glitches never halt the automated simulation pipeline.

---

## 3. The `KNOWN_FIELDS` Whitelist & Field Quarantine

In FASTEXPR, referencing a non-existent or misnamed database field causes silent server-side errors, evaluation timeouts, or NaN returns.

The pipeline enforces a **Field Quarantine Strategy**: only fields confirmed through the WorldQuant BRAIN Data Explorer UI or official documentation are permitted into `KNOWN_FIELDS`.

### 3.1 Verified Whitelist (`KNOWN_FIELDS`)

| Category | Verified Field Name | Confidence | Economic Intuition |
|---|---|---|---|
| **`price_volume`** | `open` | High | Opening price of regular trading session. |
| | `close` | High | Official closing price. |
| | `high` | High | Intraday high price. |
| | `low` | High | Intraday low price. |
| | `volume` | High | Total shares traded during session. |
| | `vwap` | High | Volume-Weighted Average Price. |
| | `returns` | High | Single-day total return ($\frac{P_t - P_{t-1}}{P_{t-1}}$). |
| | `cap` | High | Market capitalization (Shares Outstanding $\times$ Close). |
| **`fundamental`** | `assets` | Medium | Total assets from balance sheet. |
| | `liabilities` | Medium | Total liabilities from balance sheet. |
| | `equity` | Medium | Shareholders' total book equity. |
| | `sales` | Medium | Total gross revenue/sales reported. |
| | `cash` | Medium | Cash and short-term cash equivalents. |
| | `debt` | Medium | Total long-term and short-term debt obligations. |
| | `ebit` | Medium | Earnings Before Interest and Taxes (operating profit). |
| **`analyst`** | `target_price` | Medium | Consensus mean target price from equity analysts. |
| | `est_eps` | Medium | Consensus estimated earnings per share. |
| | `est_sales` | Medium | Consensus estimated sales/revenue. |
| | `rec_mean` | Medium | Mean recommendation score (1=Strong Buy to 5=Strong Sell). |
| | `rec_buy` | Medium | Total count of analysts issuing Buy recommendations. |
| | `rec_sell` | Medium | Total count of analysts issuing Sell recommendations. |

### 3.2 Quarantined Fields (`UNVERIFIED_FIELDS`)
The following fields are strictly quarantined in `generator.py` and are blocked from LLM prompts and validation gates until verified:
- **Price-Volume**: `adv20`, `turnover`, `sharesout`.
- **Fundamental**: `revenue`, `gross_profit`, `operating_income`, `free_cash_flow`, `book_value`, `net_income`.
- **Analyst**: `pt_mean`, `recommendation_count`, `revisions_up`, `revisions_down`, `eps_std`, `eps_mean`.

---

## 4. Deterministic Quality Gates

Every completed simulation returns in-sample metrics evaluated against three non-negotiable production filters:

```
                  In-Sample (IS) Simulation Metrics
                                │
                                ▼
                   ┌─────────────────────────┐
                   │  Sharpe Ratio >= 1.25   │
                   └───────────┬─────────────┘
                               │ PASS
                               ▼
                   ┌─────────────────────────┐
                   │    Fitness > 1.00       │
                   └───────────┬─────────────┘
                               │ PASS
                               ▼
                   ┌─────────────────────────┐
                   │ 0.01 <= Turnover <= 0.70│
                   └───────────┬─────────────┘
                               │
               ┌───────────────┴───────────────┐
               │ PASS                          │ FAIL
               ▼                               ▼
       status: "passed"              status: "failed_filters"
       (Eligible for Telegram)        (Stored for audit)
```

### 4.1 Sharpe Ratio Gate: $\text{Sharpe} \ge 1.25$
$$\text{Sharpe} = \frac{\mathbb{E}[R_p - R_f]}{\sigma(R_p)}$$
- Measures annualized excess return per unit of volatility.
- **Cutoff**: Alphas with $\text{Sharpe} < 1.25$ fail to survive real-world execution drag, bid-ask spread friction, and transaction fees.

### 4.2 Fitness Gate: $\text{Fitness} > 1.00$
In WorldQuant BRAIN, Fitness is defined as:
$$\text{Fitness} = \text{Sharpe} \times \sqrt{\frac{|\text{Annualized Return}|}{\text{Annualized Turnover}}}$$
- Balances risk-adjusted returns against execution velocity.
- Alphas with high Sharpe but extreme turnover exhibit collapsed Fitness scores, failing this gate.

### 4.3 Turnover Gate: $0.01 \le \text{Turnover} \le 0.70$
Turnover represents the fraction of total portfolio value traded daily:
- **Minimum Floor ($1\%$)**: Alphas with $\text{Turnover} < 0.01$ indicate inactive, stale, or illiquid positions that do not respond to new market information.
- **Maximum Ceiling ($70\%$)**: Alphas with $\text{Turnover} > 0.70$ consume excessive market impact and exchange fees, rendering the signal unprofitable after commissions.
