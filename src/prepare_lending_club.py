"""Trim the full Lending Club accepted-loans file into a modelling sample.

Input : accepted_2007_to_2018Q4.csv (or .csv.gz) from the Kaggle dataset
        "All Lending Club loan data" (about 2.2 million rows, 150 columns).
Output: data/processed/lending_club_sample.csv   (git-ignored, rebuilt by this script)
        docs/lending_club_profile.json           (committed: funnel, null rates, diagnostics)

What it does
------------
1. Reads the file in chunks and keeps ONLY an allow-list of columns that are known when a
   loan is approved. Everything else, including all post-origination (leakage) columns,
   is never loaded. An allow-list is safer than a drop-list: a column we forgot to name
   cannot leak in.
2. Drops loans without a final outcome. Only "Fully Paid" and "Charged Off" are kept
   (Current, Late, In Grace Period etc. are not final). Footer rows in the raw file
   disappear here because they have no loan_status.
3. Keeps loans issued in the chosen year range and one term. Defaults: 2013-2015, 36 months.
   The profile of the full file showed why: 28% of 2016 36-month loans and 45% of 2016
   60-month loans were still open at the end of the file, so their outcomes are incomplete.
   36-month loans issued up to 2015 are essentially all finished (0.05% open).
4. Takes a reproducible random sample per issue year (default 60,000 per year).
5. Writes a profile with a censoring diagnostic: for each issue year and term, how many
   loans are still open. Loans issued late in the file are right-censored (many have not
   finished), which biases their default rate upward. Use the diagnostic to pick the
   train/test years.

Run:
    python src/prepare_lending_club.py --input data/raw/accepted_2007_to_2018Q4.csv
    python src/prepare_lending_club.py --input data/raw/accepted_2007_to_2018Q4.csv.gz --term 36
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

KEY = ["id"]
TARGET_SOURCE = "loan_status"
SPLIT_ONLY = ["issue_d"]  # used to split train/test by time; never a model feature

NUMERIC = [
    "loan_amnt", "int_rate", "installment", "annual_inc", "dti", "delinq_2yrs",
    "fico_range_low", "fico_range_high", "inq_last_6mths", "mths_since_last_delinq",
    "mths_since_last_record", "open_acc", "pub_rec", "revol_bal", "revol_util",
    "total_acc", "mort_acc", "pub_rec_bankruptcies", "tot_cur_bal", "total_rev_hi_lim",
    "bc_util", "acc_open_past_24mths", "collections_12_mths_ex_med", "delinq_amnt",
    "tax_liens", "chargeoff_within_12_mths", "num_tl_90g_dpd_24m", "percent_bc_gt_75",
    "num_actv_bc_tl",
]
CATEGORICAL = [
    "term", "emp_length", "home_ownership", "verification_status", "purpose",
    "addr_state", "initial_list_status", "application_type",
]
# Lending Club's own risk judgement. Kept for sanity checks and comparison,
# but NOT to be used as scorecard features (they would just echo Lending Club's model).
REFERENCE_ONLY = ["grade", "sub_grade"]
DATES = ["earliest_cr_line"]

KEEP = KEY + [TARGET_SOURCE] + SPLIT_ONLY + CATEGORICAL + NUMERIC + REFERENCE_ONLY + DATES
# int_rate is also Lending Club's own price of risk; treated as reference-only in the scorecard.

# Known post-origination or otherwise unusable columns (documented, and asserted absent from KEEP).
LEAKAGE_COLUMNS = [
    "funded_amnt", "funded_amnt_inv", "out_prncp", "out_prncp_inv", "total_pymnt",
    "total_pymnt_inv", "total_rec_prncp", "total_rec_int", "total_rec_late_fee",
    "recoveries", "collection_recovery_fee", "last_pymnt_d", "last_pymnt_amnt",
    "next_pymnt_d", "last_credit_pull_d", "last_fico_range_high", "last_fico_range_low",
    "pymnt_plan", "debt_settlement_flag", "debt_settlement_flag_date", "settlement_status",
    "settlement_date", "settlement_amount", "settlement_percentage", "settlement_term",
    "hardship_flag", "hardship_type", "hardship_reason", "hardship_status",
    "hardship_start_date", "hardship_end_date", "payment_plan_start_date",
    "hardship_length", "hardship_dpd", "hardship_loan_status", "deferral_term",
    "orig_projected_additional_accrued_interest", "hardship_amount",
    "hardship_payoff_balance_amount", "hardship_last_payment_amount",
    "disbursement_method", "policy_code",
]
# Unusable for other reasons: identifiers, free text, near-identifiers.
EXCLUDED_OTHER = ["member_id", "url", "desc", "title", "emp_title", "zip_code"]

FINAL_STATUS = {"Fully Paid": 0, "Charged Off": 1}
EXCEL_ROW_LIMIT = 1_048_575  # data rows Excel keeps; a file with exactly this many was cut off by Excel

EXCEL_MESSAGE = (
    "This file looks like it was opened and saved in Excel ({why}).\n"
    "Excel cuts files at 1,048,575 rows (the original has about 2.26 million) and rewrites dates\n"
    "such as Dec-2015 as Dec-15, so the data is incomplete and some columns are altered.\n"
    "Use the ORIGINAL file from the Kaggle download and never open it in Excel:\n"
    "  - extract it again from the Kaggle zip, or point --input at the .csv.gz inside the zip\n"
    "    (no need to unzip it). Do not save over it from Excel.")

# Whole-number columns. Written as integers (not 3.0) so PostgreSQL SMALLINT columns accept them.
INTEGER_COLUMNS = [
    "issue_year", "term_months", "delinq_2yrs", "fico_range_low", "fico_range_high",
    "inq_last_6mths", "mths_since_last_delinq", "mths_since_last_record", "open_acc", "pub_rec",
    "total_acc", "mort_acc", "pub_rec_bankruptcies", "acc_open_past_24mths",
    "collections_12_mths_ex_med", "tax_liens", "chargeoff_within_12_mths", "num_tl_90g_dpd_24m",
    "num_actv_bc_tl",
]

OUTPUT_COLUMNS = (
    ["id", "loan_status", "default", "issue_d", "issue_year", "term_months"]
    + [c for c in CATEGORICAL if c != "term"]
    + NUMERIC + REFERENCE_ONLY + ["earliest_cr_line"]
)


def _status_group(s: pd.Series) -> pd.Series:
    return s.where(s.isin(FINAL_STATUS), "Not final")


def _read_chunks(path: Path, chunksize: int):
    keep = set(KEEP)
    return pd.read_csv(path, usecols=lambda c: c in keep, dtype=str, chunksize=chunksize,
                       low_memory=False)


def _to_number(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.str.replace("%", "", regex=False).str.strip(), errors="coerce")


def prepare(input_path: Path, output_path: Path, profile_path: Path, years=(2013, 2016),
            n_per_year: int = 40_000, term: int | None = None, seed: int = 42,
            chunksize: int = 200_000) -> dict:
    assert not set(KEEP) & set(LEAKAGE_COLUMNS), "allow-list contains a leakage column"
    rows_read = 0
    seen_columns: set[str] = set()
    censor: dict[tuple, int] = defaultdict(int)
    kept_parts = []
    funnel = {"rows_read": 0, "valid_issue_date": 0, "in_year_range": 0, "final_outcome": 0}
    status_counts: dict[str, int] = defaultdict(int)
    raw_issue_samples: list[str] = []

    for chunk in _read_chunks(input_path, chunksize):
        seen_columns |= set(chunk.columns)
        rows_read += len(chunk)
        if TARGET_SOURCE not in chunk or "issue_d" not in chunk:
            raise SystemExit("Input has no loan_status or issue_d column. Is this the accepted-loans file?")
        for k, v in chunk["loan_status"].value_counts(dropna=False).head(15).items():
            status_counts[str(k)] += int(v)
        if len(raw_issue_samples) < 5:
            raw_issue_samples += [str(v) for v in chunk["issue_d"].dropna().unique()[:5]]
        if rows_read == len(chunk):  # first chunk: look for signs of an Excel re-save
            first = chunk["issue_d"].dropna().head(200)
            if len(first) and first.str.fullmatch(r"[A-Za-z]{3}-\d{2}").all():
                raise SystemExit(EXCEL_MESSAGE.format(why="issue_d has 2-digit years like Dec-15"))
        issue = pd.to_datetime(chunk["issue_d"], format="%b-%Y", errors="coerce")
        chunk = chunk.assign(_issue=issue)
        chunk = chunk[chunk["_issue"].notna()]
        funnel["valid_issue_date"] += len(chunk)

        chunk = chunk[chunk["_issue"].dt.year.between(years[0], years[1])]
        if "term" in chunk:
            tm = pd.to_numeric(chunk["term"].str.extract(r"(\d+)")[0], errors="coerce")
        else:
            tm = pd.Series(np.nan, index=chunk.index)
        chunk = chunk.assign(term_months=tm)
        if term is not None:
            chunk = chunk[chunk["term_months"] == term]
        funnel["in_year_range"] += len(chunk)

        grp = _status_group(chunk["loan_status"])
        for (y, tmv, g), n in chunk.groupby([chunk["_issue"].dt.year, "term_months", grp],
                                            dropna=False).size().items():
            censor[(int(y), None if pd.isna(tmv) else int(tmv), g)] += int(n)

        chunk = chunk[chunk["loan_status"].isin(FINAL_STATUS)]
        funnel["final_outcome"] += len(chunk)
        kept_parts.append(chunk)

    funnel["rows_read"] = rows_read
    if rows_read == EXCEL_ROW_LIMIT:
        raise SystemExit(EXCEL_MESSAGE.format(why="it has exactly 1,048,575 rows, Excel's limit"))
    if not kept_parts or funnel["final_outcome"] == 0:
        top = sorted(status_counts.items(), key=lambda kv: -kv[1])[:10]
        raise SystemExit(
            "No rows left after filtering. What the script saw:\n"
            f"  rows read: {rows_read:,}\n"
            f"  columns found (of the ones we keep): {sorted(seen_columns)}\n"
            f"  rows with a readable issue_d (format like Dec-2015): {funnel['valid_issue_date']:,}\n"
            f"  rows inside the year range {years[0]}-{years[1]}: {funnel['in_year_range']:,}\n"
            f"  example raw issue_d values: {raw_issue_samples[:5]}\n"
            f"  most common loan_status values: {top}\n"
            "Send this whole message back so the filter can be fixed.")
    df = pd.concat(kept_parts, ignore_index=True)

    for col in NUMERIC:
        if col in df:
            df[col] = _to_number(df[col])
    df["default"] = df["loan_status"].map(FINAL_STATUS).astype(int)
    df["issue_year"] = df["_issue"].dt.year
    df["issue_d"] = df["_issue"].dt.strftime("%Y-%m-%d")
    if "earliest_cr_line" in df:
        df["earliest_cr_line"] = pd.to_datetime(df["earliest_cr_line"], format="%b-%Y",
                                                errors="coerce").dt.strftime("%Y-%m-%d")

    # reproducible sample per issue year
    rng = np.random.default_rng(seed)
    pieces = []
    for _, part in df.groupby("issue_year"):
        n = min(n_per_year, len(part))
        pieces.append(part.iloc[np.sort(rng.choice(len(part), size=n, replace=False))])
    sample = pd.concat(pieces, ignore_index=True)

    missing_cols = [c for c in OUTPUT_COLUMNS if c not in sample.columns]
    for c in missing_cols:
        sample[c] = np.nan
    sample = sample[OUTPUT_COLUMNS]
    for col in INTEGER_COLUMNS:
        sample[col] = pd.to_numeric(sample[col], errors="coerce").round().astype("Int64")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sample.to_csv(output_path, index=False)

    censoring = [
        {"issue_year": y, "term_months": t, "status_group": g, "loans": n}
        for (y, t, g), n in sorted(censor.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0, kv[0][2]))
    ]
    profile = {
        "input_file": input_path.name,
        "years": list(years),
        "term_filter": term,
        "n_per_year": n_per_year,
        "seed": seed,
        "funnel": funnel,
        "sample_rows": int(len(sample)),
        "sample_rows_by_year": {int(k): int(v) for k, v in sample.groupby("issue_year").size().items()},
        "default_rate_by_year": {int(k): round(float(v), 4) for k, v in
                                 sample.groupby("issue_year")["default"].mean().items()},
        "default_rate_overall": round(float(sample["default"].mean()), 4),
        "columns_requested_but_missing_in_file": sorted(set(KEEP) - seen_columns),
        "null_rate_by_column": {c: round(float(sample[c].isna().mean()), 4) for c in OUTPUT_COLUMNS},
        "censoring_diagnostic": censoring,
        "note": "Loans issued late in the file may still be open; 'Not final' counts show how many.",
    }
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(json.dumps(profile, indent=2))
    return profile


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser(description="Trim and sample the Lending Club accepted-loans file")
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output", type=Path, default=root / "data" / "processed" / "lending_club_sample.csv")
    ap.add_argument("--profile", type=Path, default=root / "docs" / "lending_club_profile.json")
    ap.add_argument("--years", type=int, nargs=2, default=[2013, 2015], metavar=("FROM", "TO"))
    ap.add_argument("--n-per-year", type=int, default=60_000)
    ap.add_argument("--term", type=int, choices=[36, 60], default=36,
                    help="loan term to keep (default 36: 36-month loans issued up to 2015 are all finished)")
    ap.add_argument("--all-terms", action="store_true", help="keep both 36 and 60 month loans")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    p = prepare(a.input, a.output, a.profile, tuple(a.years), a.n_per_year,
                None if a.all_terms else a.term, a.seed)
    f = p["funnel"]
    print(f"Rows read: {f['rows_read']:,}")
    print(f"In year range {a.years[0]}-{a.years[1]}: {f['in_year_range']:,}")
    print(f"With a final outcome: {f['final_outcome']:,}")
    print(f"Sample written: {p['sample_rows']:,} rows -> {a.output}")
    print(f"Default rate by year: {p['default_rate_by_year']}")
    if p["columns_requested_but_missing_in_file"]:
        print("Columns not found in your file (left empty):", p["columns_requested_but_missing_in_file"])
    print(f"Profile written to {a.profile}")


if __name__ == "__main__":
    main()
