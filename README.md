# Financial Analytics and Risk Reporting System

Automated finance reporting for a fictional lender: profit and loss, balance sheet, budget versus actual with variance drivers, a credit risk scorecard, interactive dashboards and a monthly board pack.

> **Status:** project setup. Components are added day by day; see the checklist at the bottom.

## What this project covers

1. Finance data: synthetic company ledgers (Faker, fixed random seed) for P&L, balance sheet and budget vs actual.
2. Credit risk data: a sample of the public Lending Club loan data for the scorecard.
3. P&L and balance sheet summaries built with SQL and Power Pivot.
4. Budget vs actual and variance analysis with clear drivers.
5. A credit risk scorecard in Python (logistic regression, weight of evidence) with risk bands.
6. Interactive dashboards in Qlik Sense.
7. A Power Automate flow that refreshes the data and emails the monthly board report as a PDF.
8. A metric dictionary defining every metric.

**Data note:** the ledgers are synthetic and the loan data is real. They are two separate modules of one fictional lender and are not reconciled with each other.

## Repository structure

```
financial-risk-reporting/
├── data/
│   ├── raw/          # original downloads (git-ignored, too large)
│   ├── processed/    # trimmed and sampled data (git-ignored)
│   └── synthetic/    # generated ledgers (7 CSVs)
├── docs/
│   ├── architecture/             # architecture diagram
│   ├── screenshots/              # Power Pivot and other evidence screenshots
│   ├── data_dictionary.md        # every table and column, real vs synthetic
│   ├── metric_dictionary.md      # every metric, formula, reference values
│   ├── dax_measures.md           # Power Pivot relationships and DAX measures
│   ├── lending_club_profile.json # sample profile
│   ├── lending_club_column_audit.csv
│   └── leakage_check.csv
├── sql/
│   ├── 00_create_schema.sql          # schemas fin and risk, tables
│   ├── 02_pl_views.sql               # P&L views
│   ├── 03_balance_sheet_views.sql    # balance sheet views
│   └── 04_budget_vs_actual_views.sql # budget vs actual and variance drivers
├── excel/
│   └── DA04_PowerPivot_Model.xlsx    # data tables, P&L / balance sheet summaries, Power Pivot model
├── src/              # generate_ledgers, prepare_lending_club, load_to_postgres,
│                     # finance_metrics, run_data_checks, build_excel_workbook, leakage tools
├── notebooks/        # exploration and scorecard notebooks
├── dashboards/       # Qlik Sense exports and screenshots
├── automation/       # Power Automate flow documentation
├── reports/          # test_results.txt, data_checks.md/.csv, board pack PDF
├── tests/            # data integrity, leakage, dictionary, SQL and Excel checks
├── pytest.ini
├── requirements.txt
└── README.md
```

## Setup

Requires Python 3.10 or newer.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## How to run (data preparation)

```bash
# 1. Synthetic company ledgers (writes data/synthetic/*.csv, seed 42)
python src/generate_ledgers.py

# 2. Lending Club sample. Download "All Lending Club loan data" from Kaggle, put
#    accepted_2007_to_2018q4.csv (or the .csv.gz) in data/raw/. Do NOT open it in Excel:
#    Excel cuts it at 1,048,575 rows and rewrites the dates.
python src/prepare_lending_club.py --input data/raw/accepted_2007_to_2018q4.csv

# 3. Leakage audit and check (writes docs/lending_club_column_audit.csv and docs/leakage_check.csv)
python src/audit_lending_club_columns.py --input data/raw/accepted_2007_to_2018q4.csv
python src/check_leakage.py

# 4. PostgreSQL: create an empty database first (CREATE DATABASE fin_risk;), then load everything.
#    Keep the password out of git: set it in the connection string or in FIN_PG_DSN.
python src/load_to_postgres.py --dsn postgresql://postgres:YOUR_PASSWORD@localhost:5432/fin_risk --lending-club

# 4b. Excel workbook (tables, P&L and balance sheet formulas, DAX list, Power Pivot guide)
python src/build_excel_workbook.py

# 4b. Excel workbook (needs openpyxl: pip install openpyxl); then follow its 'Data Model Guide' sheet
python src/build_excel_workbook.py

# 4c. Scorecard data: clean the sample and split by issue year (train 2013-2014, test 2015).
#     Writes data/processed/lc_train.csv, lc_test.csv and docs/scorecard_data_prep.md
python src/clean_lending_club.py

# 5. Data checks: balance sheet balances, P&L matches the ledger, variances add up.
#    Writes reports/data_checks.md and reports/data_checks.csv (add --dsn or set FIN_PG_DSN for the SQL checks)
python src/run_data_checks.py

# 6. Tests (the SQL view tests run only when FIN_PG_DSN is set)
python -m pytest
```

SQL files in `sql/`: `00_create_schema.sql` (tables), `02_pl_views.sql` (P&L views), `03_balance_sheet_views.sql` (balance sheet views), `04_budget_vs_actual_views.sql` (budget vs actual and variance drivers). They are plain SQL and also run in pgAdmin or DBeaver.

Sample design: 36-month loans issued 2013-2015 with a final outcome, 60,000 per year. Loans issued
in 2016 are excluded because many are still open at the end of the file, which would bias their
default rate (see `docs/lending_club_profile.json`). Train = 2013-2014, test = 2015.

## Documentation

- [Scorecard data preparation](docs/scorecard_data_prep.md): split, missing-value policy, dropped columns (generated).
- [Data dictionary](docs/data_dictionary.md): every table and column, real vs synthetic, leakage handling.
- [Metric dictionary](docs/metric_dictionary.md): every metric, its formula and reference values.
- [DAX measures](docs/dax_measures.md): Power Pivot relationships and measures.

## Data sources

- Lending Club loan data: [All Lending Club loan data on Kaggle](https://www.kaggle.com/datasets/wordsforthewise/lending-club). Download it into `data/raw/`; it is not stored in this repository.
- Synthetic ledgers: generated by `src/generate_ledgers.py`; the CSVs are committed in `data/synthetic/`.

## Progress

- [x] Repository and folder structure
- [x] Data preparation (Lending Club sample, leakage audit, synthetic ledgers)
- [x] Data dictionary and metric dictionary (first version)
- [x] Load data into SQL (schemas `fin` and `risk`)
- [x] P&L views in SQL
- [x] Balance sheet views in SQL
- [x] Excel workbook for Power Pivot (`excel/DA04_PowerPivot_Model.xlsx`, DAX in `docs/dax_measures.md`)
- [x] Budget vs actual in SQL with variance drivers
- [x] Data checks (ledger, SQL views, Lending Club) with report in `reports/data_checks.md`
- [x] Scorecard data: cleaning and time-based split by issue year
- [ ] Credit risk scorecard (WoE/IV, logistic regression, bands, validation)
- [ ] Qlik Sense dashboards
- [ ] Power Automate board pack
- [ ] Documentation, tests and final presentation
