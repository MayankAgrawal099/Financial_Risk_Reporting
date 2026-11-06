# Qlik Sense dashboards

Two separate Qlik Sense apps. Keep them separate: the finance data (a synthetic ledger, 2023-2025) and the risk data (Lending Club loans, 2013-2015) are different datasets and are not reconciled.

| App | Sheet | Load script | Measures | CSV files (dashboards/qlik/data) | Made by |
|---|---|---|---|---|---|
| DA04 Finance | P&L and budget vs actual | `finance_load_script.qvs` | `finance_measures.md` | `bva_lines.csv`, `bva_accounts.csv`, `variance_drivers.csv` | `src/export_for_qlik.py` (needs the database) |
| DA04 Risk | Scorecard bands and stability | `risk_load_script.qvs` | `risk_measures.md` | `risk_years.csv`, `risk_bands.csv`, `risk_feature_psi.csv`, `risk_score_deciles.csv`, `risk_models.csv` | `src/export_risk_for_qlik.py` (reads the scorecard outputs) |

Screenshots of both sheets are in `docs/Screenshots/` (`qlik_finance_sheet_2025.png`, `qlik_risk_sheet.png`).

## Rebuild the apps

1. Make the CSV files (the finance ones are already committed, so this is only needed after a data change):
   ```
   python src/export_for_qlik.py --dsn postgresql://postgres:PASSWORD@localhost:5432/fin_risk
   python src/export_risk_for_qlik.py
   ```
2. In Qlik Sense create an app, upload the CSV files of that app as Data files, paste the load script into the Data load editor, and click Load data. The first line of each script sets `vDataPath` (`lib://DataFiles/` for Qlik Cloud, or your own folder connection).
3. Create the master measures from the measures file (name, expression, format pattern), then build the sheet.

## Data models

- Finance: `Calendar` (one row per month) links to `BvA_Lines`, `BvA_Accounts` and `Drivers` through `Period`. Fields in the fact tables are renamed so no other field is shared.
- Risk: `Risk_Years` and `Risk_Bands` link through `Issue Year`. `Feature_PSI`, `Score_Deciles` and `Model_Compare` stand alone.
- Neither model has synthetic keys (checked in `tests/test_qlik_kit.py` and `tests/test_qlik_risk_kit.py`).

## Conventions

- Favourable variance is positive (metric M30): green always means better than budget.
- Risk KPIs are fixed to the test year (`{1<Data={'test'}>}`), so they never change when a filter is clicked.
- PSI colours: under 0.10 green, 0.10 to 0.25 amber, over 0.25 red (metric M56).
- Definitions of every metric are in `docs/metric_dictionary.md`.

## Numbers to check after loading

| Sheet | Selection | Value |
|---|---|---|
| Finance | Year = 2025 | Net income 1,962,349 against budget 3,580,855 (-45.2%); provision variance -1,636,914 |
| Finance | Year = 2023 | Net income 1,487,978 against budget 1,472,198 |
| Risk | none | Test Gini 0.350, AUC 0.675, KS 0.256, score PSI 0.003, calibration gap +1.9% |
| Risk | Data = test | Band table equals the `test` rows of `docs/scorecard_bands.csv` |
