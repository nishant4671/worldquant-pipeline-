"""Quantitative Overfitting Audit: Probability of Backtest Overfitting (PBO) & Deflated Sharpe Ratio (DSR).

Implements:
1. Deflated Sharpe Ratio (DSR) based on Bailey & López de Prado (2014), adjusting for selection bias,
   skewness, and kurtosis across multiple strategy trials.
2. Combinatorially Symmetric Cross-Validation (CSCV) and Probability of Backtest Overfitting (PBO)
   based on Bailey, Borwein, López de Prado, and Zhu (2015).
"""

import itertools
import math
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import scipy.stats


def calculate_pbo_and_dsr(
    csv_path: str | Path = "reports/quant_audit/trial_history.csv",
    output_path: str | Path = "reports/quant_audit/pbo_dsr_results.txt",
    num_subperiods: int = 16,
) -> dict[str, Any]:
    """Calculate Deflated Sharpe Ratio (DSR) and Probability of Backtest Overfitting (PBO).

    Args:
        csv_path: Path to trial_history.csv.
        output_path: Destination path for formatted audit results.
        num_subperiods: Number of disjoint slices for CSCV (default 16).

    Returns:
        dict[str, Any]: Metrics dictionary containing best Sharpe, DSR, PBO, and intermediate stats.

    Raises:
        ValueError: If fewer than 2 alphas are available in the trial dataset.
    """
    csv_file = Path(csv_path)
    if not csv_file.exists():
        raise FileNotFoundError(f"Trial history CSV not found at {csv_file}")

    # 1. Load data and clean
    df = pd.read_csv(csv_file)
    if "Date" in df.columns:
        df = df.set_index("Date")

    # Drop columns that are entirely NaN and fill remaining missing values with 0.0
    df = df.dropna(axis=1, how="all")
    df = df.fillna(0.0)

    alpha_cols = list(df.columns)
    num_alphas = len(alpha_cols)
    num_days = len(df)

    if num_alphas < 2:
        raise ValueError(
            f"At least 2 alphas required in {csv_file} to calculate PBO and DSR, found {num_alphas}."
        )

    # 2. Deflated Sharpe Ratio (DSR) calculation
    daily_returns = df.values  # Shape: (T, N)
    means = np.mean(daily_returns, axis=0)
    stds = np.std(daily_returns, axis=0, ddof=1)
    stds = np.where(stds > 1e-12, stds, 1e-12)
    daily_sharpes = means / stds

    best_idx = int(np.argmax(daily_sharpes))
    best_alpha = alpha_cols[best_idx]
    best_sr = float(daily_sharpes[best_idx])
    best_sr_ann = float(best_sr * np.sqrt(252))

    # Variance of Sharpe ratios across all trials
    var_sr = float(np.var(daily_sharpes, ddof=1)) if num_alphas > 1 else 0.0

    # Expected Maximum Sharpe Ratio (SR0) under the null hypothesis (Bailey & López de Prado 2014)
    euler_gamma = 0.57721566490153286
    if num_alphas > 1 and var_sr > 0.0:
        p1 = 1.0 - (1.0 / num_alphas)
        p2 = 1.0 - (1.0 / (num_alphas * math.e))
        z1 = float(scipy.stats.norm.ppf(p1))
        z2 = float(scipy.stats.norm.ppf(p2))
        sr_0 = float(np.sqrt(var_sr) * ((1.0 - euler_gamma) * z1 + euler_gamma * z2))
    else:
        sr_0 = 0.0

    # Skewness and Kurtosis of the best alpha returns
    best_series = daily_returns[:, best_idx]
    skew = float(scipy.stats.skew(best_series))
    kurt = float(scipy.stats.kurtosis(best_series, fisher=False))  # Pearson kurtosis (normal=3)

    # Standard deviation of the Sharpe estimator under non-normality
    t_obs = float(num_days)
    var_estimator_term = 1.0 - skew * best_sr + ((kurt - 1.0) / 4.0) * (best_sr**2)
    if var_estimator_term > 0.0 and t_obs > 1:
        sigma_sr = math.sqrt(var_estimator_term / (t_obs - 1.0))
    else:
        sigma_sr = 1.0 / math.sqrt(max(t_obs - 1.0, 1.0))

    # Z-score and DSR
    if sigma_sr > 0.0:
        z_score = (best_sr - sr_0) / sigma_sr
        dsr = float(scipy.stats.norm.cdf(z_score))
    else:
        dsr = 0.5

    # 3. Probability of Backtest Overfitting (PBO) via CSCV
    s = num_subperiods
    slices = np.array_split(np.arange(num_days), s)
    slice_lens = np.array([len(sl) for sl in slices], dtype=np.float64)
    slice_sums = np.array([daily_returns[sl].sum(axis=0) for sl in slices], dtype=np.float64)
    slice_sumsq = np.array([(daily_returns[sl] ** 2).sum(axis=0) for sl in slices], dtype=np.float64)

    total_sum = daily_returns.sum(axis=0)
    total_sumsq = (daily_returns**2).sum(axis=0)

    combos = list(itertools.combinations(range(s), s // 2))
    overfit_events = 0
    total_splits = len(combos)

    for is_indices in combos:
        is_idx = list(is_indices)
        t_is = slice_lens[is_idx].sum()
        sum_is = slice_sums[is_idx].sum(axis=0)
        sumsq_is = slice_sumsq[is_idx].sum(axis=0)

        mean_is = sum_is / t_is
        var_is = (sumsq_is - t_is * (mean_is**2)) / max(t_is - 1.0, 1.0)
        sr_is = mean_is / np.sqrt(np.maximum(var_is, 1e-12))

        # Best IS alpha
        opt_alpha_idx = int(np.argmax(sr_is))

        # OOS evaluation
        t_oos = float(num_days) - t_is
        sum_oos = total_sum - sum_is
        sumsq_oos = total_sumsq - sumsq_is

        mean_oos = sum_oos / t_oos
        var_oos = (sumsq_oos - t_oos * (mean_oos**2)) / max(t_oos - 1.0, 1.0)
        sr_oos = mean_oos / np.sqrt(np.maximum(var_oos, 1e-12))

        # OOS ranking (1-based, ascending: 1=worst, N=best)
        rank = (sr_oos < sr_oos[opt_alpha_idx]).sum() + 1
        rel_rank = float(rank) / (float(num_alphas) + 1.0)

        # Logit of OOS rank
        rel_rank_clipped = min(max(rel_rank, 1e-7), 1.0 - 1e-7)
        logit = math.log(rel_rank_clipped / (1.0 - rel_rank_clipped))

        # Negative logit indicates below-median performance OOS
        if logit < 0.0:
            overfit_events += 1

    pbo = float(overfit_events) / float(total_splits) if total_splits > 0 else 0.0

    # 4. Print results to terminal
    print("\n" + "=" * 62)
    print("        QUANTITATIVE OVERFITTING AUDIT (DSR & PBO)")
    print("=" * 62)
    print(f"Total Alpha Trials Analyzed:  {num_alphas}")
    print(f"Daily Historical Observations: {num_days}")
    print(f"Top In-Sample Alpha ID:       {best_alpha}")
    print(f"Best Trial Sharpe (Daily):    {best_sr:.4f}")
    print(f"Best Trial Sharpe (Annual):   {best_sr_ann:.4f}")
    print(f"Variance of Sharpe Across Trials: {var_sr:.6f}")
    print(f"Expected Max Sharpe (SR0):    {sr_0:.4f}")
    print(f"Deflated Sharpe Ratio (DSR):  {dsr:.4f} ({dsr * 100:.2f}%)")
    print("-" * 62)
    print(f"CSCV Disjoint Sub-periods:    {s} (Splits: {total_splits:,})")
    print(f"Overfitting Occurrences:      {overfit_events:,} / {total_splits:,}")
    print(f"Probability of Backtest Overfitting (PBO): {pbo:.4f} ({pbo * 100:.2f}%)")
    print("=" * 62 + "\n")

    # 5. Format and save results to text report
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    report_lines = [
        "=" * 64,
        "             WORLDQUANT BRAIN QUANTITATIVE AUDIT",
        "     Deflated Sharpe Ratio (DSR) & Backtest Overfitting (PBO)",
        "=" * 64,
        f"Analyzed Dataset:       {csv_file}",
        f"Total Alpha Trials:     {num_alphas}",
        f"Historical Dates (T):   {num_days}",
        f"Candidate Alphas:       {', '.join(alpha_cols)}",
        "",
        "-" * 64,
        "1. DEFLATED SHARPE RATIO (DSR) AUDIT",
        "-" * 64,
        f"Best Performing Alpha:  {best_alpha}",
        f"Daily Sharpe Ratio:     {best_sr:+.4f}",
        f"Annualized Sharpe (IS): {best_sr_ann:+.4f}",
        f"Variance of Trial SRs:  {var_sr:.6f}",
        f"Expected Max SR (SR0):  {sr_0:+.4f}",
        f"Return Skewness:        {skew:+.4f}",
        f"Return Kurtosis (raw):  {kurt:.4f}",
        f"Estimation Std (sigma): {sigma_sr:.6f}",
        f"Deflated Sharpe Ratio:  {dsr:.4f} ({dsr * 100:.2f}%)",
        "",
        "Interpretation:",
        (
            "  * DSR > 0.95 indicates true statistical significance after multi-testing discount."
            if dsr >= 0.95
            else "  * DSR < 0.95 indicates the observed Sharpe is likely a product of data snooping."
        ),
        "",
        "-" * 64,
        "2. PROBABILITY OF BACKTEST OVERFITTING (PBO) AUDIT",
        "-" * 64,
        f"Partition Slices (S):   {s}",
        f"Total CSCV Splits:      {total_splits:,}",
        f"Overfit Splits Count:   {overfit_events:,}",
        f"PBO (Probability):      {pbo:.4f} ({pbo * 100:.2f}%)",
        "",
        "Interpretation:",
        (
            "  * PBO < 0.20 indicates robust out-of-sample persistence (low overfitting risk)."
            if pbo < 0.20
            else "  * PBO >= 0.50 indicates high probability that IS selection degrades OOS."
        ),
        "=" * 64,
    ]

    report_content = "\n".join(report_lines)
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(report_content)

    return {
        "num_alphas": num_alphas,
        "num_days": num_days,
        "best_alpha": best_alpha,
        "best_sr": best_sr,
        "best_sr_ann": best_sr_ann,
        "var_sr": var_sr,
        "sr_0": sr_0,
        "skewness": skew,
        "kurtosis": kurt,
        "dsr": dsr,
        "pbo": pbo,
        "overfit_events": overfit_events,
        "total_splits": total_splits,
    }


if __name__ == "__main__":
    calculate_pbo_and_dsr()
