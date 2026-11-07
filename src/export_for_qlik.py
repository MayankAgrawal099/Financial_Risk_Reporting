"""Export the finance SQL views to CSV files for Qlik Sense, and write the master-measure list.

Qlik reads these three files (dashboards/qlik/data/):
  bva_lines.csv          fin.v_bva_summary        P&L lines by month: actual, budget, variance      (M30, M31)
  bva_accounts.csv       fin.v_bva_account_month  actual, budget, variance by month, account, cost centre
  variance_drivers.csv   fin.v_variance_drivers   monthly volume, rate and other effects             (M32, M33)
  balance_sheet.csv      fin.v_balance_sheet_monthly  month-end balance sheet and ratios (board report only)  (M20-M27)

The measure definitions below are the single source for dashboards/qlik/finance_measures.md; a test checks that every
field they use exists in dashboards/qlik/finance_load_script.qvs.

Run:  python src/export_for_qlik.py --dsn postgresql://postgres:PASSWORD@localhost:5432/fin_risk
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
QLIK_DIR = ROOT / "dashboards" / "qlik"

EXPORTS: dict[str, tuple[str, list[str]]] = {
    "bva_lines.csv": ("fin.v_bva_summary",
                      ["period", "fiscal_year", "quarter", "month_end", "line_order", "line_name", "is_cost", "actual", "budget",
                       "variance", "variance_pct"]),
    "bva_accounts.csv": ("fin.v_bva_account_month",
                         ["period", "account_id", "account_name", "account_type", "report_group", "cost_center_id",
                          "cost_center_name", "actual", "budget", "variance", "variance_pct"]),
    "variance_drivers.csv": ("fin.v_variance_drivers",
                             ["period", "account_id", "account_name", "volume_driver", "rate_driver", "volume_actual",
                              "volume_budget", "rate_actual", "rate_budget", "actual", "budget", "variance", "volume_effect",
                              "rate_effect", "other_effect"]),
    "balance_sheet.csv": ("fin.v_balance_sheet_monthly",          # not loaded by Qlik; used by the board report PDF
                          ["period", "fiscal_year", "quarter", "month_end", "is_opening", "cash", "gross_loans", "allowance",
                           "net_loans", "fixed_assets", "other_assets", "total_assets", "borrowings", "accrued_expenses",
                           "taxes_payable", "total_liabilities", "total_equity", "balance_check", "allowance_coverage",
                           "debt_to_equity", "month_net_income"]),
}
ORDER_BY = {"bva_lines.csv": "period, line_order", "bva_accounts.csv": "period, account_id, cost_center_id",
            "variance_drivers.csv": "period, account_id", "balance_sheet.csv": "period"}


def _ln(name: str) -> str:
    return f"[P&L Line]={{'{name}'}}"


def _line(name: str, field: str) -> str:
    return f"Sum({{<{_ln(name)}>}} {field})"


# (name, expression, format, what it means)
MEASURES: list[tuple[str, str, str, str]] = [
    ("Total Revenue", _line("Total Revenue", "Actual"), "#,##0", "Actual revenue before funding costs and credit losses (M03)."),
    ("Total Revenue Budget", _line("Total Revenue", "Budget"), "#,##0", "Budgeted revenue (M03)."),
    ("Net Interest Income", _line("Net Interest Income", "Actual"), "#,##0", "Interest earned minus interest paid (M05)."),
    ("Provision for Credit Losses", _line("Provision for Credit Losses", "Actual"), "#,##0", "Actual provision expense (M07)."),
    ("Operating Expenses", _line("Operating Expenses", "Actual"), "#,##0", "Actual running costs (M08)."),
    ("Net Income", _line("Net Income", "Actual"), "#,##0", "Actual profit after tax (M11)."),
    ("Net Income Budget", _line("Net Income", "Budget"), "#,##0", "Budgeted profit after tax (M11)."),
    ("Net Income Variance", _line("Net Income", "Variance"), "+#,##0;-#,##0", "Net income minus budget. Positive = favourable (M30)."),
    ("Net Income Variance %", f"{_line('Net Income', 'Variance')} / {_line('Net Income', 'Budget')}", "+0.0%;-0.0%",
     "Net income variance as a share of budget (M31)."),
    ("Net Profit Margin", f"{_line('Net Income', 'Actual')} / {_line('Total Revenue', 'Actual')}", "0.0%",
     "Net income divided by total revenue (M12)."),
    ("Cost-to-Income Ratio", f"{_line('Operating Expenses', 'Actual')} / {_line('Net Revenue', 'Actual')}", "0.0%",
     "Operating expenses divided by net revenue. Lower is better (M13)."),
    ("Actual", "Sum(Actual)", "#,##0", "Actual for the P&L lines in the table (use with the P&L Line dimension)."),
    ("Budget", "Sum(Budget)", "#,##0", "Budget for the P&L lines in the table."),
    ("Variance", "Sum(Variance)", "+#,##0;-#,##0", "Favourable variance is positive for revenue and cost lines alike (M30)."),
    ("Variance %", "Sum(Variance) / Sum(Budget)", "+0.0%;-0.0%", "Variance divided by budget (M31)."),
    ("Opex Variance by Cost Centre", "Sum({<[Account ID]={\">=6000<=6500\"}>} [Account Variance])", "+#,##0;-#,##0",
     "Operating expense variance (accounts 6000-6500); use with the Cost Center dimension."),
    ("Volume Effect", "Sum([Volume Effect])", "+#,##0;-#,##0", "Part of the variance caused by volume differing from budget (M32)."),
    ("Rate Effect", "Sum([Rate Effect])", "+#,##0;-#,##0", "Part of the variance caused by the rate differing from budget (M33)."),
    ("Other Effect", "Sum([Other Effect])", "+#,##0;-#,##0", "Variance on lines with no volume driver."),
]

WATERFALL_LINES = ["Interest Income", "Fee Income", "Interest Expense", "Provision for Credit Losses", "Operating Expenses",
                   "Income Tax"]


def export(dsn: str, out_dir: Path = QLIK_DIR / "data") -> dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = {}
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        for fname, (view, cols) in EXPORTS.items():
            sql = f"COPY (SELECT {', '.join(cols)} FROM {view} ORDER BY {ORDER_BY[fname]}) TO STDOUT WITH (FORMAT csv, HEADER true)"
            n = 0
            with open(out_dir / fname, "wb") as f, cur.copy(sql) as cp:
                for chunk in cp:
                    f.write(bytes(chunk))
            with open(out_dir / fname, "rb") as f:
                n = sum(1 for _ in f) - 1
            counts[fname] = n
    return counts


def measures_markdown() -> str:
    lines = ["# Finance sheet: master measures", "",
             "Generated by `src/export_for_qlik.py`. Create each one in Qlik Sense under Master items > Measures: "
             "paste the expression, set the number format with the format pattern, and use the name as shown.", "",
             "| Name | Expression | Format pattern | Meaning |", "|---|---|---|---|"]
    for n, e, f, d in MEASURES:
        lines.append(f"| {n} | `{e}` | `{f}` | {d} |")
    lines += ["", "## Waterfall: budget net income to actual net income", "",
              "Waterfall chart, measure items in this order. Each item uses the expression shown; "
              "set the last item to show as a subtotal.", "",
              "| Item | Expression |", "|---|---|",
              f"| Net Income Budget | `{_line('Net Income', 'Budget')}` |"]
    lines += [f"| {n} | `{_line(n, 'Variance')}` |" for n in WATERFALL_LINES]
    lines += [f"| Net Income (actual, subtotal) | `{_line('Net Income', 'Actual')}` |", ""]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dsn", default=os.environ.get("FIN_PG_DSN"))
    ap.add_argument("--out-dir", type=Path, default=QLIK_DIR / "data")
    a = ap.parse_args()
    if not a.dsn:
        sys.exit("No database connection. Pass --dsn or set the FIN_PG_DSN environment variable.")
    counts = export(a.dsn, a.out_dir)
    (QLIK_DIR).mkdir(parents=True, exist_ok=True)
    (QLIK_DIR / "finance_measures.md").write_text(measures_markdown(), encoding="utf-8")
    for f, n in counts.items():
        print(f"  {a.out_dir / f}: {n:,} rows")
    print(f"  {QLIK_DIR / 'finance_measures.md'}")


if __name__ == "__main__":
    main()
