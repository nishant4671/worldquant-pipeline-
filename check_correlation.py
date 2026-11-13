"""Check pairwise Pearson self-correlation across simulated alpha trial histories."""

from pathlib import Path
import numpy as np
import pandas as pd


def check_self_correlation(
    csv_path: str | Path = "reports/quant_audit/trial_history.csv",
    output_path: str | Path = "reports/quant_audit/self_correlation_matrix.txt",
    threshold: float = 0.70,
) -> pd.DataFrame:
    """Calculate and export Pearson correlation matrix for alpha trials.

    Args:
        csv_path: Path to input trial_history.csv.
        output_path: Path to destination text report.
        threshold: Threshold above which pairs are flagged as highly correlated.

    Returns:
        pd.DataFrame: Pearson correlation matrix.
    """
    csv_file = Path(csv_path)
    if not csv_file.exists():
        raise FileNotFoundError(f"Trial history CSV not found at {csv_file}")

    df = pd.read_csv(csv_file)
    if "Date" in df.columns:
        df = df.set_index("Date")

    df = df.dropna(axis=1, how="all")
    df = df.fillna(0.0)

    # 3. Calculate Pearson correlation matrix
    corr_matrix = df.corr(method="pearson")
    corr_rounded = corr_matrix.round(2)

    # 4. Print correlation matrix nicely formatted
    print("\n" + "=" * 60)
    print("           ALPHA SELF-CORRELATION MATRIX (PEARSON)")
    print("=" * 60)
    print(corr_rounded.to_string())
    print("-" * 60)

    # 6. Check for pairs with correlation > threshold
    alphas = list(corr_matrix.columns)
    high_corr_pairs = []
    for i in range(len(alphas)):
        for j in range(i + 1, len(alphas)):
            val = float(corr_matrix.iloc[i, j])
            if val > threshold:
                high_corr_pairs.append((alphas[i], alphas[j], val))

    warning_lines = []
    if high_corr_pairs:
        warning_msg = f"WARNING: Found {len(high_corr_pairs)} pair(s) with correlation > {threshold:.2f}:"
        print(warning_msg)
        warning_lines.append(warning_msg)
        for a1, a2, val in high_corr_pairs:
            line = f"  * {a1} <-> {a2}: {val:.2f}"
            print(line)
            warning_lines.append(line)
    else:
        ok_msg = f"No highly correlated pairs found (all < {threshold:.2f})."
        print(ok_msg)
        warning_lines.append(ok_msg)

    print("=" * 60 + "\n")

    # 5. Save output to file
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    report_lines = [
        "=" * 60,
        "           ALPHA SELF-CORRELATION MATRIX (PEARSON)",
        "=" * 60,
        f"Input Dataset: {csv_file}",
        f"Total Alphas:  {len(alphas)}",
        "",
        "Correlation Matrix (Rounded to 2 decimals):",
        corr_rounded.to_string(),
        "",
        "-" * 60,
        "Correlation Check (> 0.70):",
        *warning_lines,
        "=" * 60,
    ]
    report_content = "\n".join(report_lines) + "\n"

    with open(out_file, "w", encoding="utf-8") as f:
        f.write(report_content)

    return corr_matrix


if __name__ == "__main__":
    check_self_correlation()
