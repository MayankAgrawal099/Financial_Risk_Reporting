# Scorecard data preparation

Written by `src/clean_lending_club.py`. All parameters below were learned on the **train** loans only and applied unchanged to the test loans. Raw parameters: `docs/scorecard_data_prep.json`.

## 1. Time-based split by issue year

| Set | Issue years | Loans | Defaults | Default rate |
|---|---|---|---|---|
| Train | 2013-2014 | 120,000 | 15,552 | 12.96% |
| Test | 2015 | 60,000 | 8,899 | 14.83% |

Default rate by issue year:

| Issue year | Loans | Default rate |
|---|---|---|
| 2013 | 60,000 | 12.16% |
| 2014 | 60,000 | 13.76% |
| 2015 | 60,000 | 14.83% |

No loan appears in both sets, and every train loan was issued before every test loan.

## 2. Columns dropped

| Column | Reason |
|---|---|
| `term_months` | constant in train |
| `application_type` | constant in train |

## 3. Features kept

- Numeric (28): `loan_amnt`, `annual_inc`, `dti`, `delinq_2yrs`, `inq_last_6mths`, `mths_since_last_delinq`, `mths_since_last_record`, `open_acc`, `pub_rec`, `revol_bal`, `revol_util`, `total_acc`, `mort_acc`, `pub_rec_bankruptcies`, `tot_cur_bal`, `total_rev_hi_lim`, `bc_util`, `acc_open_past_24mths`, `collections_12_mths_ex_med`, `delinq_amnt`, `tax_liens`, `chargeoff_within_12_mths`, `num_tl_90g_dpd_24m`, `percent_bc_gt_75`, `num_actv_bc_tl`, `credit_history_years`, `fico_avg`, `loan_to_income`
- Categorical (6): `emp_length`, `home_ownership`, `verification_status`, `purpose`, `addr_state`, `initial_list_status`
- Reference only, not features: `int_rate`, `grade`, `sub_grade`, `installment`

## 4. Missing values

Policy: blank means "never" for `mths_since_last_delinq` and `mths_since_last_record`; any column with more than 5% missing in train keeps its blanks (the WoE step gives "Missing" its own bin); columns with up to 5% are filled with the train median; text columns get "Missing".

| Column | Train missing | Treatment |
|---|---|---|
| `loan_amnt` | 0.0% | none missing |
| `annual_inc` | 0.0% | none missing |
| `dti` | 0.0% | none missing |
| `delinq_2yrs` | 0.0% | none missing |
| `inq_last_6mths` | 0.0% | none missing |
| `mths_since_last_delinq` | 52.7% | kept as Missing (own bin) |
| `mths_since_last_record` | 84.2% | kept as Missing (own bin) |
| `open_acc` | 0.0% | none missing |
| `pub_rec` | 0.0% | none missing |
| `revol_bal` | 0.0% | none missing |
| `revol_util` | 0.1% | filled with train median 56.7 |
| `total_acc` | 0.0% | none missing |
| `mort_acc` | 0.0% | none missing |
| `pub_rec_bankruptcies` | 0.0% | none missing |
| `tot_cur_bal` | 0.0% | none missing |
| `total_rev_hi_lim` | 0.0% | none missing |
| `bc_util` | 1.0% | filled with train median 69.1 |
| `acc_open_past_24mths` | 0.0% | none missing |
| `collections_12_mths_ex_med` | 0.0% | none missing |
| `delinq_amnt` | 0.0% | none missing |
| `tax_liens` | 0.0% | none missing |
| `chargeoff_within_12_mths` | 0.0% | none missing |
| `num_tl_90g_dpd_24m` | 0.0% | none missing |
| `percent_bc_gt_75` | 1.0% | filled with train median 50 |
| `num_actv_bc_tl` | 0.0% | none missing |
| `credit_history_years` | 0.0% | none missing |
| `fico_avg` | 0.0% | none missing |
| `loan_to_income` | 0.0% | none missing |

Missing share by issue year, for columns with any blanks (a jump between train and test years means the data changed over time):

| Column | 2013 | 2014 | 2015 | Drift flag |
|---|---|---|---|---|
| `annual_inc` | 0.0% | 0.0% | 0.0% |  |
| `dti` | 0.0% | 0.0% | 0.0% |  |
| `mths_since_last_delinq` | 56.6% | 48.8% | 48.0% |  |
| `mths_since_last_record` | 87.3% | 81.0% | 81.4% |  |
| `revol_util` | 0.1% | 0.0% | 0.1% |  |
| `bc_util` | 0.8% | 1.2% | 1.0% |  |
| `percent_bc_gt_75` | 0.8% | 1.2% | 1.0% |  |
| `loan_to_income` | 0.0% | 0.0% | 0.0% |  |

## 5. Other rules

- Impossible values set to missing (negative dti, zero income, FICO outside 300-850, ...); counts below.
- Numbers capped at the train 0.5% and 99.5% percentiles.
- Categories under 0.5% of train loans, and categories never seen in train, become "Other".
- `home_ownership` values ANY and NONE become OTHER.
- Derived: `credit_history_years` (issue date minus earliest credit line), `fico_avg` (mean of low and high), `loan_to_income` (loan amount / annual income). The raw FICO low and high columns are replaced by `fico_avg`.
- `installment` is reference only because it is computed from the interest rate, which is Lending Club's own price of risk.

## 6. What was changed (counts)

| Change | Train | Test |
|---|---|---|
| Rows in | 120,000 | 60,000 |
| Duplicate ids removed | 0 | 0 |
| Rows out | 120,000 | 60,000 |
| Impossible values set to missing: `annual_inc` | 0 | 1 |
| Filled with train median: `bc_util` | 1,212 | 616 |
| Filled with train median: `percent_bc_gt_75` | 1,174 | 627 |
| Filled with train median: `revol_util` | 58 | 34 |
| Capped at train percentile: `acc_open_past_24mths` | 494 | 615 |
| Capped at train percentile: `annual_inc` | 996 | 692 |
| Capped at train percentile: `bc_util` | 583 | 271 |
| Capped at train percentile: `chargeoff_within_12_mths` | 118 | 54 |
| Capped at train percentile: `collections_12_mths_ex_med` | 74 | 80 |
| Capped at train percentile: `credit_history_years` | 1,162 | 789 |
| Capped at train percentile: `delinq_2yrs` | 428 | 337 |
| Capped at train percentile: `delinq_amnt` | 348 | 255 |
| Capped at train percentile: `dti` | 1,193 | 1,710 |
| Capped at train percentile: `fico_avg` | 537 | 330 |
| Capped at train percentile: `inq_last_6mths` | 224 | 3 |
| Capped at train percentile: `loan_amnt` | 419 | 295 |
| Capped at train percentile: `loan_to_income` | 1,071 | 1,457 |
| Capped at train percentile: `mort_acc` | 415 | 135 |
| Capped at train percentile: `mths_since_last_delinq` | 346 | 198 |
| Capped at train percentile: `mths_since_last_record` | 95 | 85 |
| Capped at train percentile: `num_actv_bc_tl` | 348 | 298 |
| Capped at train percentile: `num_tl_90g_dpd_24m` | 331 | 266 |
| Capped at train percentile: `open_acc` | 969 | 763 |
| Capped at train percentile: `pub_rec` | 360 | 336 |
| Capped at train percentile: `pub_rec_bankruptcies` | 132 | 146 |
| Capped at train percentile: `revol_bal` | 1,200 | 829 |
| Capped at train percentile: `revol_util` | 1,187 | 838 |
| Capped at train percentile: `tax_liens` | 396 | 359 |
| Capped at train percentile: `tot_cur_bal` | 1,198 | 667 |
| Capped at train percentile: `total_acc` | 716 | 633 |
| Capped at train percentile: `total_rev_hi_lim` | 1,191 | 817 |
| Grouped into Other: `addr_state` | 4,001 | 2,457 |
| Grouped into Other: `purpose` | 837 | 234 |
