# Data dictionary

Two separate datasets feed this project. They are different modules of one fictional lender and are
**not reconciled with each other**.

| Dataset | Real or synthetic | Used for | Where it comes from |
|---|---|---|---|
| Lending Club sample (`lending_club_sample`) | **Real** public loan data (Kaggle) | Credit risk scorecard | `src/prepare_lending_club.py`, rebuilt from the raw file (not stored in the repo) |
| Company ledgers (7 tables, section 3) | **Synthetic** (Faker and a seeded simulation, seed 42) | P&L, balance sheet, budget vs actual | `src/generate_ledgers.py`, CSVs committed in `data/synthetic/` |

Sections: 1. Lending Club leakage handling, 2. Lending Club sample columns, 3. Synthetic ledger tables.
Column types use the PostgreSQL types that the SQL schema will use.

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

---

## 2. Lending Club sample columns (REAL data)

Table `lending_club_sample`, file `data/processed/lending_club_sample.csv` (git-ignored, rebuilt by
`src/prepare_lending_club.py`). One row per loan. Default sample: 36-month loans issued 2013-2015 with a
final outcome, 60,000 per issue year. Descriptions paraphrase the official Lending Club data dictionary.
Null rates are in `docs/lending_club_profile.json`.

"Role" values: **key**, **target** (derived), **split** (time split only), **feature** (candidate scorecard
input, known at application), **reference** (comparison only, not a feature).

| Column | Type | Role | Description |
|---|---|---|---|
| `id` | BIGINT | key | Lending Club loan identifier. |
| `loan_status` | VARCHAR(20) | target source | Final status. Only `Fully Paid` and `Charged Off` are kept. |
| `default` | SMALLINT | target | Derived: 1 if Charged Off, 0 if Fully Paid. |
| `issue_d` | DATE | split | Month the loan was funded (first day of month). Used only to split train and test by time. |
| `issue_year` | SMALLINT | split | Derived: year of `issue_d`. |
| `term_months` | SMALLINT | feature | Derived from `term`: number of payments (36 in the default sample). |
| `emp_length` | VARCHAR(20) | feature | Employment length from "< 1 year" to "10+ years". Null = not provided. |
| `home_ownership` | VARCHAR(20) | feature | RENT, OWN, MORTGAGE or OTHER, as reported at application. |
| `verification_status` | VARCHAR(30) | feature | Whether income was verified: Verified, Source Verified, Not Verified. |
| `purpose` | VARCHAR(40) | feature | Borrower's stated reason for the loan (for example debt_consolidation). |
| `addr_state` | CHAR(2) | feature | Borrower's US state. |
| `initial_list_status` | CHAR(1) | feature | Lending Club's initial listing status, w or f (whole or fractional). |
| `application_type` | VARCHAR(20) | feature | Individual or Joint App. |
| `loan_amnt` | NUMERIC(10,2) | feature | Amount of the loan applied for, USD. |
| `int_rate` | NUMERIC(6,2) | reference | Interest rate, percent. Lending Club's price of risk, so not a feature. |
| `installment` | NUMERIC(10,2) | feature | Monthly payment owed, USD. |
| `annual_inc` | NUMERIC(14,2) | feature | Self-reported annual income, USD. |
| `dti` | NUMERIC(8,2) | feature | Monthly debt payments (excluding mortgage and this loan) divided by monthly income, percent. |
| `delinq_2yrs` | SMALLINT | feature | Number of 30+ days past-due delinquencies in the past 2 years. |
| `fico_range_low` | SMALLINT | feature | Lower bound of the borrower's FICO score range at application. |
| `fico_range_high` | SMALLINT | feature | Upper bound of the borrower's FICO score range at application. |
| `inq_last_6mths` | SMALLINT | feature | Credit inquiries in the last 6 months (excluding auto and mortgage). |
| `mths_since_last_delinq` | SMALLINT | feature | Months since the last delinquency. Null = never delinquent (treat as its own bin, do not fill). |
| `mths_since_last_record` | SMALLINT | feature | Months since the last public record. Null = no record (treat as its own bin, do not fill). |
| `open_acc` | SMALLINT | feature | Number of open credit lines. |
| `pub_rec` | SMALLINT | feature | Number of derogatory public records. |
| `revol_bal` | NUMERIC(14,2) | feature | Total revolving credit balance, USD. |
| `revol_util` | NUMERIC(8,2) | feature | Revolving line utilisation rate, percent (the % sign is stripped). |
| `total_acc` | SMALLINT | feature | Total credit lines in the borrower's file. |
| `mort_acc` | SMALLINT | feature | Number of mortgage accounts. |
| `pub_rec_bankruptcies` | SMALLINT | feature | Number of public-record bankruptcies. |
| `tot_cur_bal` | NUMERIC(14,2) | feature | Total current balance of all accounts, USD. |
| `total_rev_hi_lim` | NUMERIC(14,2) | feature | Total revolving high credit / credit limit, USD. |
| `bc_util` | NUMERIC(8,2) | feature | Total balance divided by credit limit across bankcard accounts, percent. |
| `acc_open_past_24mths` | SMALLINT | feature | Number of accounts opened in the past 24 months. |
| `collections_12_mths_ex_med` | SMALLINT | feature | Collections in the last 12 months, excluding medical collections. |
| `delinq_amnt` | NUMERIC(14,2) | feature | Past-due amount on accounts that are currently delinquent, USD. |
| `tax_liens` | SMALLINT | feature | Number of tax liens. |
| `chargeoff_within_12_mths` | SMALLINT | feature | Number of charge-offs within the last 12 months, at application. |
| `num_tl_90g_dpd_24m` | SMALLINT | feature | Number of accounts 90 or more days past due in the last 24 months. |
| `percent_bc_gt_75` | NUMERIC(8,2) | feature | Percentage of bankcard accounts above 75% of their limit. |
| `num_actv_bc_tl` | SMALLINT | feature | Number of currently active bankcard accounts. |
| `grade` | CHAR(1) | reference | Lending Club's assigned loan grade (A to G). Comparison only. |
| `sub_grade` | CHAR(2) | reference | Lending Club's assigned sub-grade (A1 to G5). Comparison only. |
| `earliest_cr_line` | DATE | feature | Month the borrower's earliest credit line was opened (first day of month). Credit history length is derived from this. |

Conventions: percentages in this table are on a 0-100 scale as in the source (for example 18.5 means 18.5%).
Blank cells in the CSV load as NULL.

---

## 3. Synthetic ledger tables (SYNTHETIC data)

Fictional lender **Kestrel Credit Ltd**, USD, monthly, January 2023 to December 2025, with opening balances
at 31 December 2022 (period `2022-12`). Generated by `src/generate_ledgers.py` with seed 42. Every number is
simulated. Nothing here is real company data. Amounts are in USD. Rates and ratios are stored as fractions
(0.16 means 16%). Dates are ISO text (`YYYY-MM-DD`); `period` is text `YYYY-MM`.

### `dim_account`  (chart of accounts, 21 rows)

| Column | Type | Description |
|---|---|---|
| `account_id` | INTEGER | Primary key. 1xxx assets, 2xxx liabilities, 3xxx equity, 4xxx revenue, 5xxx-7xxx expenses. |
| `account_name` | VARCHAR(60) | Account name, for example Interest Income. |
| `account_type` | VARCHAR(10) | Asset, Liability, Equity, Revenue or Expense. |
| `statement` | CHAR(2) | BS (balance sheet) or PL (profit and loss). |
| `normal_balance` | CHAR(1) | D (debit) or C (credit): the side on which the account normally increases. Allowance for Credit Losses is a contra-asset with a credit balance. |
| `report_group` | VARCHAR(40) | Reporting subtotal, for example Operating Expenses. |

### `dim_cost_center`  (5 rows)

| Column | Type | Description |
|---|---|---|
| `cost_center_id` | CHAR(3) | Primary key: OPS, SMK, TEC, RSK or COR. |
| `cost_center_name` | VARCHAR(40) | Operations, Sales and Marketing, Technology, Risk and Collections, Corporate. |

### `dim_vendor`  (23 rows)

| Column | Type | Description |
|---|---|---|
| `vendor_id` | CHAR(4) | Primary key, for example V001. |
| `vendor_name` | VARCHAR(100) | Company name generated with Faker. Not a real supplier. May differ between Faker versions. |
| `category` | VARCHAR(40) | Advertising and Media, Cloud and Software, Facilities, Professional Services or Collections Agency. |

### `dim_period`  (37 rows)

| Column | Type | Description |
|---|---|---|
| `period` | CHAR(7) | Primary key, `YYYY-MM`. Includes the opening month `2022-12`. |
| `month_start` | DATE | First day of the month. |
| `month_end` | DATE | Last day of the month. |
| `fiscal_year` | SMALLINT | Calendar year (fiscal year equals calendar year). |
| `quarter` | CHAR(2) | Q1 to Q4. |
| `month_name` | CHAR(3) | Jan to Dec. |
| `is_opening` | BOOLEAN | True only for `2022-12`, which holds the opening balances. |

### `fact_gl_journal`  (general ledger, one row per journal line)

| Column | Type | Description |
|---|---|---|
| `journal_id` | VARCHAR(16) | Journal number, `JE-YYYYMM-NNN`. Every journal balances (debits equal credits). |
| `line_no` | SMALLINT | Line number within the journal. Primary key with `journal_id`. |
| `posting_date` | DATE | Posting date, always the month end. |
| `period` | CHAR(7) | Accounting period `YYYY-MM`. |
| `account_id` | INTEGER | Foreign key to `dim_account`. |
| `debit` | NUMERIC(18,2) | Debit amount (zero on credit lines). |
| `credit` | NUMERIC(18,2) | Credit amount (zero on debit lines). |
| `cost_center_id` | CHAR(3) | Foreign key to `dim_cost_center`. Filled on expense lines only; empty otherwise. |
| `vendor_id` | CHAR(4) | Foreign key to `dim_vendor`. Filled on vendor-invoiced expense lines only. |
| `source` | VARCHAR(30) | Type of transaction: OPENING, ORIGINATION, FEE_INCOME, INTEREST_INCOME, REPAYMENT, CHARGE_OFF, PROVISION, INTEREST_EXPENSE, ACCRUAL_SETTLEMENT, PAYROLL, OPEX_6100, OPEX_6200, OPEX_6300, OPEX_6400, DEPRECIATION, CAPEX, TAX, TAX_PAYMENT, BORROWING_DRAW, BORROWING_REPAY. |
| `memo` | VARCHAR(80) | Short description. |

Actual results only. The budget is in `fact_budget`.

### `fact_budget`  (monthly budget for P&L accounts)

| Column | Type | Description |
|---|---|---|
| `period` | CHAR(7) | Month `YYYY-MM` (2023-01 to 2025-12). |
| `account_id` | INTEGER | Foreign key to `dim_account`. P&L accounts only. |
| `cost_center_id` | CHAR(3) | Cost centre for expense lines; empty for revenue. |
| `budget_amount` | NUMERIC(18,2) | Planned amount, as a positive number for both revenue and expenses. Zero where nothing is planned. |

Budget is produced by running the same model with plan assumptions and no noise. It is complete for every period and account/cost-centre pair, so it joins cleanly to actuals.

### `fact_drivers`  (monthly business drivers, Actual and Budget)

Two rows per period: `scenario` is `Actual` or `Budget`. Rates are derived from the posted (rounded) amounts, so
price-and-volume variance analysis reconciles exactly.

| Column | Type | Description |
|---|---|---|
| `period` | CHAR(7) | Month `YYYY-MM`. |
| `scenario` | VARCHAR(6) | Actual or Budget. |
| `originations` | NUMERIC(18,2) | New loans disbursed in the month, USD. |
| `opening_loan_book` | NUMERIC(18,2) | Gross loans at the start of the month. |
| `repayments` | NUMERIC(18,2) | Principal repaid in the month. |
| `writeoffs` | NUMERIC(18,2) | Loans written off in the month. |
| `closing_loan_book` | NUMERIC(18,2) | Gross loans at month end: opening + originations - repayments - writeoffs. |
| `avg_loan_book` | NUMERIC(18,2) | Average of opening and closing loan book. |
| `interest_income` | NUMERIC(18,2) | Interest income for the month. |
| `yield_annual` | NUMERIC(10,6) | Annualised yield: interest income x 12 / average loan book. |
| `fee_income` | NUMERIC(18,2) | Origination fee income. |
| `fee_rate` | NUMERIC(10,6) | Fee income / originations. |
| `opening_borrowings` | NUMERIC(18,2) | Borrowings at the start of the month. |
| `interest_expense` | NUMERIC(18,2) | Interest on borrowings. |
| `cost_of_funds_annual` | NUMERIC(10,6) | Interest expense x 12 / opening borrowings. |
| `allowance_closing` | NUMERIC(18,2) | Allowance for credit losses at month end. |
| `allowance_coverage` | NUMERIC(10,6) | Allowance / closing loan book. |
| `provision` | NUMERIC(18,2) | Provision for credit losses: change in allowance + write-offs. |
| `chargeoff_rate_monthly` | NUMERIC(10,6) | Write-offs / opening loan book. |
| `headcount` | SMALLINT | Employees in the month. |
| `cost_per_head` | NUMERIC(12,2) | Average monthly salary cost per employee. |
| `marketing_pct_of_originations` | NUMERIC(10,6) | Marketing spend / originations. |

### Injected events (Actual run only)

These make the budget vs actual variances explainable:
- 2023: originations about 3% above plan.
- Jul-Dec 2024: competition cuts originations about 8% and yield by 0.6 percentage points; Jan-Jun 2025: yield stays 0.3 points below plan.
- Mar 2024: one-off technology cost of USD 120,000.
- Jul-Dec 2023 and Sep 2024-Mar 2025: hiring delayed, so salaries run under budget.
- Apr-Sep 2025: charge-offs run about 50% above plan; the allowance is raised from 4.5% towards 5.25% of loans.
- 2025: marketing about 10% over budget.

### Modelling assumptions and simplifications

- All amounts in USD; postings dated at month end; one business entity.
- Interest is collected in cash when earned (no accrued interest receivable). Fees are collected at origination.
- Fixed assets are held net of depreciation (no accumulated depreciation account).
- No closing entries. Balance sheet equity = share capital + opening retained earnings + cumulative net income, computed in SQL.
- 30% of vendor costs are accrued and paid the following month. Tax is accrued monthly on year-to-date profit at 25% and paid quarterly.
- Borrowings are drawn or repaid in USD 500,000 steps at month end to keep cash between USD 2.0M and 4.5M.
- Opening balances at 31 Dec 2022: cash 3.0M, loans 60.0M, allowance 2.7M, fixed assets 1.2M, other assets 0.5M, borrowings 40.0M, accrued expenses 0.12M, taxes payable 0.08M, share capital 12.0M, retained earnings as the balancing figure (9.8M).
