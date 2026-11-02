"""Data checks: balance sheet balances, P&L totals match the ledger, variances add up.

Runs two groups of checks and writes the results as evidence:
  A. LEDGER checks on data/synthetic/*.csv   (always run, no database needed)
  B. SQL checks on the PostgreSQL views      (run when a connection string is given)
     plus Lending Club checks                (run when risk.lending_club_sample has rows)

Every check has an ID, a plain-English description, the expected and the actual value, and PASS / FAIL.
Checks that cannot run are listed as SKIPPED, never silently left out.

    python src/run_data_checks.py                      (ledger checks only)
    python src/run_data_checks.py --dsn postgresql://postgres:PASSWORD@localhost:5432/fin_risk
    # or set FIN_PG_DSN once and omit --dsn

Writes reports/data_checks.csv and reports/data_checks.md. Exit code 1 if any check FAILS.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from finance_metrics import annual_summary, balance_sheet, bva_net_income, pl_by_period, variance_drivers

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_DIR = ROOT / "data" / "synthetic"
TABLES = ["dim_account", "dim_cost_center", "dim_vendor", "dim_period",
          "fact_gl_journal", "fact_budget", "fact_drivers"]
MONEY_TOL = 0.05      # USD; amounts are stored to the cent and sums of ~1,800 lines can drift a few cents
RATIO_TOL = 1e-6


@dataclass
class Result:
    id: str
    area: str
    description: str
    expected: str
    actual: str
    status: str   # PASS, FAIL or SKIPPED


class Checks:
    def __init__(self) -> None:
        self.results: list[Result] = []

    def add(self, cid: str, area: str, description: str, passed: bool, expected: str, actual: str) -> None:
        self.results.append(Result(cid, area, description, expected, actual, "PASS" if passed else "FAIL"))

    def skip(self, cid: str, area: str, description: str, reason: str) -> None:
        self.results.append(Result(cid, area, description, "", reason, "SKIPPED"))

    def number(self, cid, area, description, value, expected=0.0, tol=MONEY_TOL, fmt="{:,.2f}") -> None:
        """PASS when |value - expected| <= tol."""
        value = float(value)
        self.add(cid, area, description, abs(value - expected) <= tol,
                 f"{fmt.format(expected)} (tolerance {tol:g})", fmt.format(value))


# ================================================================== A. ledger checks (CSV files)
def load_tables(directory: Path = SYNTHETIC_DIR) -> dict[str, pd.DataFrame]:
    return {name: pd.read_csv(directory / f"{name}.csv", keep_default_na=False, na_values=[""]) for name in TABLES}


def ledger_checks(c: Checks, t: dict[str, pd.DataFrame]) -> None:
    gl, bud, drv = t["fact_gl_journal"], t["fact_budget"], t["fact_drivers"]
    A = "Ledger"

    # ---- double entry
    per_journal = (gl["debit"] - gl["credit"]).groupby(gl["journal_id"]).sum().abs()
    c.number("L01", A, "Every journal has debits equal to credits (largest gap across all journals)", per_journal.max())
    c.number("L02", A, "Trial balance: total debits minus total credits across the whole ledger",
             gl["debit"].sum() - gl["credit"].sum())
    both = ((gl["debit"] > 0) & (gl["credit"] > 0)).sum()
    neg = ((gl["debit"] < 0) | (gl["credit"] < 0)).sum()
    c.add("L03", A, "No ledger line has both a debit and a credit, or a negative amount", both + neg == 0,
          "0 lines", f"{both + neg} lines")

    # ---- keys and coverage
    bad = 0
    for col, dim, key in (("account_id", "dim_account", "account_id"), ("period", "dim_period", "period"),
                          ("cost_center_id", "dim_cost_center", "cost_center_id"), ("vendor_id", "dim_vendor", "vendor_id")):
        vals = gl[col].dropna()
        bad += (~vals.isin(t[dim][key])).sum()
    bad += (~bud["account_id"].isin(t["dim_account"]["account_id"])).sum() + (~bud["period"].isin(t["dim_period"]["period"])).sum()
    c.add("L04", A, "Every key in the ledger and budget exists in its dimension table", bad == 0, "0 orphan keys", f"{bad} orphan keys")

    periods = sorted(t["dim_period"]["period"])
    expected_periods = [str(p) for p in pd.period_range("2022-12", "2025-12", freq="M")]
    c.add("L05", A, "Periods run month by month from 2022-12 (opening) to 2025-12 with no gap",
          periods == expected_periods, f"{len(expected_periods)} periods", f"{len(periods)} periods")

    keys = bud.assign(cc=bud["cost_center_id"].fillna(""))[["period", "account_id", "cc"]]
    n_combos = keys.drop_duplicates(["account_id", "cc"]).shape[0]
    full = len(keys.drop_duplicates()) == len(keys) == n_combos * 36
    c.add("L06", A, "Budget has one row per account and cost centre for each of the 36 months, no duplicates",
          full, f"{n_combos * 36} rows", f"{len(keys)} rows, {len(keys.drop_duplicates())} distinct")

    sc = drv["scenario"].value_counts()
    c.add("L07", A, "Drivers cover all 36 months for both Actual and Budget",
          sc.get("Actual", 0) == 36 and sc.get("Budget", 0) == 36, "36 + 36 rows", f"{sc.get('Actual', 0)} + {sc.get('Budget', 0)} rows")

    roll = drv["opening_loan_book"] + drv["originations"] - drv["repayments"] - drv["writeoffs"] - drv["closing_loan_book"]
    c.number("L08", A, "Loan book roll-forward: opening + originations - repayments - write-offs = closing (largest gap)",
             roll.abs().max())

    # ---- balance sheet balances (from the CSV ledger)
    bs = balance_sheet(t)
    gap = (bs["total_assets"] - bs["total_liabilities"] - bs["equity"]).abs()
    c.number("L09", A, "Balance sheet: assets = liabilities + equity in every one of the 37 month-ends (largest gap)", gap.max())
    c.number("L10", A, "Allowance for credit losses is never above gross loans (months where it is)",
             (bs["allowance"] > bs["gross_loans"]).sum(), tol=0, fmt="{:,.0f}")

    # ---- P&L totals match the ledger (independent derivation straight from journal lines)
    pl = pl_by_period(t)
    pl_lines = gl[gl["account_id"] >= 4000]
    ledger_ni = (pl_lines["credit"] - pl_lines["debit"]).groupby(pl_lines["period"].str[:4]).sum()
    metric_ni = pl["net_income"].groupby(pl.index.str[:4]).sum()
    for y in ledger_ni.index:
        c.number(f"L11-{y}", A, f"P&L net income FY{y} equals credits minus debits on all P&L accounts in the ledger",
                 metric_ni[y] - ledger_ni[y])
    fy = annual_summary(t)
    c.number("L12", A, "P&L identity: revenue - interest expense - provision - operating expenses - tax = net income (largest gap)",
             (fy.total_revenue - fy.interest_expense - fy.provision - fy.opex - fy.tax - fy.net_income).abs().max())

    # ---- variances sum correctly
    ni = bva_net_income(t)
    c.number("L13", A, "Budget variances: the sum of all account variances equals the net income variance (largest gap by year)",
             (ni["sum_of_line_variances"] - ni["net_income_variance"]).abs().max())
    v = variance_drivers(t).fillna(0.0)
    c.number("L14", A, "Variance drivers: volume + rate + other effect equals the variance on every row (largest gap)",
             (v.volume_effect + v.rate_effect + v.other_effect - v.variance).abs().max(), tol=0.011)
    budget_signed = bud.assign(s=(bud["account_id"] < 5000).map({True: 1, False: -1}))
    budget_ni = (budget_signed["s"] * budget_signed["budget_amount"]).sum()
    c.number("L15", A, "Budget net income over 36 months equals revenue minus costs in fact_budget",
             ni["net_income_budget"].sum() - budget_ni)


# ================================================================== B. SQL checks (PostgreSQL)
SQL_CHECK_IDS = [("S01", "Row counts of the 7 loaded tables equal the CSV files"),
                 ("S02", "Balance sheet view: balance_check is 0 in every month-end"),
                 ("S03", "Balance sheet view total assets match the Python reference (largest gap)"),
                 ("S04", "P&L views: net income equals credits minus debits on P&L accounts in the ledger table (by year)"),
                 ("S05", "P&L views: quarters add up to years, income statement equals the monthly view"),
                 ("S06", "Budget vs actual: actual net income equals the P&L view, month by month"),
                 ("S07", "Budget vs actual: budget net income equals the budget table (by year)"),
                 ("S08", "Budget vs actual: sum of all account variances equals the net income variance (by year)"),
                 ("S09", "Budget vs actual: quarters and cost centres add up to the annual figures"),
                 ("S10", "Variance drivers: volume + rate + other equals the variance on every row"),
                 ("S11", "Views are populated (row counts as expected)")]


def _txt(v):
    """Some database encodings (SQL_ASCII) return text as bytes."""
    return v.decode("utf-8") if isinstance(v, bytes) else v


def _one(cur, sql):
    cur.execute(sql)
    return cur.fetchone()[0]


def sql_checks(c: Checks, conn, t: dict[str, pd.DataFrame]) -> None:
    A = "SQL"
    with conn.cursor() as cur:
        counts = {n: _one(cur, f"SELECT COUNT(*) FROM fin.{n}") for n in TABLES}
        wrong = {n: (counts[n], len(t[n])) for n in TABLES if counts[n] != len(t[n])}
        c.add("S01", A, "Row counts of the 7 loaded tables equal the CSV files", not wrong,
              "all 7 equal", "all equal" if not wrong else str(wrong))

        c.number("S02", A, "Balance sheet view: largest absolute balance_check over the 37 month-ends",
                 _one(cur, "SELECT MAX(ABS(balance_check)) FROM fin.v_balance_sheet_monthly"), tol=0.005)
        cur.execute("SELECT period, total_assets FROM fin.v_balance_sheet_monthly ORDER BY period")
        sql_ta = pd.Series({_txt(p): float(a) for p, a in cur.fetchall()})
        ref_ta = balance_sheet(t)["total_assets"]
        c.number("S03", A, "Balance sheet view total assets match the Python reference (largest gap)",
                 (sql_ta - ref_ta.reindex(sql_ta.index)).abs().max())

        cur.execute("""SELECT SUBSTRING(period, 1, 4) AS y, SUM(credit - debit)
                       FROM fin.fact_gl_journal WHERE account_id >= 4000 GROUP BY 1 ORDER BY 1""")
        ledger = {_txt(y): float(v) for y, v in cur.fetchall()}
        cur.execute("SELECT fiscal_year::text, net_income FROM fin.v_pl_annual ORDER BY fiscal_year")
        pl_year = {_txt(y): float(v) for y, v in cur.fetchall()}
        gap = max(abs(pl_year[y] - ledger[y]) for y in ledger)
        c.number("S04", A, "P&L annual view net income vs the ledger table, credits minus debits (largest gap by year)", gap)

        q_gap = _one(cur, """SELECT MAX(ABS(q.s - a.net_income)) FROM
                   (SELECT fiscal_year, SUM(net_income) AS s FROM fin.v_pl_quarterly GROUP BY fiscal_year) q
                   JOIN fin.v_pl_annual a USING (fiscal_year)""")
        i_gap = _one(cur, """SELECT ABS((SELECT SUM(amount) FROM fin.v_income_statement WHERE line_name = 'Net Income')
                                       - (SELECT SUM(net_income) FROM fin.v_pl_monthly))""")
        c.number("S05", A, "P&L views: quarters vs years and income statement vs monthly view (largest gap)", max(float(q_gap), float(i_gap)))

        s6 = _one(cur, """SELECT MAX(ABS(b.actual - p.net_income)) FROM fin.v_bva_summary b
                          JOIN fin.v_pl_monthly p USING (period) WHERE b.line_name = 'Net Income'""")
        c.number("S06", A, "Budget vs actual: actual net income vs the P&L view (largest monthly gap)", s6)

        cur.execute("""SELECT SUBSTRING(period, 1, 4), SUM(CASE WHEN account_id < 5000 THEN budget_amount ELSE -budget_amount END)
                       FROM fin.fact_budget GROUP BY 1 ORDER BY 1""")
        bud = {_txt(y): float(v) for y, v in cur.fetchall()}
        cur.execute("SELECT fiscal_year::text, budget FROM fin.v_bva_summary_annual WHERE line_name = 'Net Income' ORDER BY 1")
        view_bud = {_txt(y): float(v) for y, v in cur.fetchall()}
        c.number("S07", A, "Budget vs actual: budget net income vs the budget table (largest gap by year)",
                 max(abs(view_bud[y] - bud[y]) for y in bud))

        s8 = _one(cur, """SELECT MAX(ABS(a.v - n.variance)) FROM
                   (SELECT fiscal_year, SUM(variance) AS v FROM fin.v_bva_account_month GROUP BY fiscal_year) a
                   JOIN fin.v_bva_summary_annual n USING (fiscal_year) WHERE n.line_name = 'Net Income'""")
        c.number("S08", A, "Budget vs actual: sum of account variances vs net income variance (largest gap by year)", s8)

        s9q = _one(cur, """SELECT MAX(ABS(q.v - a.variance)) FROM
                   (SELECT fiscal_year, line_name, SUM(variance) AS v FROM fin.v_bva_summary_quarterly GROUP BY 1, 2) q
                   JOIN fin.v_bva_summary_annual a USING (fiscal_year, line_name)""")
        s9c = _one(cur, """SELECT MAX(ABS(k.v - a.variance)) FROM
                   (SELECT fiscal_year, SUM(variance) AS v FROM fin.v_bva_cost_center_annual GROUP BY 1) k
                   JOIN fin.v_bva_summary_annual a USING (fiscal_year) WHERE a.line_name = 'Operating Expenses'""")
        c.number("S09", A, "Budget vs actual: quarters and cost centres vs annual (largest gap)", max(float(s9q), float(s9c)))

        s10 = _one(cur, """SELECT MAX(ABS(variance - (COALESCE(volume_effect, 0) + COALESCE(rate_effect, 0) + other_effect)))
                           FROM fin.v_variance_drivers""")
        c.number("S10", A, "Variance drivers: volume + rate + other vs variance (largest gap on any row)", s10, tol=0.011)

        want = {"fin.v_pl_monthly": 36, "fin.v_balance_sheet_monthly": 37, "fin.v_bva_summary": 396,
                "fin.v_variance_drivers": 396, "fin.v_bva_summary_annual": 33}
        got = {v: _one(cur, f"SELECT COUNT(*) FROM {v}") for v in want}
        c.add("S11", A, "Views are populated (row counts as expected)", got == want, str(want), str(got))


def lending_club_checks(c: Checks, conn) -> None:
    A = "Lending Club"
    with conn.cursor() as cur:
        n = _one(cur, "SELECT COUNT(*) FROM risk.lending_club_sample")
        if n == 0:
            for cid, d in (("R01", "Default flag agrees with loan status"), ("R02", "Only 36-month loans issued 2013-2015"),
                           ("R03", "Default rate per issue year is plausible (5%-35%)"), ("R04", "FICO range and issue year are consistent")):
                c.skip(cid, A, d, "risk.lending_club_sample is empty (load it with --lending-club)")
            return
        bad = _one(cur, """SELECT COUNT(*) FROM risk.lending_club_sample
                           WHERE ("default" = 1) <> (loan_status = 'Charged Off')""")
        c.number("R01", A, "Loans where the default flag disagrees with 'Charged Off' status", bad, tol=0, fmt="{:,.0f}")
        bad = _one(cur, """SELECT COUNT(*) FROM risk.lending_club_sample
                           WHERE issue_year NOT BETWEEN 2013 AND 2015 OR term_months <> 36""")
        c.number("R02", A, "Loans outside the design (36 months, issued 2013-2015)", bad, tol=0, fmt="{:,.0f}")
        cur.execute("""SELECT issue_year, AVG("default"::numeric) FROM risk.lending_club_sample GROUP BY 1 ORDER BY 1""")
        rates = {int(y): float(r) for y, r in cur.fetchall()}
        c.add("R03", A, "Default rate per issue year is plausible (between 5% and 35%)",
              bool(rates) and all(0.05 <= r <= 0.35 for r in rates.values()),
              "each year 5%-35%", ", ".join(f"{y}: {r:.1%}" for y, r in rates.items()))
        bad = _one(cur, """SELECT COUNT(*) FROM risk.lending_club_sample
                           WHERE fico_range_low > fico_range_high OR issue_year <> EXTRACT(YEAR FROM issue_d)""")
        c.number("R04", A, "Loans with FICO low above high, or issue year not matching issue date", bad, tol=0, fmt="{:,.0f}")


# ================================================================== reporting
def write_reports(results: list[Result], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([r.__dict__ for r in results])
    df.to_csv(out_dir / "data_checks.csv", index=False)
    n = df["status"].value_counts()
    lines = ["# Data checks", "",
             f"**{n.get('PASS', 0)} passed, {n.get('FAIL', 0)} failed, {n.get('SKIPPED', 0)} skipped.**", "",
             "Generated by `src/run_data_checks.py`. Ledger checks read `data/synthetic/*.csv`; SQL checks read the "
             "PostgreSQL views; Lending Club checks read `risk.lending_club_sample`.", "",
             "| ID | Area | Check | Expected | Actual | Result |", "|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r.id} | {r.area} | {r.description} | {r.expected} | {r.actual} | {r.status} |")
    (out_dir / "data_checks.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(dsn: str | None, data_dir: Path = SYNTHETIC_DIR) -> list[Result]:
    c = Checks()
    t = load_tables(data_dir)
    ledger_checks(c, t)
    if dsn:
        import psycopg
        with psycopg.connect(dsn) as conn:
            sql_checks(c, conn, t)
            lending_club_checks(c, conn)
    else:
        for cid, d in SQL_CHECK_IDS:
            c.skip(cid, "SQL", d, "no database connection (set FIN_PG_DSN or pass --dsn)")
    return c.results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dsn", default=os.environ.get("FIN_PG_DSN"), help="PostgreSQL connection string (or env FIN_PG_DSN)")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "reports")
    a = ap.parse_args()
    results = run(a.dsn)
    write_reports(results, a.out_dir)
    for r in results:
        print(f"{r.status:7} {r.id:8} {r.description}  ->  {r.actual}")
    failed = [r for r in results if r.status == "FAIL"]
    n = len(results)
    print(f"\n{sum(r.status == 'PASS' for r in results)} passed, {len(failed)} failed, "
          f"{sum(r.status == 'SKIPPED' for r in results)} skipped of {n}. Written to {a.out_dir}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
