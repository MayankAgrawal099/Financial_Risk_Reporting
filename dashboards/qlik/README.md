# Qlik Sense: finance dashboard sheet (build guide)

Sheet name: **Finance - P&L and Budget vs Actual**. Data: the synthetic ledger of Kestrel Credit Ltd, 2023 to 2025, in USD.
Favourable variance is positive everywhere (metric M30), so green always means better than budget.
The Lending Club data is a different, unrelated module and is not used on this sheet.

## 1. Get the data in

1. Make the CSVs (already committed in `dashboards/qlik/data/`; re-make them after any data change):
   `python src\export_for_qlik.py --dsn postgresql://postgres:YOUR_PASSWORD@localhost:5432/fin_risk`
2. In Qlik Sense create a new app named `DA04 Finance`.
3. Upload `bva_lines.csv`, `bva_accounts.csv` and `variance_drivers.csv`
   (Qlik Cloud: Add data > Data files; Desktop: put them in a folder and create a Folder data connection `DA04_Data`).
4. Data load editor: paste `finance_load_script.qvs`, set `vDataPath` to your connection, click Load data.
5. Data model viewer: you should see `Calendar` in the middle linked to `BvA_Lines`, `BvA_Accounts` and `Drivers` by `Period` only.
   No synthetic key tables (`$Syn`) should appear. If one does, send me a screenshot.

## 2. Master items

- Dimensions: `Year`, `Quarter`, `Month` (sort by Month, numerically), `P&L Line` (sort by `Line Order`), `Cost Center`, `Driver Account`.
- Measures: all rows of `finance_measures.md`. Paste the expression exactly, set Number formatting to Custom and use the format pattern.

## 3. Layout (top to bottom)

| Row | Object | Dimension | Measures | Notes |
|---|---|---|---|---|
| Filters | Filter panes | Year, Quarter, Month | none | Save a bookmark "FY2025" with Year = 2025 |
| 1 | 5 KPI objects | none | Total Revenue; Net Income; Net Income Variance %; Net Profit Margin; Cost-to-Income Ratio | Colour the variance KPI green when 0 or more, red below 0 |
| 2 left | Pivot or straight table: the P&L | P&L Line | Actual, Budget, Variance, Variance % | Sort by Line Order. Totals off. Colour Variance and Variance % text: green if >= 0, red if < 0 |
| 2 right | Combo chart: net income by month | Month | bars: Net Income; line: Net Income Budget | Month axis sorted ascending |
| 3 left | Waterfall chart: budget to actual net income | measure items | see `finance_measures.md`, section Waterfall | Last item shown as subtotal |
| 3 right | Bar chart: operating expense variance by cost centre | Cost Center | Opex Variance by Cost Centre | Sort by value; same green/red colouring |
| 4 | Stacked bar: what drove the variance | Driver Account | Volume Effect, Rate Effect, Other Effect | Add a text note: volume effect = volume vs budget at budget rate; rate effect = the rest |

Colour expression for conditional colouring (Colors and legend > By expression): `If(Sum(Variance) >= 0, RGB(45,164,78), RGB(209,36,47))`.
For the bar chart use `[Account Variance]`, and for the drivers chart colour by measure.

Footer text: "Source: synthetic ledger, Kestrel Credit Ltd, 2023-2025. Variance: favourable is positive. Definitions in docs/metric_dictionary.md."

## 4. Checks before you screenshot

With Year = 2025 selected (no other selections), the sheet should show:

| Item | Value |
|---|---|
| Net Income (actual) | 1,962,349 |
| Net Income Budget | 3,580,855 |
| Net Income Variance | -1,618,506 (-45.2%) |
| Provision for Credit Losses variance | -1,636,914 |
| Waterfall steps (variance) | Interest Income -324,082; Fee Income +21,588; Interest Expense +23,333; Provision -1,636,914; Operating Expenses -241,935; Income Tax +539,502 |

Also check: select Year = 2023 and Net Income shows 1,487,978 against budget 1,472,198. If any number is different, the load or a measure is wrong.

## 5. If the waterfall will not take measure items

Use a bar chart with the dimension `P&L Line`, measure `Variance`, and a filter `[P&L Line]` on the six lines. The bridge is the same, without the start and end bars.
