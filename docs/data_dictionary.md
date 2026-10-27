# Data dictionary

> Work in progress. This file is built up task by task. Sections:
> 1. Lending Club: leakage handling and excluded columns (this section, complete)
> 2. Lending Club sample columns (to be added)
> 3. Synthetic ledger tables and assumptions (to be added)

---

## 1. Lending Club: leakage handling and excluded columns

**Source:** Kaggle, "All Lending Club loan data" (accepted loans, 2007-2018 Q4), 2,260,701 rows.
The raw file is not stored in this repository.

### Why this matters

A credit scorecard must only use information available when a loan is approved. Lending Club
files also contain what happened afterwards (payments, recoveries, settlements). Those columns
reveal the outcome, so a model trained on them looks excellent and is useless in practice.

### How leakage is prevented (three layers)

1. **Allow-list, not drop-list.** `src/prepare_lending_club.py` loads only columns named in an
   allow-list (`KEEP`). A column nobody reviewed cannot slip in. A test asserts that no known
   leakage column is on the allow-list.
2. **Column audit.** `src/audit_lending_club_columns.py` reads the raw file's header and labels every
   column. The full result is committed in `docs/lending_club_column_audit.csv`.
3. **Empirical check.** `src/check_leakage.py` computes each kept feature's single-feature AUC against
   the default flag on the prepared sample. Realistic application-time features score roughly
   0.50-0.70; anything at 0.75 or above is flagged. Results: `docs/leakage_check.csv`.

### Excluded as leakage (only known after the loan starts)

| Columns (as named in the file) | Why excluded |
|---|---|
| `total_pymnt`, `total_pymnt_inv`, `total_rec_prncp`, `total_rec_int`, `total_rec_late_fee`, `out_prncp`, `out_prncp_inv` | Payments received and balances outstanding. They grow as the loan is repaid and show whether it was repaid. |
| `recoveries`, `collection_recovery_fee` | Non-zero only after a loan is charged off. They are the outcome. |
| `last_pymnt_d`, `last_pymnt_amnt`, `next_pymnt_d`, `last_credit_pull_d` | Dates and amounts from the loan's life. A last payment long before the file date signals default. |
| `last_fico_range_high`, `last_fico_range_low` | FICO score re-pulled after origination. Falls sharply before a default. `fico_range_low/high` (at application) are kept instead. |
| `hardship_*`, `deferral_term`, `payment_plan_start_date`, `orig_projected_additional_accrued_interest` | Hardship programme data. Exists only for borrowers who struggled. |
| `settlement_*`, `debt_settlement_flag`, `debt_settlement_flag_date` | Debt settlement data. Exists only after default. |
| `funded_amnt`, `funded_amnt_inv` | Set at funding and nearly identical to `loan_amnt`. Redundant. |
| `pymnt_plan`, `disbursement_method`, `policy_code` | Process flags set after approval, or constant. |

If any of these names does not exist in the raw file, the audit simply does not list it.

### Excluded for other reasons

| Columns | Reason |
|---|---|
| `member_id`, `url`, `desc`, `title`, `emp_title`, `zip_code` | Identifiers, free text or near-identifiers (also a privacy risk). |
| `*_joint`, `sec_app_*` | Filled only for joint applications (a small share of loans). |
| Remaining credit-bureau fields not on the allow-list | Labelled `NOT_SELECTED_REVIEW` in the audit. They may be known at application, but were not reviewed. Verify against the Lending Club data dictionary before adding any. |

### Kept but not used as features

| Column | Treatment |
|---|---|
| `issue_d` | Used only to split train and test by time. Never a feature. |
| `loan_status` | Used only to derive the `default` target (Charged Off = 1, Fully Paid = 0). |
| `grade`, `sub_grade`, `int_rate` | Lending Club's own risk assessment and price of risk. Kept to compare the scorecard against Lending Club's grading. Not used as features, since the scorecard would just reproduce their model. |

### Related sample design decision (right-censoring)

Loans without a final outcome (Current, Late, In Grace Period) are dropped. Because the file ends in
Q4 2018, loans issued late are still open: 28% of 2016 36-month loans and 45% of 2016 60-month loans.
Keeping only their finished outcomes would overstate their default rate. The sample is therefore
**36-month loans issued 2013-2015** (0.05% still open in 2015), split by time:
train 2013-2014, test 2015. The evidence is in `docs/lending_club_profile.json` (`censoring_diagnostic`).

### Results on the full raw file

Column audit (`docs/lending_club_column_audit.csv`), 151 columns in the file:

| Decision | Columns |
|---|---|
| LEAKAGE (excluded) | 42 |
| NOT_SELECTED_REVIEW (excluded, not yet reviewed) | 44 |
| EXCLUDED_SPARSE_JOINT | 16 |
| EXCLUDED_IDENTIFIER_OR_TEXT | 6 |
| USED_FEATURE (kept in sample) | 37 |
| REFERENCE_ONLY (`grade`, `sub_grade`, `int_rate`) | 3 |
| KEY, SPLIT_ONLY, TARGET_SOURCE (`id`, `issue_d`, `loan_status`) | 3 |

Empirical leakage check on the prepared sample (`docs/leakage_check.csv`, 36-month loans issued 2013-2015):
**0 of the kept columns were flagged** (threshold: single-feature AUC strength 0.75 or above).

| Column | Single-feature AUC strength | Note |
|---|---|---|
| `sub_grade` | 0.663 | Lending Club's own grade, reference only |
| `int_rate` | 0.654 | Lending Club's own price of risk, reference only |
| `grade` | 0.651 | Lending Club's own grade, reference only |
| `fico_range_high` / `fico_range_low` | 0.598 | Strongest genuine application-time feature (lower score, more defaults) |
| `annual_inc` | 0.581 | |
| `acc_open_past_24mths` | 0.580 | |
| `dti` | 0.571 | |
| `total_rev_hi_lim` | 0.566 | |
| `tot_cur_bal` | 0.564 | |
| `mort_acc` | 0.562 | |
| `home_ownership` | 0.552 | |

How to read this: the strongest signals are Lending Club's own grades, as expected, and every feature
we may use sits between 0.50 and 0.60. That is the pattern of real application-time information.
Leaked columns typically score above 0.9. The check is a screen, not a proof: leakage can also come from
combinations of columns, so the scorecard is validated on a later time period (train 2013-2014, test 2015).

The 44 `NOT_SELECTED_REVIEW` columns are mostly extra credit-bureau fields. They are not needed for the
scorecard. If one is added later, check the Lending Club data dictionary first and re-run `check_leakage.py`.
