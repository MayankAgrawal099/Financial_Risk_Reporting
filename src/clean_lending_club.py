"""Finish cleaning the Lending Club sample and split it by issue year (train / test).

Input : data/processed/lending_club_sample.csv   (written by prepare_lending_club.py)
Output: data/processed/lc_train.csv, lc_test.csv  (git-ignored)
        docs/scorecard_data_prep.json             (every fitted cleaning parameter)
        docs/scorecard_data_prep.md               (readable report: split, missing values, drops)

Rules (full list in docs/scorecard_data_prep.md):
  * Time-based split: train = issue years 2013-2014, test = 2015. No random shuffle, so the test set looks
    like "the future" the scorecard will really face.
  * Every number learned from the data (medians, percentile caps, kept categories, which columns to drop)
    is fitted on the TRAIN rows only and then applied unchanged to the test rows. This avoids leakage.
  * Impossible values (negative dti, zero income, FICO outside 300-850 ...) become missing.
  * Missing values: "never happened" fields (months since last delinquency / public record) and any column
    with more than 5% missing in train keep their NaN, so the WoE step can give "Missing" its own bin.
    Columns with a little missing (up to 5%) are filled with the train median. Text columns get "Missing".
  * Numbers are capped at the train 0.5th and 99.5th percentiles (winsorising). Rare categories become "Other".
  * Columns that are constant in train, or more than 60% missing in train, are dropped and listed. The two
    "never happened" columns are exempt from the 60% rule: their blanks carry meaning (about 85% of loans have
    no public record), so they are kept with Missing as its own bin.
  * int_rate, grade, sub_grade and installment are Lending Club's own price of risk. They are NOT features;
    they are kept as reference columns to benchmark the scorecard against Lending Club's grade.

    python src/clean_lending_club.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

TARGET = "default"
KEY = ["id"]
REFERENCE = ["int_rate", "grade", "sub_grade", "installment"]
CATEGORICAL = ["emp_length", "home_ownership", "verification_status", "purpose", "addr_state",
               "initial_list_status", "application_type"]
NUMERIC = ["loan_amnt", "annual_inc", "dti", "delinq_2yrs", "inq_last_6mths", "mths_since_last_delinq",
           "mths_since_last_record", "open_acc", "pub_rec", "revol_bal", "revol_util", "total_acc", "mort_acc",
           "pub_rec_bankruptcies", "tot_cur_bal", "total_rev_hi_lim", "bc_util", "acc_open_past_24mths",
           "collections_12_mths_ex_med", "delinq_amnt", "tax_liens", "chargeoff_within_12_mths",
           "num_tl_90g_dpd_24m", "percent_bc_gt_75", "num_actv_bc_tl", "term_months"]
DERIVED = ["credit_history_years", "fico_avg", "loan_to_income"]   # built from earliest_cr_line, fico_*, loan_amnt
NATURAL_MISSING = ["mths_since_last_delinq", "mths_since_last_record"]   # blank means "never": keep as Missing

VALID_RANGE = {   # (min, max); None = no limit. Values outside become missing.
    "annual_inc": (1, None), "dti": (0, None), "revol_util": (0, None), "bc_util": (0, None),
    "percent_bc_gt_75": (0, 100), "loan_amnt": (1, None), "fico_range_low": (300, 850),
    "fico_range_high": (300, 850), "credit_history_years": (0, None),
}
SETTINGS = {"keep_missing_above": 0.05, "drop_missing_above": 0.60, "rare_category_below": 0.005,
            "winsor_low": 0.005, "winsor_high": 0.995, "drift_flag_points": 0.10}


# ------------------------------------------------------------------ reading and deriving
def read_sample(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, low_memory=False)
    for c in ("issue_d", "earliest_cr_line"):
        if c in df:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    return df


def split_by_issue_year(df: pd.DataFrame, train_years, test_years) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Time-based split. Raises if the years overlap or the data is not in time order."""
    if set(train_years) & set(test_years):
        raise ValueError("train and test years overlap")
    if max(train_years) >= min(test_years):
        raise ValueError("test years must come after the train years")
    train = df[df["issue_year"].isin(train_years)].copy()
    test = df[df["issue_year"].isin(test_years)].copy()
    if train.empty or test.empty:
        raise ValueError(f"empty split: train {len(train)} rows, test {len(test)} rows")
    assert train["issue_d"].max() < test["issue_d"].min(), "a train loan was issued after a test loan"
    assert not set(train["id"]) & set(test["id"]), "a loan id is in both train and test"
    return train, test


def _clean_ranges(df: pd.DataFrame, stats: dict) -> pd.DataFrame:
    for col, (lo, hi) in VALID_RANGE.items():
        if col not in df:
            continue
        bad = pd.Series(False, index=df.index)
        if lo is not None:
            bad |= df[col] < lo
        if hi is not None:
            bad |= df[col] > hi
        if bad.any():
            stats.setdefault("invalid_set_to_missing", {})[col] = int(bad.sum())
            df.loc[bad, col] = np.nan
    return df


def derive(df: pd.DataFrame, stats: dict) -> pd.DataFrame:
    df = df.copy()
    df = _clean_ranges(df, stats)       # first on the raw inputs (income, fico ...)
    df["credit_history_years"] = (df["issue_d"] - df["earliest_cr_line"]).dt.days / 365.25
    df["fico_avg"] = (df["fico_range_low"] + df["fico_range_high"]) / 2
    df["loan_to_income"] = df["loan_amnt"] / df["annual_inc"]
    return _clean_ranges(df, stats)     # again for the derived columns


# ------------------------------------------------------------------ fit on train
def fit_cleaning(train_raw: pd.DataFrame) -> dict:
    """Learn every cleaning parameter from the TRAIN rows only."""
    stats: dict = {}
    d = derive(train_raw, stats)
    s = SETTINGS
    params: dict = {"settings": s, "dropped": {}, "impute_median": {}, "keep_missing": [], "winsor": {},
                    "categories": {}, "train_missing_share": {}}
    num, cat = [], []
    for col in NUMERIC + DERIVED:
        if col not in d:
            params["dropped"][col] = "column not in file"
            continue
        share = float(d[col].isna().mean())
        params["train_missing_share"][col] = round(share, 4)
        if d[col].nunique(dropna=True) <= 1:
            params["dropped"][col] = "constant in train"
        elif share > s["drop_missing_above"] and col not in NATURAL_MISSING:   # a blank there means "never": informative
            params["dropped"][col] = f"{share:.0%} missing in train (limit {s['drop_missing_above']:.0%})"
        else:
            num.append(col)
            if col in NATURAL_MISSING or share > s["keep_missing_above"]:
                params["keep_missing"].append(col)
            elif share > 0:
                params["impute_median"][col] = float(d[col].median())
            lo, hi = d[col].quantile([s["winsor_low"], s["winsor_high"]])
            params["winsor"][col] = [float(lo), float(hi)]
    for col in CATEGORICAL:
        if col not in d:
            params["dropped"][col] = "column not in file"
            continue
        v = _tidy_category(d[col], col)
        params["train_missing_share"][col] = round(float((v == "Missing").mean()), 4)
        share = v.value_counts(normalize=True)
        if len(share) <= 1:
            params["dropped"][col] = "constant in train"
            continue
        keep = sorted(share[share >= s["rare_category_below"]].index.tolist())
        if "Other" not in keep:
            keep.append("Other")
        params["categories"][col] = keep
        cat.append(col)
    params["numeric_features"], params["categorical_features"] = num, cat
    params["reference_columns"] = [c for c in REFERENCE if c in train_raw]
    return params


def _tidy_category(s: pd.Series, col: str) -> pd.Series:
    v = s.astype("string").str.strip()
    v = v.mask(v.isna() | (v == "") | (v.str.lower() == "nan") | (v.str.lower() == "n/a"), "Missing")
    if col == "home_ownership":
        v = v.replace({"ANY": "OTHER", "NONE": "OTHER"})
    return v.astype(object)


# ------------------------------------------------------------------ apply (train or test)
def apply_cleaning(raw: pd.DataFrame, params: dict) -> tuple[pd.DataFrame, dict]:
    """Apply the fitted parameters. Returns the cleaned frame and counts of what was changed."""
    stats: dict = {"rows_in": int(len(raw))}
    raw = raw.drop_duplicates(subset="id", keep="first")
    stats["duplicate_ids_removed"] = int(stats["rows_in"] - len(raw))
    d = derive(raw, stats)
    out = d[KEY + [TARGET, "issue_year"]].copy()
    for col in params["numeric_features"]:
        x = d[col]
        if col in params["impute_median"]:
            stats.setdefault("imputed_with_median", {})[col] = int(x.isna().sum())
            x = x.fillna(params["impute_median"][col])
        lo, hi = params["winsor"][col]
        clipped = int(((x < lo) | (x > hi)).sum())
        if clipped:
            stats.setdefault("capped_at_train_percentile", {})[col] = clipped
        out[col] = x.clip(lo, hi)
    for col in params["categorical_features"]:
        v = _tidy_category(d[col], col)
        known = set(params["categories"][col])
        unseen = ~v.isin(known)
        if unseen.any():
            stats.setdefault("grouped_into_Other", {})[col] = int(unseen.sum())
        out[col] = v.where(~unseen, "Other")
    for col in params["reference_columns"]:
        out[col] = d[col]
    stats["rows_out"] = int(len(out))
    return out, stats


# ------------------------------------------------------------------ report
def missing_by_year(raw: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    d = derive(raw, {})
    return d.groupby("issue_year")[cols].apply(lambda g: g.isna().mean()).T


def write_report(path: Path, params: dict, train_stats: dict, test_stats: dict, train: pd.DataFrame,
                 test: pd.DataFrame, by_year: pd.DataFrame, train_years, test_years) -> None:
    s = params["settings"]
    L = ["# Scorecard data preparation", "",
         "Written by `src/clean_lending_club.py`. All parameters below were learned on the **train** loans only "
         "and applied unchanged to the test loans. Raw parameters: `docs/scorecard_data_prep.json`.", "",
         "## 1. Time-based split by issue year", "",
         "| Set | Issue years | Loans | Defaults | Default rate |", "|---|---|---|---|---|"]
    for name, yrs, df in (("Train", train_years, train), ("Test", test_years, test)):
        L.append(f"| {name} | {min(yrs) if min(yrs) == max(yrs) else f'{min(yrs)}-{max(yrs)}'} | {len(df):,} | {int(df[TARGET].sum()):,} | {df[TARGET].mean():.2%} |")
    L += ["", "Default rate by issue year:", "", "| Issue year | Loans | Default rate |", "|---|---|---|"]
    both = pd.concat([train, test])
    for y, g in both.groupby("issue_year"):
        L.append(f"| {int(y)} | {len(g):,} | {g[TARGET].mean():.2%} |")
    L += ["", "No loan appears in both sets, and every train loan was issued before every test loan.", "",
          "## 2. Columns dropped", "", "| Column | Reason |", "|---|---|"]
    L += [f"| `{c}` | {r} |" for c, r in params["dropped"].items()] or ["| none | |"]
    L += ["", "## 3. Features kept", "",
          f"- Numeric ({len(params['numeric_features'])}): " + ", ".join(f"`{c}`" for c in params["numeric_features"]),
          f"- Categorical ({len(params['categorical_features'])}): " + ", ".join(f"`{c}`" for c in params["categorical_features"]),
          "- Reference only, not features: " + ", ".join(f"`{c}`" for c in params["reference_columns"]),
          "", "## 4. Missing values", "",
          f"Policy: blank means \"never\" for `mths_since_last_delinq` and `mths_since_last_record`; any column with more "
          f"than {s['keep_missing_above']:.0%} missing in train keeps its blanks (the WoE step gives \"Missing\" its own bin); "
          f"columns with up to {s['keep_missing_above']:.0%} are filled with the train median; text columns get \"Missing\".", "",
          "| Column | Train missing | Treatment |", "|---|---|---|"]
    for c in params["numeric_features"]:
        t = "kept as Missing (own bin)" if c in params["keep_missing"] else (
            f"filled with train median {params['impute_median'][c]:.4g}" if c in params["impute_median"] else "none missing")
        L.append(f"| `{c}` | {params['train_missing_share'][c]:.1%} | {t} |")
    L += ["", "Missing share by issue year, for columns with any blanks (a jump between train and test years means the "
          "data changed over time):", "", "| Column | " + " | ".join(str(int(y)) for y in by_year.columns) + " | Drift flag |",
          "|---|" + "---|" * (len(by_year.columns) + 1)]
    for c, row in by_year.iterrows():
        if row.max() == 0:
            continue
        tr = row[[y for y in by_year.columns if y in train_years]].mean()
        te = row[[y for y in by_year.columns if y in test_years]].mean()
        flag = "CHECK" if abs(te - tr) > s["drift_flag_points"] else ""
        L.append(f"| `{c}` | " + " | ".join(f"{v:.1%}" for v in row) + f" | {flag} |")
    L += ["", "## 5. Other rules", "",
          "- Impossible values set to missing (negative dti, zero income, FICO outside 300-850, ...); counts below.",
          f"- Numbers capped at the train {s['winsor_low']:.1%} and {s['winsor_high']:.1%} percentiles.",
          f"- Categories under {s['rare_category_below']:.1%} of train loans, and categories never seen in train, become \"Other\".",
          "- `home_ownership` values ANY and NONE become OTHER.",
          "- Derived: `credit_history_years` (issue date minus earliest credit line), `fico_avg` (mean of low and high), "
          "`loan_to_income` (loan amount / annual income). The raw FICO low and high columns are replaced by `fico_avg`.",
          "- `installment` is reference only because it is computed from the interest rate, which is Lending Club's own price of risk.", "",
          "## 6. What was changed (counts)", "", "| Change | Train | Test |", "|---|---|---|"]
    L.append(f"| Rows in | {train_stats['rows_in']:,} | {test_stats['rows_in']:,} |")
    L.append(f"| Duplicate ids removed | {train_stats['duplicate_ids_removed']} | {test_stats['duplicate_ids_removed']} |")
    L.append(f"| Rows out | {train_stats['rows_out']:,} | {test_stats['rows_out']:,} |")
    for key, label in (("invalid_set_to_missing", "Impossible values set to missing"),
                       ("imputed_with_median", "Filled with train median"),
                       ("capped_at_train_percentile", "Capped at train percentile"),
                       ("grouped_into_Other", "Grouped into Other")):
        cols = sorted(set(train_stats.get(key, {})) | set(test_stats.get(key, {})))
        for c in cols:
            L.append(f"| {label}: `{c}` | {train_stats.get(key, {}).get(c, 0):,} | {test_stats.get(key, {}).get(c, 0):,} |")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ pipeline
def run(input_path: Path, out_dir: Path, docs_dir: Path, train_years=(2013, 2014), test_years=(2015,)) -> dict:
    raw = read_sample(input_path)
    train_raw, test_raw = split_by_issue_year(raw, train_years, test_years)
    params = fit_cleaning(train_raw)
    train, train_stats = apply_cleaning(train_raw, params)
    test, test_stats = apply_cleaning(test_raw, params)
    assert list(train.columns) == list(test.columns)
    out_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)
    train.to_csv(out_dir / "lc_train.csv", index=False)
    test.to_csv(out_dir / "lc_test.csv", index=False)
    cols_with_blanks = [c for c in params["numeric_features"] if params["train_missing_share"].get(c, 0) > 0
                        or derive(test_raw, {})[c].isna().any()]
    by_year = missing_by_year(pd.concat([train_raw, test_raw]), cols_with_blanks) if cols_with_blanks else \
        pd.DataFrame(columns=sorted(set(train_years) | set(test_years)))
    (docs_dir / "scorecard_data_prep.json").write_text(
        json.dumps({"train_years": list(train_years), "test_years": list(test_years), "params": params,
                    "train_stats": train_stats, "test_stats": test_stats}, indent=2), encoding="utf-8")
    write_report(docs_dir / "scorecard_data_prep.md", params, train_stats, test_stats, train, test, by_year,
                 train_years, test_years)
    return {"params": params, "train": train, "test": test, "train_stats": train_stats, "test_stats": test_stats}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--input", type=Path, default=ROOT / "data" / "processed" / "lending_club_sample.csv")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "data" / "processed")
    ap.add_argument("--docs-dir", type=Path, default=ROOT / "docs")
    ap.add_argument("--train-years", type=int, nargs="+", default=[2013, 2014])
    ap.add_argument("--test-years", type=int, nargs="+", default=[2015])
    a = ap.parse_args()
    r = run(a.input, a.out_dir, a.docs_dir, tuple(a.train_years), tuple(a.test_years))
    p = r["params"]
    print(f"Train: {len(r['train']):,} loans, default rate {r['train'][TARGET].mean():.2%}")
    print(f"Test : {len(r['test']):,} loans, default rate {r['test'][TARGET].mean():.2%}")
    print(f"Features: {len(p['numeric_features'])} numeric, {len(p['categorical_features'])} categorical")
    print(f"Dropped : {p['dropped'] or 'none'}")
    print(f"Kept as Missing (own bin): {p['keep_missing']}")
    print(f"Report: {a.docs_dir / 'scorecard_data_prep.md'}")


if __name__ == "__main__":
    main()
