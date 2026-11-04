# Metric dictionary

Every metric used in the SQL views, the Qlik Sense dashboards and the monthly board pack is defined here, so
finance teams can trust and reproduce the numbers. Each metric has an ID (M01, M02, ...) that the SQL view
and dashboard labels refer to.

**Status:** *Defined* = built and checked against the data. *Draft* = definition agreed, to be confirmed when the
metric is built. Updated as each metric is built; finalised on 1 Nov 2026.

## Conventions

| Topic | Rule |
|---|---|
| Currency and unit | USD, whole dollars unless stated. Ratios are shown as percentages. |
| Period | Calendar month (`period` = `YYYY-MM`). A fiscal year is the calendar year (FY2024 = Jan-Dec 2024). |
| Source of actuals | `fact_gl_journal` joined to `dim_account` on `account_id`. |
| Sign of P&L amounts | Shown as positive numbers. Revenue = credits minus debits. Expenses = debits minus credits. |
| Variance sign | **Favourable variance is positive**: Actual minus Budget for revenue, Budget minus Actual for expenses and provisions. |
| Annualising | A metric measured over n months is multiplied by 12 / n before being shown as an annual rate. |
| Averages | Average loan book = mean of the monthly `avg_loan_book` (opening and closing book averaged within each month). |
| Equity | The ledger has no closing entries. Equity = share capital + opening retained earnings + cumulative net income. |
| Zero denominators | A ratio with a zero denominator is shown as blank, never as 0. |

Reference implementation: `src/finance_metrics.py`. SQL and dashboard results must match it (see section 6).
SQL: M01-M16 are implemented in `sql/02_pl_views.sql` (`fin.v_pl_monthly`, `fin.v_pl_quarterly`, `fin.v_pl_annual`) and M20-M28 in `sql/03_balance_sheet_views.sql` (`fin.v_balance_sheet_monthly`, `fin.v_balance_sheet_year_end`).

## 1. Profit and loss

| ID | Metric | Definition | Formula | Source | Status |
|---|---|---|---|---|---|
| M01 | Interest Income | Interest earned on the loan book. | Account 4000: credits - debits | `fact_gl_journal` | Defined |
| M02 | Fee Income | Origination fees on new loans. | Account 4100: credits - debits | `fact_gl_journal` | Defined |
| M03 | Total Revenue | Income before funding costs and credit losses. | M01 + M02 | derived | Defined |
| M04 | Interest Expense | Cost of borrowings. | Account 5000: debits - credits | `fact_gl_journal` | Defined |
| M05 | Net Interest Income (NII) | Interest earned minus interest paid. | M01 - M04 | derived | Defined |
| M06 | Net Revenue | Income after funding costs, before credit losses and operating costs. | M05 + M02 (equals M03 - M04) | derived | Defined |
| M07 | Provision for Credit Losses | Expense that tops up the allowance and covers write-offs. | Account 5100: debits - credits | `fact_gl_journal` | Defined |
| M08 | Operating Expenses | Running costs: salaries, marketing, technology, rent and admin, collections, depreciation. | Accounts 6000-6500: debits - credits | `fact_gl_journal` | Defined |
| M09 | Pre-tax Profit | Profit before income tax. | M06 - M07 - M08 | derived | Defined |
| M10 | Income Tax | Tax charge, accrued monthly on year-to-date profit at 25%. | Account 7000: debits - credits | `fact_gl_journal` | Defined |
| M11 | Net Income | Profit after tax. | M09 - M10 | derived | Defined |
| M12 | Net Profit Margin | Share of revenue kept as profit. | M11 / M03 | derived | Defined |
| M13 | Cost-to-Income Ratio | Operating costs per dollar of net revenue. Lower is better. | M08 / M06 | derived | Defined |
| M14 | Yield on Loans | Annualised interest rate earned on the average loan book. | M01 x (12 / n) / average loan book | `fact_gl_journal`, `fact_drivers` | Defined |
| M15 | Cost of Funds | Annualised interest rate paid on opening borrowings. | M04 x (12 / n) / average opening borrowings | `fact_gl_journal`, `fact_drivers` | Defined |
| M16 | Net Interest Margin (NIM) | Annualised NII as a percentage of the average loan book. | M05 x (12 / n) / average loan book | `fact_gl_journal`, `fact_drivers` | Defined |

## 2. Balance sheet and capital (month-end balances)

| ID | Metric | Definition | Formula | Source | Status |
|---|---|---|---|---|---|
| M20 | Gross Loan Book | Loans outstanding before the allowance. | Account 1100 balance (cumulative debits - credits to month end) | `fact_gl_journal` | Defined |
| M21 | Allowance for Credit Losses | Reserve held against expected losses. | Account 1110 balance, shown as a positive number | `fact_gl_journal` | Defined |
| M22 | Net Loans | Loan book after the allowance. | M20 - M21 | derived | Defined |
| M23 | Allowance Coverage | Reserve as a share of the loan book. | M21 / M20 | derived | Defined |
| M24 | Total Assets | Cash, net loans, fixed assets and other assets. | Accounts 1000 + 1100 + 1110 + 1200 + 1300 | `fact_gl_journal` | Defined |
| M25 | Total Liabilities | Borrowings, accrued expenses, taxes payable. | Accounts 2000 + 2100 + 2200 | `fact_gl_journal` | Defined |
| M26 | Total Equity | Owners' funds. | Account 3000 + account 3100 + cumulative M11 | `fact_gl_journal` | Defined |
| M27 | Debt-to-Equity | Borrowings per dollar of equity. | Account 2000 balance / M26 | derived | Defined |
| M28 | Return on Equity (annualised) | Profit earned on owners' funds. | M11 x (12 / n) / average of the month-end M26 values in the period | derived | Defined |

Check that must hold every month: **M24 = M25 + M26** (the balance sheet balances).

## 3. Budget variance

Variances compare `fact_gl_journal` (actual) with `fact_budget`. Budget and actual share the same accounts and cost centres.

| ID | Metric | Definition | Formula | Source | Status |
|---|---|---|---|---|---|
| M30 | Variance | Difference to budget, favourable positive. | Revenue lines: Actual - Budget. Expense and provision lines: Budget - Actual. | `fact_gl_journal`, `fact_budget` | Defined |
| M31 | Variance % | Variance relative to budget. | M30 / Budget. Blank if Budget is zero. | derived | Defined |
| M32 | Volume Effect | Part of the variance caused by a different business volume than planned. | (Volume actual - Volume budget) x Rate budget | `fact_drivers` | Defined |
| M33 | Rate Effect | Part of the variance caused by a different price or rate than planned. | (Rate actual - Rate budget) x Volume actual | `fact_drivers` | Defined |

M32 + M33 (+ other effect) equals the variance M30 exactly. In the SQL views (`fin.v_variance_drivers`) all
effects use the M30 sign, so a **positive effect always helps profit**. Rates are implied: rate = amount / volume,
for actual and for budget, which is what makes the split exact (it equals the yield, fee rate, cost of funds and
so on shown in `fact_drivers`, apart from rounding). Driver pairs (volume, rate):

| P&L line | Volume driver | Rate driver |
|---|---|---|
| Interest Income | `avg_loan_book` | yield (monthly, = `yield_annual` / 12) |
| Fee Income | `originations` | fee rate |
| Interest Expense | `opening_borrowings` | cost of funds (monthly) |
| Provision for Credit Losses | `opening_loan_book` | provision rate on the opening book |
| Salaries and Benefits | `headcount` | cost per head |
| Marketing | `originations` | marketing % of originations |

Lines without a driver (technology, rent and administration, collections, depreciation, income tax) show their
whole variance as the **other effect**; for technology this is the one-off cost of March 2024. The sum of all
line variances equals the net income variance.

Reading the split: a **volume effect** says "the business was bigger or smaller than planned"; a **rate effect**
says "each unit earned or cost more or less than planned" (price for income lines, cost per unit for cost lines).

## 4. Portfolio credit risk (company ledger)

| ID | Metric | Definition | Formula | Source | Status |
|---|---|---|---|---|---|
| M40 | Charge-off Rate (monthly) | Share of the opening loan book written off in the month. | `writeoffs` / `opening_loan_book` | `fact_drivers` | Defined |
| M41 | Charge-off Rate (annualised) | Simple annualisation. | M40 x 12 | `fact_drivers` | Defined |
| M42 | Loan Default Rate (Lending Club) | Share of finished loans that were charged off, in any group (year, grade, score band). | Charged Off / (Fully Paid + Charged Off) | `lending_club_sample` | Defined |

M40 comes from the synthetic ledger and M42 from the real Lending Club sample. The two are not reconciled.

## 5. Credit scorecard metrics (to be confirmed when built, 28-29 Oct)

| ID | Metric | Definition | Formula | Status |
|---|---|---|---|---|
| M50 | Weight of Evidence (WoE) | How strongly a bin separates good loans from bad. | ln(share of all non-defaults in bin / share of all defaults in bin). A constant of 0.5 is added to each bin's counts so an empty cell never gives an infinite value. Bins are fitted on the train loans only (`src/woe_binning.py`). | Defined |
| M51 | Information Value (IV) | Predictive strength of a feature. | Sum over bins of (share non-defaults - share defaults) x WoE. Rule of thumb: under 0.02 useless, 0.02-0.1 weak, 0.1-0.3 medium, 0.3-0.5 strong, over 0.5 suspicious (check for leakage). Features are selected at IV 0.02 to 0.5; IV is also computed on the test loans with the same bins as a stability check. | Defined |
| M52 | AUC | Probability a random defaulter is scored riskier than a random non-defaulter. | Area under the ROC curve on the 2015 test set (`src/scorecard_model.py`, also reported on train) | Defined |
| M53 | Gini | Rescaled AUC. | 2 x AUC - 1 | Defined |
| M54 | KS statistic | Largest gap between the cumulative bad and good distributions across score. | max abs(cumulative % defaults - cumulative % non-defaults) | Defined |
| M55 | Default Rate by Score Band | Observed default rate in each risk band. | M42 per band. Five bands A (safest) to E, cut at the 20th, 40th, 60th and 80th percentile of the train scores and applied unchanged to the test loans; should rise steadily from A to E | Defined |
| M56 | Population Stability Index (PSI) | How much the score distribution (or a feature's bin mix) moved between train and a later period. | Sum over bins of (share later - share train) x ln(share later / share train). Score bins are the 10 train score deciles (also reported on the 5 risk bands); feature bins are the WoE bins fitted on train, including Missing. Shares are floored at 0.0001. Under 0.10 stable, 0.10-0.25 moderate shift, over 0.25 large shift. | Defined |

Train = loans issued 2013-2014. Test = loans issued 2015. `grade`, `sub_grade` and `int_rate` are not used as inputs.

## 6. Reference values (check the SQL views and dashboards against these)

Computed from the committed synthetic ledgers (seed 42) by `src/finance_metrics.py`. A test fails if this
section and the data disagree. USD, whole dollars.

**Profit and loss**

| Year | Total Revenue (M03) | Interest Expense (M04) | NII (M05) | Net Revenue (M06) | Provision (M07) | Opex (M08) | Pre-tax Profit (M09) | Tax (M10) | Net Income (M11) |
|---|---|---|---|---|---|---|---|---|---|
| FY2023 | 12,797,425 | 4,073,333 | 7,741,115 | 8,724,092 | 2,516,660 | 4,223,462 | 1,983,970 | 495,993 | 1,487,978 |
| FY2024 | 16,471,638 | 5,806,667 | 9,594,326 | 10,664,971 | 2,619,693 | 4,840,818 | 3,204,460 | 801,115 | 2,403,345 |
| FY2025 | 20,335,794 | 7,253,333 | 11,780,265 | 13,082,461 | 4,819,721 | 5,646,274 | 2,616,465 | 654,116 | 1,962,349 |

**Ratios and balances**

| Year | Cost-to-Income (M13) | Net Profit Margin (M12) | Yield (M14) | NIM (M16) | Gross Loans, year end (M20) | Borrowings, year end | Equity, year end (M26) | Debt-to-Equity (M27) | ROE (M28) |
|---|---|---|---|---|---|---|---|---|---|
| FY2023 | 48.4% | 11.6% | 16.0% | 10.5% | 86,976,200 | 63,500,000 | 23,287,978 | 2.73x | 6.6% |
| FY2024 | 45.4% | 14.6% | 15.7% | 9.8% | 107,259,535 | 80,500,000 | 25,691,322 | 3.13x | 9.8% |
| FY2025 | 43.2% | 9.6% | 15.8% | 9.8% | 132,329,507 | 101,500,000 | 27,653,672 | 3.67x | 7.4% |

## Change log

| Date | Change |
|---|---|
| 28 Oct 2026 | First version: P&L, balance sheet, variance and portfolio metrics defined; scorecard metrics drafted. |
| 30 Oct 2026 | P&L metrics M01-M16 implemented as SQL views and verified against the reference values in section 6. |
| 30 Oct 2026 | Balance sheet metrics M20-M28 implemented as SQL views and verified; ROE (M28) now Defined and added to the reference values. |
| 3 Nov 2026 | Variance metrics M30-M33 implemented as SQL views (`04_budget_vs_actual_views.sql`); volume and rate effects use implied rates and the M30 sign; salaries and provision added as driver lines. |
| 4 Nov 2026 | Scorecard metrics M50 (WoE) and M51 (IV) implemented in `src/woe_binning.py` and set to Defined. |
| 4 Nov 2026 | Scorecard metrics M52 (AUC), M53 (Gini), M54 (KS) and M55 (default rate by band) implemented in `src/scorecard_model.py` and set to Defined. Score scaling: 600 points at 50:1 odds, 20 points to double the odds. |
| 4 Nov 2026 | Scorecard metric M56 (PSI) implemented in `src/scorecard_stability.py` and set to Defined: score PSI on train deciles and risk bands, feature PSI on the fitted WoE bins, by issue year. |
