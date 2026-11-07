# Test results summary

**Result: final.** All 202 tests passed and all 32 data checks passed, with nothing skipped.

Recorded 2026-11-08 by `src/record_test_results.py` on Python 3.12.10 (Windows). Raw pytest output: `reports/test_results.txt`. Data check detail: `reports/data_checks.md`. Accuracy numbers as plain text: `reports/scorecard_accuracy.md`.

## 1. Automated tests (pytest)

**202 passed, 0 failed, 0 skipped** in 71 seconds, across 20 test files.

| Test file | What it checks | Passed | Failed | Skipped |
|---|---|---|---|---|
| `test_board_report.py` | The board report PDF: numbers tie to the extracts, pages are present, risk page is optional | 16 | 0 | 0 |
| `test_clean_lending_club.py` | The scorecard cleaning and the time-based split, on a small dirty mock sample | 13 | 0 | 0 |
| `test_data_checks.py` | Runs the data checks in src/run_data_checks.py as tests | 5 | 0 | 0 |
| `test_data_dictionary.py` | The data dictionary must document exactly the columns that exist | 3 | 0 | 0 |
| `test_excel_workbook.py` | The Excel workbook: tables, P&L and balance sheet formulas, DAX measure list and reference check | 2 | 0 | 0 |
| `test_leakage_tools.py` | The leakage audit and check tools: columns known only after the loan outcome are caught | 6 | 0 | 0 |
| `test_ledgers.py` | Integrity checks for the synthetic ledgers (run: pytest) | 10 | 0 | 0 |
| `test_metric_definitions.py` | The metric dictionary must agree with the data: identities hold and reference values match | 15 | 0 | 0 |
| `test_prepare_lending_club.py` | The Lending Club prep script, using a small mock file in the real format | 9 | 0 | 0 |
| `test_qlik_kit.py` | The Qlik Sense finance kit: load script, measures and the exported CSV files | 18 | 0 | 0 |
| `test_qlik_risk_kit.py` | The Qlik Sense risk kit: export, load script and measures (mock scorecard run, no Qlik needed) | 12 | 0 | 0 |
| `test_record_test_results.py` | Recording scorecard accuracy numbers in the test results file | 9 | 0 | 0 |
| `test_run_monthly_refresh.py` | The monthly refresh runner: status file, log, failure handling and the email step | 7 | 0 | 0 |
| `test_scorecard_model.py` | The scorecard: logistic regression, scaling, AUC / KS / Gini and risk bands (mock data) | 19 | 0 | 0 |
| `test_scorecard_stability.py` | The PSI stability check (mock data with known drift) | 13 | 0 | 0 |
| `test_send_report_email.py` | The email sender (no network: dry run and message building only) | 5 | 0 | 0 |
| `test_sql_schema.py` | Static checks on the SQL files (no database needed) | 4 | 0 | 0 |
| `test_sql_views.py` | Checks the P&L views against the Python reference implementation | 16 | 0 | 0 |
| `test_variance.py` | Variance reference checks that need no database: effects add up exactly (M30-M33) | 4 | 0 | 0 |
| `test_woe_binning.py` | WoE binning and IV, on mock data where the true relationships are known | 16 | 0 | 0 |

## 2. Data checks

**32 passed, 0 failed, 0 skipped** of 32. Ledger checks (L) read the synthetic ledger files, SQL checks (S) read the PostgreSQL views, Lending Club checks (R) read the real loan sample.

| Area | Checks | Passed | Failed | Skipped |
|---|---|---|---|---|
| Ledger | 17 | 17 | 0 | 0 |
| Lending Club | 4 | 4 | 0 | 0 |
| SQL | 11 | 11 | 0 | 0 |

| ID | Area | Check | Expected | Actual | Result |
|---|---|---|---|---|---|
| L01 | Ledger | Every journal has debits equal to credits (largest gap across all journals) | 0.00 (tolerance 0.05) | 0.00 | PASS |
| L02 | Ledger | Trial balance: total debits minus total credits across the whole ledger | 0.00 (tolerance 0.05) | 0.00 | PASS |
| L03 | Ledger | No ledger line has both a debit and a credit, or a negative amount | 0 lines | 0 lines | PASS |
| L04 | Ledger | Every key in the ledger and budget exists in its dimension table | 0 orphan keys | 0 orphan keys | PASS |
| L05 | Ledger | Periods run month by month from 2022-12 (opening) to 2025-12 with no gap | 37 periods | 37 periods | PASS |
| L06 | Ledger | Budget has one row per account and cost centre for each of the 36 months, no duplicates | 540 rows | 540 rows, 540 distinct | PASS |
| L07 | Ledger | Drivers cover all 36 months for both Actual and Budget | 36 + 36 rows | 36 + 36 rows | PASS |
| L08 | Ledger | Loan book roll-forward: opening + originations - repayments - write-offs = closing (largest gap) | 0.00 (tolerance 0.05) | 0.00 | PASS |
| L09 | Ledger | Balance sheet: assets = liabilities + equity in every one of the 37 month-ends (largest gap) | 0.00 (tolerance 0.05) | 0.00 | PASS |
| L10 | Ledger | Allowance for credit losses is never above gross loans (months where it is) | 0 (tolerance 0) | 0 | PASS |
| L11-2023 | Ledger | P&L net income FY2023 equals credits minus debits on all P&L accounts in the ledger | 0.00 (tolerance 0.05) | 0.00 | PASS |
| L11-2024 | Ledger | P&L net income FY2024 equals credits minus debits on all P&L accounts in the ledger | 0.00 (tolerance 0.05) | 0.00 | PASS |
| L11-2025 | Ledger | P&L net income FY2025 equals credits minus debits on all P&L accounts in the ledger | 0.00 (tolerance 0.05) | 0.00 | PASS |
| L12 | Ledger | P&L identity: revenue - interest expense - provision - operating expenses - tax = net income (largest gap) | 0.00 (tolerance 0.05) | 0.00 | PASS |
| L13 | Ledger | Budget variances: the sum of all account variances equals the net income variance (largest gap by year) | 0.00 (tolerance 0.05) | 0.00 | PASS |
| L14 | Ledger | Variance drivers: volume + rate + other effect equals the variance on every row (largest gap) | 0.00 (tolerance 0.011) | 0.00 | PASS |
| L15 | Ledger | Budget net income over 36 months equals revenue minus costs in fact_budget | 0.00 (tolerance 0.05) | -0.00 | PASS |
| S01 | SQL | Row counts of the 7 loaded tables equal the CSV files | all 7 equal | all equal | PASS |
| S02 | SQL | Balance sheet view: largest absolute balance_check over the 37 month-ends | 0.00 (tolerance 0.005) | 0.00 | PASS |
| S03 | SQL | Balance sheet view total assets match the Python reference (largest gap) | 0.00 (tolerance 0.05) | 0.00 | PASS |
| S04 | SQL | P&L annual view net income vs the ledger table, credits minus debits (largest gap by year) | 0.00 (tolerance 0.05) | 0.00 | PASS |
| S05 | SQL | P&L views: quarters vs years and income statement vs monthly view (largest gap) | 0.00 (tolerance 0.05) | 0.00 | PASS |
| S06 | SQL | Budget vs actual: actual net income vs the P&L view (largest monthly gap) | 0.00 (tolerance 0.05) | 0.00 | PASS |
| S07 | SQL | Budget vs actual: budget net income vs the budget table (largest gap by year) | 0.00 (tolerance 0.05) | 0.00 | PASS |
| S08 | SQL | Budget vs actual: sum of account variances vs net income variance (largest gap by year) | 0.00 (tolerance 0.05) | 0.00 | PASS |
| S09 | SQL | Budget vs actual: quarters and cost centres vs annual (largest gap) | 0.00 (tolerance 0.05) | 0.00 | PASS |
| S10 | SQL | Variance drivers: volume + rate + other vs variance (largest gap on any row) | 0.00 (tolerance 0.011) | 0.00 | PASS |
| S11 | SQL | Views are populated (row counts as expected) | {'fin.v_pl_monthly': 36, 'fin.v_balance_sheet_monthly': 37, 'fin.v_bva_summary': 396, 'fin.v_variance_drivers': 396, 'fin.v_bva_summary_annual': 33} | {'fin.v_pl_monthly': 36, 'fin.v_balance_sheet_monthly': 37, 'fin.v_bva_summary': 396, 'fin.v_variance_drivers': 396, 'fin.v_bva_summary_annual': 33} | PASS |
| R01 | Lending Club | Loans where the default flag disagrees with 'Charged Off' status | 0 (tolerance 0) | 0 | PASS |
| R02 | Lending Club | Loans outside the design (36 months, issued 2013-2015) | 0 (tolerance 0) | 0 | PASS |
| R03 | Lending Club | Default rate per issue year is plausible (between 5% and 35%) | each year 5%-35% | 2013: 12.2%, 2014: 13.8%, 2015: 14.8% | PASS |
| R04 | Lending Club | Loans with FICO low above high, or issue year not matching issue date | 0 (tolerance 0) | 0 | PASS |

## 3. Scorecard model metrics (real Lending Club data)

Lending Club 36-month loans. Train issue years 2013, 2014, test 2015 (time-based split, so the test year is the future the model has not seen). Logistic regression on 14 WoE features; score 600 points at 50:1 odds, 20 points to double the odds.

### Discrimination (higher is better)

| Model / data | Loans | Default rate | AUC | Gini | KS |
|---|---|---|---|---|---|
| Scorecard, train | 120,000 | 12.96% | 0.662 | 0.325 | 0.234 |
| Scorecard, test | 60,000 | 14.83% | 0.675 | 0.350 | 0.256 |
| Lending Club sub_grade (benchmark), test | 60,000 | 14.83% | 0.683 | 0.365 | 0.263 |
| Lending Club int_rate (benchmark), test | 60,000 | 14.83% | 0.682 | 0.364 | 0.263 |
| FICO score alone (benchmark), test | 60,000 | 14.83% | 0.602 | 0.204 | 0.155 |

### Default rate by risk band, test year (A = safest)

| Band | Score range | Loans | Default rate | Predicted PD | Lift |
|---|---|---|---|---|---|
| A | 561 and above | 13,463 | 5.35% | 5.24% | 0.36 |
| B | 550 to 560 | 11,431 | 10.35% | 8.81% | 0.70 |
| C | 540 to 549 | 12,275 | 14.00% | 12.04% | 0.94 |
| D | 530 to 539 | 11,097 | 18.79% | 16.01% | 1.27 |
| E | below 530 | 11,734 | 27.21% | 23.96% | 1.83 |

Default rate rises from band A to band E at every step: **yes**.

### Stability (PSI, metric M56: under 0.10 stable, 0.10 to 0.25 moderate, over 0.25 large)

Score PSI, test against train: **0.003** (stable). Features: 14 stable, 0 moderate, 0 large (largest: inq_last_6mths, 0.039).

| Issue year | Data | Default rate | Predicted PD | AUC | Gini | KS | Score PSI |
|---|---|---|---|---|---|---|---|
| 2013 | train | 12.16% | 12.67% | 0.658 | 0.317 | 0.230 | 0.002 |
| 2014 | train | 13.76% | 13.25% | 0.665 | 0.330 | 0.237 | 0.002 |
| 2015 | test | 14.83% | 12.97% | 0.675 | 0.350 | 0.256 | 0.003 |

### Calibration

Test year: predicted default rate 12.97%, actual 14.83% (gap +1.86%). The model ranks borrowers consistently but under-predicts the level; an intercept recalibration on recent loans would fix it.

## 4. How these results were produced

```
python -m pytest -v --junitxml=reports/pytest_results.xml > reports/test_results.txt
python src/run_data_checks.py          # with FIN_PG_DSN set, so the SQL and loan checks run
python src/record_test_results.py
```
