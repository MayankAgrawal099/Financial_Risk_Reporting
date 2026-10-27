"""Empirical leakage check on the prepared Lending Club sample.

A column can leak the outcome even if its name looks harmless. For every feature this
computes a single-feature AUC against the default flag:

* numeric columns: rank-based AUC (Mann-Whitney) of the raw value
* text columns: AUC of the out-of-fold default rate of each category

AUC 0.5 means no signal. Real application-time features are usually weak (0.52-0.70).
A single feature at 0.75 or above is suspicious and is flagged for review.

Run:  python src/check_leakage.py
Writes docs/leakage_check.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

SKIP = {"id", "loan_status", "default", "issue_d", "issue_year", "earliest_cr_line"}
FLAG_AT = 0.75


def auc(score: pd.Series, y: pd.Series) -> float:
    """Rank-based AUC; NaN scores are dropped."""
    m = score.notna()
    s, t = score[m], y[m]
    n1, n0 = int(t.sum()), int((1 - t).sum())
    if n1 == 0 or n0 == 0 or s.nunique() < 2:
        return float("nan")
    r = s.rank(method="average")
    return float((r[t == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def oof_category_rate(cat: pd.Series, y: pd.Series, folds: int = 5) -> pd.Series:
    """Out-of-fold mean default rate per category, so rare categories cannot memorise."""
    out = pd.Series(np.nan, index=cat.index)
    fold = np.arange(len(cat)) % folds
    cat = cat.fillna("(missing)")
    for k in range(folds):
        train = fold != k
        rate = y[train].groupby(cat[train]).mean()
        out[~train] = cat[~train].map(rate)
    return out.fillna(y.mean())


def check(sample_csv: Path, out_csv: Path, flag_at: float = FLAG_AT) -> pd.DataFrame:
    df = pd.read_csv(sample_csv, low_memory=False)
    y = df["default"]
    rows = []
    for col in df.columns:
        if col in SKIP:
            continue
        if pd.api.types.is_numeric_dtype(df[col]):
            a, kind = auc(df[col], y), "numeric"
        else:
            a, kind = auc(oof_category_rate(df[col], y), y), "category"
        if np.isnan(a):
            continue
        strength = max(a, 1 - a)
        rows.append({"column": col, "type": kind, "auc": round(a, 4),
                     "strength": round(strength, 4),
                     "null_rate": round(float(df[col].isna().mean()), 4),
                     "flag": "SUSPICIOUS" if strength >= flag_at else "ok"})
    res = pd.DataFrame(rows).sort_values("strength", ascending=False, ignore_index=True)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    res.to_csv(out_csv, index=False)
    return res


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--input", type=Path, default=root / "data" / "processed" / "lending_club_sample.csv")
    ap.add_argument("--output", type=Path, default=root / "docs" / "leakage_check.csv")
    ap.add_argument("--flag-at", type=float, default=FLAG_AT)
    a = ap.parse_args()
    res = check(a.input, a.output, a.flag_at)
    print(res.head(12).to_string(index=False))
    n = int((res["flag"] == "SUSPICIOUS").sum())
    print(f"\n{n} column(s) flagged as suspicious (single-feature AUC strength >= {a.flag_at})")
    print(f"Written to {a.output}")


if __name__ == "__main__":
    main()
