"""Label every column in the raw Lending Club file: used, leakage, excluded, or needs review.

Reads only the header of the big file (fast). Writes docs/lending_club_column_audit.csv.

Decisions
---------
KEY / TARGET_SOURCE / SPLIT_ONLY   needed for joining, deriving the target, splitting by time
USED_FEATURE                       known when the loan is approved; kept in the sample
REFERENCE_ONLY                     kept for comparison, NOT a scorecard feature (Lending Club's own risk call)
LEAKAGE                            only known after the loan starts; would leak the outcome
EXCLUDED_IDENTIFIER_OR_TEXT        identifiers, free text, near-identifiers
EXCLUDED_SPARSE_JOINT              only filled for joint applications
NOT_SELECTED_REVIEW                not named anywhere; check the Lending Club data dictionary
                                   before ever adding it (it may be known at application, or not)

Run:  python src/audit_lending_club_columns.py --input data/raw/accepted_2007_to_2018q4.csv
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

import prepare_lending_club as plc

LEAKAGE_RULES = [
    (r"^(total_pymnt|total_rec_|out_prncp|recoveries|collection_recovery_fee)",
     "Payments, balances or recoveries observed after the loan started"),
    (r"^(last_pymnt|next_pymnt|last_credit_pull|last_fico_range)",
     "Latest payment or credit-pull information observed during the loan's life"),
    (r"^(hardship|deferral_term|payment_plan_start|orig_projected)",
     "Hardship programme data, only exists after a borrower struggles"),
    (r"^(settlement|debt_settlement)",
     "Debt settlement data, only exists after default"),
    (r"^funded_amnt",
     "Set at funding and almost identical to loan_amnt (redundant)"),
    (r"^(pymnt_plan|disbursement_method|policy_code)",
     "Process or programme flags set after approval, or constant"),
]


def classify(col: str) -> tuple[str, str]:
    if col in plc.KEY:
        return "KEY", "Loan identifier"
    if col == plc.TARGET_SOURCE:
        return "TARGET_SOURCE", "Final loan status; used only to derive the default flag"
    if col in plc.SPLIT_ONLY:
        return "SPLIT_ONLY", "Issue date; used to split train/test by time, never a feature"
    if col in plc.REFERENCE_ONLY:
        return "REFERENCE_ONLY", "Lending Club's own risk grade; comparison only, not a feature"
    if col in plc.KEEP:
        why = "Lending Club's own interest rate, comparison only" if col == "int_rate" \
            else "Known at application; kept as a candidate feature"
        return ("REFERENCE_ONLY" if col == "int_rate" else "USED_FEATURE"), why
    for pattern, reason in LEAKAGE_RULES:
        if re.match(pattern, col):
            return "LEAKAGE", reason
    if col in plc.EXCLUDED_OTHER:
        return "EXCLUDED_IDENTIFIER_OR_TEXT", "Identifier, free text or near-identifier"
    if re.search(r"_joint$|^sec_app_|^joint_", col):
        return "EXCLUDED_SPARSE_JOINT", "Only populated for joint applications"
    return "NOT_SELECTED_REVIEW", "Not selected; verify against the data dictionary before use"


def audit(input_path: Path, out_csv: Path) -> pd.DataFrame:
    cols = list(pd.read_csv(input_path, nrows=0).columns)
    rows = [{"column": c, "decision": d, "reason": r}
            for c in cols for d, r in [classify(c)]]
    df = pd.DataFrame(rows)
    missing = sorted(set(plc.KEEP) - set(cols))
    if missing:
        print("WARNING: allow-listed columns not in the file:", missing)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_csv, index=False)
    return df


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output", type=Path, default=root / "docs" / "lending_club_column_audit.csv")
    a = ap.parse_args()
    df = audit(a.input, a.output)
    print(f"{len(df)} columns in the file")
    print(df["decision"].value_counts().to_string())
    print(f"Audit written to {a.output}")


if __name__ == "__main__":
    main()
