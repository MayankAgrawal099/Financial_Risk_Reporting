"""Build the Excel workbook for the Power Pivot part of the project.

Output: excel/DA04_PowerPivot_Model.xlsx   and   docs/dax_measures.md

The workbook contains
  * the 7 synthetic ledger tables as Excel Tables (ready for "Add to Data Model")
  * PL_Monthly and BS_Monthly: P&L and balance sheet built with SUMIFS formulas on the ledger table
  * Summary: three-year P&L, ratios and year-end balance sheet
  * Reference_Check: the reference values from docs/metric_dictionary.md compared with the Summary
  * DAX_Measures and Data Model Guide: what to type in Power Pivot, and the steps

A Power Pivot data model itself can only be created inside Excel (it is stored in a binary part that
other tools cannot write). Follow the "Data Model Guide" sheet to add the tables to the Data Model,
create the relationships and paste the measures.

Run:  python src/build_excel_workbook.py
"""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter as L
from openpyxl.worksheet.table import Table, TableStyleInfo

from finance_metrics import annual_summary
from generate_ledgers import generate

ROOT = Path(__file__).resolve().parents[1]
FONT = "Arial"
F = lambda **k: Font(name=FONT, size=k.pop("size", 10), **k)  # noqa: E731
HDR_FILL = PatternFill("solid", fgColor="1F3864")
SUB_FILL = PatternFill("solid", fgColor="D9E1F2")
BLUE, GREEN = "0000FF", "007A33"
USD = '#,##0;(#,##0);"-"'
PCT = "0.0%"
TOP = Border(top=Side(style="thin", color="808080"))

TABLES = ["dim_account", "dim_cost_center", "dim_vendor", "dim_period",
          "fact_gl_journal", "fact_budget", "fact_drivers"]
GL = "fact_gl_journal"
DB, CR, DATE, ACCT = f"{GL}!$F:$F", f"{GL}!$G:$G", f"{GL}!$C:$C", f"{GL}!$E:$E"

# (measure, DAX expression, number format, meaning)
DAX = [
    ("Debit Total", "SUM(fact_gl_journal[debit])", "#,##0", "Sum of debits in the current filter."),
    ("Credit Total", "SUM(fact_gl_journal[credit])", "#,##0", "Sum of credits in the current filter."),
    ("Interest Income", 'CALCULATE([Credit Total] - [Debit Total], dim_account[report_group] = "Interest Income")', "#,##0", "M01"),
    ("Fee Income", 'CALCULATE([Credit Total] - [Debit Total], dim_account[report_group] = "Fee Income")', "#,##0", "M02"),
    ("Total Revenue", "[Interest Income] + [Fee Income]", "#,##0", "M03"),
    ("Interest Expense", 'CALCULATE([Debit Total] - [Credit Total], dim_account[report_group] = "Interest Expense")', "#,##0", "M04"),
    ("Net Interest Income", "[Interest Income] - [Interest Expense]", "#,##0", "M05"),
    ("Net Revenue", "[Net Interest Income] + [Fee Income]", "#,##0", "M06"),
    ("Provision for Credit Losses", 'CALCULATE([Debit Total] - [Credit Total], dim_account[report_group] = "Credit Costs")', "#,##0", "M07"),
    ("Operating Expenses", 'CALCULATE([Debit Total] - [Credit Total], dim_account[report_group] = "Operating Expenses")', "#,##0", "M08"),
    ("Pre-tax Profit", "[Net Revenue] - [Provision for Credit Losses] - [Operating Expenses]", "#,##0", "M09"),
    ("Income Tax", 'CALCULATE([Debit Total] - [Credit Total], dim_account[report_group] = "Tax")', "#,##0", "M10"),
    ("Net Income", "[Pre-tax Profit] - [Income Tax]", "#,##0", "M11"),
    ("Net Profit Margin", "DIVIDE([Net Income], [Total Revenue])", "0.0%", "M12"),
    ("Cost-to-Income Ratio", "DIVIDE([Operating Expenses], [Net Revenue])", "0.0%", "M13"),
    ("Net Debit To Date", "CALCULATE([Debit Total] - [Credit Total], FILTER(ALL(dim_period), dim_period[month_end] <= MAX(dim_period[month_end])))", "#,##0",
     "Cumulative debits minus credits up to the last month in the filter. Basis of every balance sheet measure."),
    ("Cash and Bank", "CALCULATE([Net Debit To Date], dim_account[account_id] = 1000)", "#,##0", "Account 1000"),
    ("Gross Loans", "CALCULATE([Net Debit To Date], dim_account[account_id] = 1100)", "#,##0", "M20"),
    ("Allowance for Credit Losses", "CALCULATE([Net Debit To Date], dim_account[account_id] = 1110) * -1", "#,##0", "M21, shown positive"),
    ("Net Loans", "[Gross Loans] - [Allowance for Credit Losses]", "#,##0", "M22"),
    ("Fixed Assets", "CALCULATE([Net Debit To Date], dim_account[account_id] = 1200)", "#,##0", "Account 1200"),
    ("Other Assets", "CALCULATE([Net Debit To Date], dim_account[account_id] = 1300)", "#,##0", "Account 1300"),
    ("Total Assets", 'CALCULATE([Net Debit To Date], dim_account[account_type] = "Asset")', "#,##0", "M24"),
    ("Borrowings", "CALCULATE([Net Debit To Date], dim_account[account_id] = 2000) * -1", "#,##0", "Account 2000"),
    ("Accrued Expenses", "CALCULATE([Net Debit To Date], dim_account[account_id] = 2100) * -1", "#,##0", "Account 2100"),
    ("Taxes Payable", "CALCULATE([Net Debit To Date], dim_account[account_id] = 2200) * -1", "#,##0", "Account 2200"),
    ("Total Liabilities", 'CALCULATE([Net Debit To Date], dim_account[account_type] = "Liability") * -1', "#,##0", "M25"),
    ("Share Capital", "CALCULATE([Net Debit To Date], dim_account[account_id] = 3000) * -1", "#,##0", "Account 3000"),
    ("Opening Retained Earnings", "CALCULATE([Net Debit To Date], dim_account[account_id] = 3100) * -1", "#,##0", "Account 3100"),
    ("Cumulative Net Income", "CALCULATE([Net Income], FILTER(ALL(dim_period), dim_period[month_end] <= MAX(dim_period[month_end])))", "#,##0",
     "Net income from the start to the last month in the filter (the ledger has no closing entries)."),
    ("Total Equity", "[Share Capital] + [Opening Retained Earnings] + [Cumulative Net Income]", "#,##0", "M26"),
    ("Balance Check", "[Total Assets] - [Total Liabilities] - [Total Equity]", "#,##0.00", "Must be 0 in every period."),
    ("Allowance Coverage", "DIVIDE([Allowance for Credit Losses], [Gross Loans])", "0.0%", "M23"),
    ("Debt to Equity", "DIVIDE([Borrowings], [Total Equity])", '0.00"x"', "M27"),
    ("Return on Equity", "DIVIDE([Net Income] * 12 / DISTINCTCOUNT(fact_gl_journal[period]), AVERAGEX(VALUES(dim_period[month_end]), [Total Equity]))", "0.0%",
     "M28: annualised net income over the average of the month-end equity values in the period."),
]

RELATIONSHIPS = [
    ("fact_gl_journal[account_id]", "dim_account[account_id]"),
    ("fact_gl_journal[period]", "dim_period[period]"),
    ("fact_gl_journal[cost_center_id]", "dim_cost_center[cost_center_id]"),
    ("fact_gl_journal[vendor_id]", "dim_vendor[vendor_id]"),
    ("fact_budget[period]", "dim_period[period]"),
    ("fact_budget[account_id]", "dim_account[account_id]"),
    ("fact_budget[cost_center_id]", "dim_cost_center[cost_center_id]"),
    ("fact_drivers[period]", "dim_period[period]"),
]


def style_header(ws, row, c1, c2):
    for c in range(c1, c2 + 1):
        cell = ws.cell(row, c)
        cell.font, cell.fill = F(bold=True, color="FFFFFF"), HDR_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def write_data_sheets(wb: Workbook, tables: dict[str, pd.DataFrame]) -> None:
    for name in TABLES:
        df = tables[name].copy()
        ws = wb.create_sheet(name)
        for col in ("posting_date", "month_start", "month_end"):
            if col in df:
                df[col] = pd.to_datetime(df[col]).dt.date
        ws.append(list(df.columns))
        for row in df.itertuples(index=False):
            ws.append([None if (isinstance(v, float) and pd.isna(v)) or v is pd.NA else v for v in row])
        ref = f"A1:{L(len(df.columns))}{len(df) + 1}"
        t = Table(displayName=name, ref=ref)
        t.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
        ws.add_table(t)
        for i, col in enumerate(df.columns, 1):
            ws.column_dimensions[L(i)].width = max(12, min(40, len(col) + 4))
            for r in range(1, len(df) + 2):
                cell = ws.cell(r, i)
                cell.font = F(bold=(r == 1), color=("FFFFFF" if r == 1 else "000000"))
                if r > 1 and col in ("posting_date", "month_start", "month_end"):
                    cell.number_format = "yyyy-mm-dd"
        ws.freeze_panes = "A2"


def month_header(ws, first_date: dt.date, n: int, col0: int, row_date=4, row_fy=3):
    ws.cell(row_date, col0, first_date).number_format = "mmm-yy"
    ws.cell(row_date, col0).font = F(bold=True, color=BLUE)
    for i in range(1, n):
        c = ws.cell(row_date, col0 + i, f"=EOMONTH({L(col0 + i - 1)}{row_date},1)")
        c.number_format = "mmm-yy"
    for i in range(n):
        ws.cell(row_fy, col0 + i, f"=YEAR({L(col0 + i)}{row_date})").number_format = "0"
        ws.cell(row_fy, col0 + i).font = F(color="808080", size=8)
    ws.cell(row_fy, 1, "Fiscal year").font = F(color="808080", size=8)


def build_pl(wb: Workbook) -> None:
    ws = wb.create_sheet("PL_Monthly")
    ws["A1"] = "Profit and loss by month (USD): built with SUMIFS on the ledger table"
    ws["A1"].font = F(bold=True, size=13, color="1F3864")
    ws["A2"] = "Revenue = credits - debits; expenses = debits - credits. Account range columns are inputs (blue)."
    ws["A2"].font = F(italic=True, color="595959")
    ws["A4"], ws["B4"], ws["C4"], ws["D4"] = "USD", "Account from", "Account to", "Type"
    n_m, c0 = 36, 5
    month_header(ws, dt.date(2023, 1, 31), n_m, c0)
    fy0 = c0 + n_m                                    # first FY column
    for i, y in enumerate((2023, 2024, 2025)):
        ws.cell(3, fy0 + i, y).font = F(color=BLUE, size=8)
        ws.cell(4, fy0 + i, f'="FY"&{L(fy0 + i)}3')
    style_header(ws, 4, 1, fy0 + 2)
    for c in range(c0, c0 + n_m):
        ws.cell(4, c).font = F(bold=True, color="FFFFFF")

    # (row, label, from, to, type)  type None => formula row
    detail = {5: ("Interest Income", 4000, 4000, "Revenue"), 6: ("Fee Income", 4100, 4100, "Revenue"),
              8: ("Interest Expense", 5000, 5000, "Expense"), 11: ("Provision for Credit Losses", 5100, 5100, "Expense"),
              12: ("Salaries and Benefits", 6000, 6000, "Expense"), 13: ("Marketing", 6100, 6100, "Expense"),
              14: ("Technology and Software", 6200, 6200, "Expense"), 15: ("Rent and Administration", 6300, 6300, "Expense"),
              16: ("Collections and Recovery Costs", 6400, 6400, "Expense"), 17: ("Depreciation", 6500, 6500, "Expense"),
              20: ("Income Tax", 7000, 7000, "Expense")}
    formulas = {7: ("Total Revenue", "{c}5+{c}6"), 9: ("Net Interest Income", "{c}5-{c}8"),
                10: ("Net Revenue", "{c}9+{c}6"), 18: ("Operating Expenses", "SUM({c}12:{c}17)"),
                19: ("Pre-tax Profit", "{c}10-{c}11-{c}18"), 21: ("Net Income", "{c}19-{c}20"),
                23: ("Net Profit Margin", 'IF({c}7=0,"",{c}21/{c}7)'),
                24: ("Cost-to-Income Ratio", 'IF({c}10=0,"",{c}18/{c}10)')}
    last_m, last_c = L(c0 + n_m - 1), fy0 + 2
    for r, (label, a, b, typ) in detail.items():
        ws.cell(r, 1, label).font = F()
        for col, v in zip((2, 3), (a, b)):
            ws.cell(r, col, v).font = F(color=BLUE)
        ws.cell(r, 4, typ).font = F(color=BLUE)
        for c in range(c0, c0 + n_m):
            cl = L(c)
            def pair(rng_sum):
                return (f"SUMIFS({rng_sum},{DATE},{cl}$4,{ACCT},\">=\"&$B{r},{ACCT},\"<=\"&$C{r})")
            ws.cell(r, c, f'=IF($D{r}="Revenue",{pair(CR)}-{pair(DB)},{pair(DB)}-{pair(CR)})')
        for k in range(3):
            cl = L(fy0 + k)
            ws.cell(r, fy0 + k, f"=SUMIFS($E{r}:${last_m}{r},$E$3:${last_m}$3,{cl}$3)")
    for r, (label, f) in formulas.items():
        ws.cell(r, 1, label).font = F(bold=r in (7, 9, 10, 18, 19, 21))
        for c in range(c0, last_c + 1):
            ws.cell(r, c, "=" + f.format(c=L(c)))
            if r in (7, 9, 10, 18, 19, 21):
                ws.cell(r, c).border = TOP
        if r in (7, 9, 10, 18, 19, 21):
            ws.cell(r, 1).border = TOP
    for r in range(5, 25):
        for c in range(c0, last_c + 1):
            cell = ws.cell(r, c)
            cell.font = F(bold=r in (7, 9, 10, 18, 19, 21))
            cell.number_format = PCT if r in (23, 24) else USD
    ws.column_dimensions["A"].width = 32
    for c, w in ((2, 11), (3, 11), (4, 10)):
        ws.column_dimensions[L(c)].width = w
    for c in range(c0, last_c + 1):
        ws.column_dimensions[L(c)].width = 12
    ws.freeze_panes = "E5"


def build_bs(wb: Workbook) -> None:
    ws = wb.create_sheet("BS_Monthly")
    ws["A1"] = "Balance sheet at each month end (USD): cumulative balances from the ledger table"
    ws["A1"].font = F(bold=True, size=13, color="1F3864")
    ws["A2"] = ("Assets = debits - credits; liabilities and equity = credits - debits. The allowance is negative "
                "(contra-asset). Equity includes net income since the opening position (no closing entries).")
    ws["A2"].font = F(italic=True, color="595959")
    ws["A4"], ws["B4"], ws["C4"] = "USD", "Account", "Side"
    n_m, c0 = 37, 5
    month_header(ws, dt.date(2022, 12, 31), n_m, c0)
    style_header(ws, 4, 1, c0 + n_m - 1)
    last_c = c0 + n_m - 1
    detail = {5: ("Cash and Bank", 1000, "D"), 6: ("Loans Receivable (Gross)", 1100, "D"),
              7: ("Allowance for Credit Losses", 1110, "D"), 9: ("Fixed Assets (Net)", 1200, "D"),
              10: ("Prepaid and Other Assets", 1300, "D"), 12: ("Borrowings", 2000, "C"),
              13: ("Accrued Expenses", 2100, "C"), 14: ("Taxes Payable", 2200, "C"),
              16: ("Share Capital", 3000, "C"), 17: ("Retained Earnings (Opening)", 3100, "C")}
    bold = (8, 11, 15, 19, 20)
    formulas = {8: ("Net Loans", "{c}6+{c}7"), 11: ("Total Assets", "{c}5+{c}8+{c}9+{c}10"),
                15: ("Total Liabilities", "SUM({c}12:{c}14)"),
                18: ("Net Income Since Opening",
                     'SUMIFS(PL_Monthly!$E$21:$AN$21,PL_Monthly!$E$4:$AN$4,"<="&{c}$4)'),
                19: ("Total Equity", "SUM({c}16:{c}18)"),
                20: ("Total Liabilities and Equity", "{c}15+{c}19"),
                21: ("Balance Check (must be 0)", "ROUND({c}11-{c}20,2)"),
                23: ("Allowance Coverage", 'IF({c}6=0,"",-{c}7/{c}6)'),
                24: ("Debt-to-Equity", 'IF({c}19=0,"",{c}12/{c}19)')}
    for r, (label, acct, side) in detail.items():
        ws.cell(r, 1, label).font = F()
        ws.cell(r, 2, acct).font = F(color=BLUE)
        ws.cell(r, 3, side).font = F(color=BLUE)
        for c in range(c0, last_c + 1):
            cl = L(c)
            dr = f'SUMIFS({DB},{ACCT},$B{r},{DATE},"<="&{cl}$4)'
            cr = f'SUMIFS({CR},{ACCT},$B{r},{DATE},"<="&{cl}$4)'
            ws.cell(r, c, f'=IF($C{r}="D",{dr}-{cr},{cr}-{dr})')
    for r, (label, f) in formulas.items():
        ws.cell(r, 1, label)
        for c in range(c0, last_c + 1):
            ws.cell(r, c, "=" + f.format(c=L(c)))
    for r in range(5, 25):
        ws.cell(r, 1).font = F(bold=r in bold)
        for c in range(c0, last_c + 1):
            cell = ws.cell(r, c)
            cell.font = F(bold=r in bold)
            cell.number_format = PCT if r == 23 else ('0.00"x"' if r == 24 else ('#,##0.00;(#,##0.00);"-"' if r == 21 else USD))
            if r in bold:
                cell.border = TOP
    ws.column_dimensions["A"].width = 32
    for c in range(2, 5):
        ws.column_dimensions[L(c)].width = 9
    for c in range(c0, last_c + 1):
        ws.column_dimensions[L(c)].width = 13
    ws.freeze_panes = "E5"
    red = PatternFill("solid", bgColor="FFC7CE")
    ws.conditional_formatting.add(f"E21:{L(last_c)}21", CellIsRule(operator="notEqual", formula=["0"], fill=red))


def build_summary(wb: Workbook) -> dict[str, int]:
    ws = wb.create_sheet("Summary")
    ws["A1"] = "Kestrel Credit Ltd (synthetic): three-year summary"
    ws["A1"].font = F(bold=True, size=14, color="1F3864")
    ws["A2"] = "USD. Every figure is pulled by formula from PL_Monthly, BS_Monthly and fact_drivers. FY = calendar year."
    ws["A2"].font = F(italic=True, color="595959")
    ws["A3"] = "USD"
    for i, y in enumerate((2023, 2024, 2025)):
        c = ws.cell(3, 2 + i, y)
        c.number_format = '"FY"0'
    style_header(ws, 3, 1, 4)
    rows = {}
    r = 4

    def section(title):
        nonlocal r
        ws.cell(r, 1, title).font = F(bold=True)
        for c in range(1, 5):
            ws.cell(r, c).fill = SUB_FILL
        r += 1

    def pl(label, src_row, key, fmt=USD, bold=False):
        nonlocal r
        ws.cell(r, 1, label).font = F(bold=bold)
        for i in range(3):
            col = L(2 + i)
            ws.cell(r, 2 + i, f"=INDEX(PL_Monthly!$AO{src_row}:$AQ{src_row},MATCH({col}$3,PL_Monthly!$AO$3:$AQ$3,0))")
            ws.cell(r, 2 + i).font = F(color=GREEN, bold=bold)
            ws.cell(r, 2 + i).number_format = fmt
        rows[key] = r
        r += 1

    def bs(label, src_row, key, fmt=USD, bold=False):
        nonlocal r
        ws.cell(r, 1, label).font = F(bold=bold)
        for i in range(3):
            col = L(2 + i)
            ws.cell(r, 2 + i, f"=INDEX(BS_Monthly!$E{src_row}:$AO{src_row},MATCH(DATE({col}$3,12,31),BS_Monthly!$E$4:$AO$4,0))")
            ws.cell(r, 2 + i).font = F(color=GREEN, bold=bold)
            ws.cell(r, 2 + i).number_format = fmt
        rows[key] = r
        r += 1

    def calc(label, formula, key, fmt):
        nonlocal r
        ws.cell(r, 1, label).font = F()
        for i in range(3):
            col = L(2 + i)
            ws.cell(r, 2 + i, "=" + formula.format(c=col)).number_format = fmt
            ws.cell(r, 2 + i).font = F()
        rows[key] = r
        r += 1

    section("Profit and loss")
    pl("Interest Income", 5, "interest_income"); pl("Fee Income", 6, "fee_income")
    pl("Total Revenue", 7, "total_revenue", bold=True); pl("Interest Expense", 8, "interest_expense")
    pl("Net Interest Income", 9, "nii", bold=True); pl("Net Revenue", 10, "net_revenue", bold=True)
    pl("Provision for Credit Losses", 11, "provision"); pl("Operating Expenses", 18, "opex")
    pl("Pre-tax Profit", 19, "pbt", bold=True); pl("Income Tax", 20, "tax")
    pl("Net Income", 21, "net_income", bold=True)
    section("Balance sheet at year end")
    bs("Cash and Bank", 5, "cash"); bs("Loans Receivable (Gross)", 6, "gross_loans")
    bs("Allowance for Credit Losses (negative)", 7, "allowance"); bs("Net Loans", 8, "net_loans", bold=True)
    bs("Fixed Assets (Net)", 9, "fixed"); bs("Prepaid and Other Assets", 10, "other")
    bs("Total Assets", 11, "total_assets", bold=True); bs("Borrowings", 12, "borrowings")
    bs("Accrued Expenses", 13, "accrued"); bs("Taxes Payable", 14, "taxes")
    bs("Total Liabilities", 15, "total_liabilities", bold=True); bs("Total Equity", 19, "equity", bold=True)
    bs("Balance Check (must be 0)", 21, "check", fmt='#,##0.00;(#,##0.00);"-"')
    section("Ratios")
    avg_book = 'AVERAGEIFS(fact_drivers!$H:$H,fact_drivers!$B:$B,"Actual",fact_drivers!$A:$A,{c}$3&"-*")'
    avg_debt = 'AVERAGEIFS(fact_drivers!$M:$M,fact_drivers!$B:$B,"Actual",fact_drivers!$A:$A,{c}$3&"-*")'
    r_ = rows
    calc("Net Profit Margin", f"{{c}}{r_['net_income']}/{{c}}{r_['total_revenue']}", "npm", PCT)
    calc("Cost-to-Income Ratio", f"{{c}}{r_['opex']}/{{c}}{r_['net_revenue']}", "cti", PCT)
    calc("Yield on Loans", f"{{c}}{r_['interest_income']}/{avg_book}", "yield", PCT)
    calc("Cost of Funds", f"{{c}}{r_['interest_expense']}/{avg_debt}", "cof", PCT)
    calc("Net Interest Margin", f"{{c}}{r_['nii']}/{avg_book}", "nim", PCT)
    calc("Allowance Coverage", f"-{{c}}{r_['allowance']}/{{c}}{r_['gross_loans']}", "coverage", PCT)
    calc("Debt-to-Equity", f"{{c}}{r_['borrowings']}/{{c}}{r_['equity']}", "de", '0.00"x"')
    calc("Return on Equity", f"{{c}}{r_['net_income']}/AVERAGEIFS(BS_Monthly!$E$19:$AO$19,BS_Monthly!$E$3:$AO$3,{{c}}$3)",
         "roe", PCT)
    ws.column_dimensions["A"].width = 38
    for c in "BCD":
        ws.column_dimensions[c].width = 16
    ws.freeze_panes = "B4"
    return rows


def build_reference_check(wb: Workbook, rows: dict[str, int], tables) -> None:
    ws = wb.create_sheet("Reference_Check")
    ws["A1"] = "Reference check: workbook vs docs/metric_dictionary.md (section 6)"
    ws["A1"].font = F(bold=True, size=13, color="1F3864")
    ws["A2"] = ("Reference values are typed inputs (blue), rounded as in the dictionary. Workbook values are links (green). "
                "Use the same table to check your Power Pivot tables: they must give the same numbers.")
    ws["A2"].font = F(italic=True, color="595959")
    heads = ["Metric", "Tolerance", "Ref FY2023", "Ref FY2024", "Ref FY2025",
             "Book FY2023", "Book FY2024", "Book FY2025", "Status"]
    for c, h in enumerate(heads, 1):
        ws.cell(4, c, h)
    style_header(ws, 4, 1, 9)
    fy = annual_summary(tables)
    spec = [("Total Revenue (M03)", "total_revenue", "total_revenue", 1, USD, 0),
            ("Interest Expense (M04)", "interest_expense", "interest_expense", 1, USD, 0),
            ("Net Interest Income (M05)", "nii", "nii", 1, USD, 0),
            ("Net Revenue (M06)", "net_revenue", "net_revenue", 1, USD, 0),
            ("Provision (M07)", "provision", "provision", 1, USD, 0),
            ("Operating Expenses (M08)", "opex", "opex", 1, USD, 0),
            ("Pre-tax Profit (M09)", "pbt", "pbt", 1, USD, 0),
            ("Income Tax (M10)", "tax", "tax", 1, USD, 0),
            ("Net Income (M11)", "net_income", "net_income", 1, USD, 0),
            ("Net Profit Margin (M12)", "npm", "net_profit_margin", 0.0006, PCT, 3),
            ("Cost-to-Income (M13)", "cti", "cost_to_income", 0.0006, PCT, 3),
            ("Yield on Loans (M14)", "yield", "yield_on_loans", 0.0006, PCT, 3),
            ("Net Interest Margin (M16)", "nim", "nim", 0.0006, PCT, 3),
            ("Gross Loans, year end (M20)", "gross_loans", "gross_loans_ye", 1, USD, 0),
            ("Total Equity, year end (M26)", "equity", "equity_ye", 1, USD, 0),
            ("Debt-to-Equity (M27)", "de", "debt_to_equity_ye", 0.006, '0.00"x"', 2),
            ("Return on Equity (M28)", "roe", "roe", 0.0006, PCT, 3)]
    for i, (label, key, ref_col, tol, fmt, dp) in enumerate(spec):
        r = 5 + i
        ws.cell(r, 1, label).font = F()
        ws.cell(r, 2, tol).font = F(color=BLUE)
        for k, year in enumerate(("2023", "2024", "2025")):
            ws.cell(r, 3 + k, round(float(fy.loc[year, ref_col]), dp)).font = F(color=BLUE)
            ws.cell(r, 3 + k).number_format = fmt
            ws.cell(r, 6 + k, f"=Summary!{L(2 + k)}{rows[key]}").font = F(color=GREEN)
            ws.cell(r, 6 + k).number_format = fmt
        ws.cell(r, 9, f'=IF(AND(ABS(F{r}-C{r})<=$B{r},ABS(G{r}-D{r})<=$B{r},ABS(H{r}-E{r})<=$B{r}),"OK","CHECK")')
        ws.cell(r, 9).font = F(bold=True)
        ws.cell(r, 2).number_format = "General"
    end = 4 + len(spec)
    ws.cell(end + 2, 1, "Overall").font = F(bold=True)
    ws.cell(end + 2, 9, f'=IF(COUNTIF(I5:I{end},"CHECK")=0,"ALL OK","REVIEW")').font = F(bold=True)
    ws.cell(end + 3, 1, "Balance sheet balances in all 37 month-ends?").font = F(bold=True)
    ws.cell(end + 3, 9, '=IF(SUMPRODUCT(ABS(BS_Monthly!E21:AO21))=0,"OK","CHECK")').font = F(bold=True)
    ok, bad = PatternFill("solid", bgColor="C6EFCE"), PatternFill("solid", bgColor="FFC7CE")
    rng = f"I5:I{end + 3}"
    ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=['"CHECK"'], fill=bad))
    ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=['"REVIEW"'], fill=bad))
    for v in ('"OK"', '"ALL OK"'):
        ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=[v], fill=ok))
    ws.column_dimensions["A"].width = 44
    for c in range(2, 10):
        ws.column_dimensions[L(c)].width = 15
    ws.freeze_panes = "B5"


def build_dax_sheet(wb: Workbook) -> None:
    ws = wb.create_sheet("DAX_Measures")
    ws["A1"] = "DAX measures for the Data Model"
    ws["A1"].font = F(bold=True, size=13, color="1F3864")
    ws["A2"] = ("Create each measure on table fact_gl_journal. In Excel: right-click fact_gl_journal in the PivotTable Fields list > "
                "Add Measure. Name = column A, Formula = an equals sign followed by column B, Format = column C. "
                "Create them in this order (later measures use earlier ones).")
    ws["A2"].font = F(italic=True, color="595959")
    ws["A2"].alignment = Alignment(wrap_text=True, vertical="top")
    ws.row_dimensions[2].height = 42
    for c, h in enumerate(["Measure name", "DAX expression (type = first)", "Number format", "Metric / note"], 1):
        ws.cell(4, c, h)
    style_header(ws, 4, 1, 4)
    for i, (name, dax, fmt, note) in enumerate(DAX):
        r = 5 + i
        for c, v in enumerate((name, dax, fmt, note), 1):
            cell = ws.cell(r, c, v)
            cell.font = F()
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    for c, w in zip("ABCD", (30, 95, 14, 55)):
        ws.column_dimensions[c].width = w
    ws.freeze_panes = "A5"


GUIDE = [
    ("h", "Build the Power Pivot data model (about 15 minutes)"),
    ("t", "Needs Excel for Windows (Microsoft 365, or 2016 and later Professional Plus). The Data Model itself is created in Excel; this workbook has the data and the answers ready."),
    ("h", "Step 1. Add the 7 tables to the Data Model"),
    ("t", "Each sheet named dim_* or fact_* holds an Excel Table with the same name. For each one: click inside the table > Insert > PivotTable > tick 'Add this data to the Data Model' > OK. (Or: Power Pivot tab > Add to Data Model.) Repeat for all 7 tables. You can close the empty PivotTables or delete those sheets."),
    ("h", "Step 2. Create the relationships"),
    ("t", "Data tab > Relationships > New (or Power Pivot > Manage > Diagram View, then drag fields). Many side first, one side second. All are many-to-one, single direction:"),
    ("rel", ""),
    ("t", "Revenue lines have a blank cost_center_id and vendor_id. That is expected: they appear under (Blank)."),
    ("h", "Step 3. Add the measures"),
    ("t", "Open the DAX_Measures sheet. Add each measure to fact_gl_journal in the order listed (see the instructions at the top of that sheet). Set the number format. If a measure shows an error, send me the error text."),
    ("h", "Step 4. Build the P&L pivot (new sheet named PP_PL)"),
    ("t", "Insert > PivotTable > Use this workbook's Data Model. Rows: drag the measures Interest Income, Fee Income, Total Revenue, Interest Expense, Net Interest Income, Net Revenue, Provision for Credit Losses, Operating Expenses, Pre-tax Profit, Income Tax, Net Income, Net Profit Margin, Cost-to-Income Ratio into Values, then drag the 'Values' item from Columns to Rows. Columns: dim_period[fiscal_year] (expand to quarter and month_name if wanted). Filter dim_period[fiscal_year] to 2023-2025 (the opening month 2022-12 has no P&L)."),
    ("h", "Step 5. Build the balance sheet pivot (new sheet named PP_BS)"),
    ("t", "Same setup. Values: Cash and Bank, Gross Loans, Allowance for Credit Losses, Net Loans, Fixed Assets, Other Assets, Total Assets, Borrowings, Accrued Expenses, Taxes Payable, Total Liabilities, Share Capital, Opening Retained Earnings, Cumulative Net Income, Total Equity, Balance Check, Allowance Coverage, Debt to Equity, Return on Equity. Values on Rows. Columns: dim_period[fiscal_year], then dim_period[period] below it. A year column shows its December balance (the balance sheet is a snapshot)."),
    ("h", "Step 6. Check your pivots"),
    ("t", "Compare your pivots with the Summary and Reference_Check sheets: FY2025 Net Income must be 1,962,349; FY2025 Total Equity 27,653,672; Balance Check must be 0 in every column. These are the same numbers as the SQL views and docs/metric_dictionary.md."),
    ("h", "Step 7. Save and commit"),
    ("t", "Save the workbook (same name) with the pivots and the model, then copy it over excel/DA04_PowerPivot_Model.xlsx in your repo. The measures are also listed in docs/dax_measures.md for reviewers."),
    ("h", "What is already done for you without Power Pivot"),
    ("t", "PL_Monthly, BS_Monthly and Summary are plain-formula versions (SUMIFS) of the same statements. They work on any Excel and prove the numbers. The Power Pivot pivots should reproduce them."),
    ("h", "Note on the DAX"),
    ("t", "The DAX was written to standard patterns but could not be run in the environment that built this file. Treat Reference_Check as the test: if a pivot number differs, the measure needs fixing."),
]


def build_guide(wb: Workbook) -> None:
    ws = wb.create_sheet("Data Model Guide")
    ws.column_dimensions["A"].width = 120
    ws.column_dimensions["B"].width = 4
    r = 1
    ws.cell(r, 1, "Data Model Guide").font = F(bold=True, size=14, color="1F3864")
    r += 2
    for kind, text in GUIDE:
        if kind == "h":
            ws.cell(r, 1, text).font = F(bold=True, size=11)
            ws.cell(r, 1).fill = SUB_FILL
        elif kind == "rel":
            for a, b in RELATIONSHIPS:
                ws.cell(r, 1, f"    {a}   ->   {b}").font = F(name_hack=None) if False else Font(name="Consolas", size=10)
                r += 1
            continue
        else:
            ws.cell(r, 1, text).font = F()
            ws.cell(r, 1).alignment = Alignment(wrap_text=True, vertical="top")
            ws.row_dimensions[r].height = max(15, 14 * (len(text) // 115 + 1))
        r += 1


def build_start(wb: Workbook) -> None:
    ws = wb["Start Here"]
    ws["A1"] = "DA-04 Financial Analytics and Risk Reporting: Excel and Power Pivot workbook"
    ws["A1"].font = F(bold=True, size=14, color="1F3864")
    lines = [
        ("All data is SYNTHETIC: fictional lender Kestrel Credit Ltd, USD, Jan 2023 to Dec 2025 (opening balances at 31 Dec 2022).", False),
        ("", False),
        ("Sheets", True),
        ("Summary: three-year P&L, ratios and year-end balance sheet (formulas).", False),
        ("PL_Monthly / BS_Monthly: the statements by month, built with SUMIFS on the ledger table.", False),
        ("Reference_Check: compares this workbook with the reference values in docs/metric_dictionary.md. All rows should say OK.", False),
        ("DAX_Measures / Data Model Guide: what to build in Power Pivot and how.", False),
        ("dim_* and fact_* sheets: the 7 ledger tables as Excel Tables, ready for the Data Model. Source: data/synthetic/*.csv.", False),
        ("", False),
        ("Colour code: blue = typed input, black = formula, green = link to another sheet.", False),
        ("In this file posting_date, month_start and month_end are real Excel dates; period stays text (YYYY-MM).", False),
    ]
    for i, (t, b) in enumerate(lines, 3):
        ws.cell(i, 1, t).font = F(bold=b)
    ws.column_dimensions["A"].width = 130


def build(out: Path, dax_doc: Path, seed: int = 42) -> None:
    tables = generate(seed)
    wb = Workbook()
    wb.active.title = "Start Here"
    build_start(wb)
    write_data_sheets(wb, tables)
    build_pl(wb)
    build_bs(wb)
    rows = build_summary(wb)
    build_reference_check(wb, rows, tables)
    build_dax_sheet(wb)
    build_guide(wb)
    order = ["Start Here", "Summary", "PL_Monthly", "BS_Monthly", "Reference_Check", "DAX_Measures",
             "Data Model Guide"] + TABLES
    wb._sheets = [wb[n] for n in order]
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)

    md = ["# DAX measures", "",
          "Measures for the Power Pivot Data Model (table `fact_gl_journal`). Create them in this order; later measures use earlier ones.",
          "The same list is in the workbook sheet `DAX_Measures`. Metric IDs refer to `docs/metric_dictionary.md`.", "",
          "## Relationships (many-to-one, single direction)", ""]
    md += [f"- `{a}` -> `{b}`" for a, b in RELATIONSHIPS]
    md += ["", "## Measures", "", "| Measure | Format | Note |", "|---|---|---|"]
    md += [f"| {n} | `{f}` | {note} |" for n, _, f, note in DAX]
    md += ["", "```dax"]
    md += [f"{n} :=\n    {d}\n" for n, d, _, _ in DAX]
    md += ["```", ""]
    dax_doc.write_text("\n".join(md), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=ROOT / "excel" / "DA04_PowerPivot_Model.xlsx")
    ap.add_argument("--dax-doc", type=Path, default=ROOT / "docs" / "dax_measures.md")
    a = ap.parse_args()
    build(a.out, a.dax_doc)
    print(f"Wrote {a.out} and {a.dax_doc}")


if __name__ == "__main__":
    main()
