# DAX measures

Measures for the Power Pivot Data Model (table `fact_gl_journal`). Create them in this order; later measures use earlier ones.
The same list is in the workbook sheet `DAX_Measures`. Metric IDs refer to `docs/metric_dictionary.md`.

## Relationships (many-to-one, single direction)

- `fact_gl_journal[account_id]` -> `dim_account[account_id]`
- `fact_gl_journal[period]` -> `dim_period[period]`
- `fact_gl_journal[cost_center_id]` -> `dim_cost_center[cost_center_id]`
- `fact_gl_journal[vendor_id]` -> `dim_vendor[vendor_id]`
- `fact_budget[period]` -> `dim_period[period]`
- `fact_budget[account_id]` -> `dim_account[account_id]`
- `fact_budget[cost_center_id]` -> `dim_cost_center[cost_center_id]`
- `fact_drivers[period]` -> `dim_period[period]`

## Measures

| Measure | Format | Note |
|---|---|---|
| Debit Total | `#,##0` | Sum of debits in the current filter. |
| Credit Total | `#,##0` | Sum of credits in the current filter. |
| Interest Income | `#,##0` | M01 |
| Fee Income | `#,##0` | M02 |
| Total Revenue | `#,##0` | M03 |
| Interest Expense | `#,##0` | M04 |
| Net Interest Income | `#,##0` | M05 |
| Net Revenue | `#,##0` | M06 |
| Provision for Credit Losses | `#,##0` | M07 |
| Operating Expenses | `#,##0` | M08 |
| Pre-tax Profit | `#,##0` | M09 |
| Income Tax | `#,##0` | M10 |
| Net Income | `#,##0` | M11 |
| Net Profit Margin | `0.0%` | M12 |
| Cost-to-Income Ratio | `0.0%` | M13 |
| Net Debit To Date | `#,##0` | Cumulative debits minus credits up to the last month in the filter. Basis of every balance sheet measure. |
| Cash and Bank | `#,##0` | Account 1000 |
| Gross Loans | `#,##0` | M20 |
| Allowance for Credit Losses | `#,##0` | M21, shown positive |
| Net Loans | `#,##0` | M22 |
| Fixed Assets | `#,##0` | Account 1200 |
| Other Assets | `#,##0` | Account 1300 |
| Total Assets | `#,##0` | M24 |
| Borrowings | `#,##0` | Account 2000 |
| Accrued Expenses | `#,##0` | Account 2100 |
| Taxes Payable | `#,##0` | Account 2200 |
| Total Liabilities | `#,##0` | M25 |
| Share Capital | `#,##0` | Account 3000 |
| Opening Retained Earnings | `#,##0` | Account 3100 |
| Cumulative Net Income | `#,##0` | Net income from the start to the last month in the filter (the ledger has no closing entries). |
| Total Equity | `#,##0` | M26 |
| Balance Check | `#,##0.00` | Must be 0 in every period. |
| Allowance Coverage | `0.0%` | M23 |
| Debt to Equity | `0.00"x"` | M27 |
| Return on Equity | `0.0%` | M28: annualised net income over the average of the month-end equity values in the period. |

```dax
Debit Total :=
    SUM(fact_gl_journal[debit])

Credit Total :=
    SUM(fact_gl_journal[credit])

Interest Income :=
    CALCULATE([Credit Total] - [Debit Total], dim_account[report_group] = "Interest Income")

Fee Income :=
    CALCULATE([Credit Total] - [Debit Total], dim_account[report_group] = "Fee Income")

Total Revenue :=
    [Interest Income] + [Fee Income]

Interest Expense :=
    CALCULATE([Debit Total] - [Credit Total], dim_account[report_group] = "Interest Expense")

Net Interest Income :=
    [Interest Income] - [Interest Expense]

Net Revenue :=
    [Net Interest Income] + [Fee Income]

Provision for Credit Losses :=
    CALCULATE([Debit Total] - [Credit Total], dim_account[report_group] = "Credit Costs")

Operating Expenses :=
    CALCULATE([Debit Total] - [Credit Total], dim_account[report_group] = "Operating Expenses")

Pre-tax Profit :=
    [Net Revenue] - [Provision for Credit Losses] - [Operating Expenses]

Income Tax :=
    CALCULATE([Debit Total] - [Credit Total], dim_account[report_group] = "Tax")

Net Income :=
    [Pre-tax Profit] - [Income Tax]

Net Profit Margin :=
    DIVIDE([Net Income], [Total Revenue])

Cost-to-Income Ratio :=
    DIVIDE([Operating Expenses], [Net Revenue])

Net Debit To Date :=
    CALCULATE([Debit Total] - [Credit Total], FILTER(ALL(dim_period), dim_period[month_end] <= MAX(dim_period[month_end])))

Cash and Bank :=
    CALCULATE([Net Debit To Date], dim_account[account_id] = 1000)

Gross Loans :=
    CALCULATE([Net Debit To Date], dim_account[account_id] = 1100)

Allowance for Credit Losses :=
    CALCULATE([Net Debit To Date], dim_account[account_id] = 1110) * -1

Net Loans :=
    [Gross Loans] - [Allowance for Credit Losses]

Fixed Assets :=
    CALCULATE([Net Debit To Date], dim_account[account_id] = 1200)

Other Assets :=
    CALCULATE([Net Debit To Date], dim_account[account_id] = 1300)

Total Assets :=
    CALCULATE([Net Debit To Date], dim_account[account_type] = "Asset")

Borrowings :=
    CALCULATE([Net Debit To Date], dim_account[account_id] = 2000) * -1

Accrued Expenses :=
    CALCULATE([Net Debit To Date], dim_account[account_id] = 2100) * -1

Taxes Payable :=
    CALCULATE([Net Debit To Date], dim_account[account_id] = 2200) * -1

Total Liabilities :=
    CALCULATE([Net Debit To Date], dim_account[account_type] = "Liability") * -1

Share Capital :=
    CALCULATE([Net Debit To Date], dim_account[account_id] = 3000) * -1

Opening Retained Earnings :=
    CALCULATE([Net Debit To Date], dim_account[account_id] = 3100) * -1

Cumulative Net Income :=
    CALCULATE([Net Income], FILTER(ALL(dim_period), dim_period[month_end] <= MAX(dim_period[month_end])))

Total Equity :=
    [Share Capital] + [Opening Retained Earnings] + [Cumulative Net Income]

Balance Check :=
    [Total Assets] - [Total Liabilities] - [Total Equity]

Allowance Coverage :=
    DIVIDE([Allowance for Credit Losses], [Gross Loans])

Debt to Equity :=
    DIVIDE([Borrowings], [Total Equity])

Return on Equity :=
    DIVIDE([Net Income] * 12 / DISTINCTCOUNT(fact_gl_journal[period]), AVERAGEX(VALUES(dim_period[month_end]), [Total Equity]))

```
