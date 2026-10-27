"""Generate synthetic ledgers for a fictional lender (Kestrel Credit Ltd).

What it produces (CSV files in data/synthetic/):

    dim_account.csv       chart of accounts
    dim_cost_center.csv   cost centres
    dim_vendor.csv        vendors (names generated with Faker)
    dim_period.csv        month calendar (includes the opening-balance month)
    fact_gl_journal.csv   double-entry general ledger, one row per journal line
    fact_budget.csv       monthly budget for every P&L account
    fact_drivers.csv      monthly business drivers for Actual and Budget

How it works
------------
A small monthly model of a consumer lender is simulated twice with the same code:

* Budget run: planned assumptions, no noise.
* Actual run: the same model with random noise plus a few injected events
  (see `actual_shocks`), so budget vs actual variances have real causes:
  a pricing squeeze in H2 2024, a credit-loss spike in Apr-Sep 2025, a marketing
  overspend in 2025, a one-off technology cost in Mar 2024, and delayed hiring.

Every transaction is posted as a balanced double-entry journal, in integer cents,
so the trial balance and balance sheet balance exactly. Only the Actual run is
written to the general ledger. The Budget run is aggregated into fact_budget.

Simplifications (also listed in docs/data_dictionary.md)
-------------------------------------------------------
* All amounts are in USD. Postings are dated at month end.
* Interest is collected in cash in the month it is earned (no accrued interest receivable).
* Fixed assets are held net of depreciation (no accumulated depreciation account).
* No closing entries: the balance sheet adds cumulative net income to opening retained earnings.
* Borrowings are drawn or repaid in 500k steps at month end to keep cash in a target band.
* Financial numbers are fully determined by the seed. Vendor names come from Faker, so they
  can differ between Faker versions; commit the generated CSVs to keep them stable.

Run:  python src/generate_ledgers.py            (writes to data/synthetic/)
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from faker import Faker

COMPANY = "Kestrel Credit Ltd"
SEED = 42
FIRST_MONTH = "2023-01"
N_MONTHS = 36
OPENING_PERIOD = "2022-12"

# id, name, type, statement, normal balance, reporting group
ACCOUNTS = [
    (1000, "Cash and Bank", "Asset", "BS", "D", "Current Assets"),
    (1100, "Loans Receivable (Gross)", "Asset", "BS", "D", "Loan Book"),
    (1110, "Allowance for Credit Losses", "Asset", "BS", "C", "Loan Book"),
    (1200, "Fixed Assets (Net)", "Asset", "BS", "D", "Non-current Assets"),
    (1300, "Prepaid and Other Assets", "Asset", "BS", "D", "Current Assets"),
    (2000, "Borrowings", "Liability", "BS", "C", "Debt"),
    (2100, "Accrued Expenses", "Liability", "BS", "C", "Current Liabilities"),
    (2200, "Taxes Payable", "Liability", "BS", "C", "Current Liabilities"),
    (3000, "Share Capital", "Equity", "BS", "C", "Equity"),
    (3100, "Retained Earnings (Opening)", "Equity", "BS", "C", "Equity"),
    (4000, "Interest Income", "Revenue", "PL", "C", "Interest Income"),
    (4100, "Fee Income", "Revenue", "PL", "C", "Fee Income"),
    (5000, "Interest Expense", "Expense", "PL", "D", "Interest Expense"),
    (5100, "Provision for Credit Losses", "Expense", "PL", "D", "Credit Costs"),
    (6000, "Salaries and Benefits", "Expense", "PL", "D", "Operating Expenses"),
    (6100, "Marketing", "Expense", "PL", "D", "Operating Expenses"),
    (6200, "Technology and Software", "Expense", "PL", "D", "Operating Expenses"),
    (6300, "Rent and Administration", "Expense", "PL", "D", "Operating Expenses"),
    (6400, "Collections and Recovery Costs", "Expense", "PL", "D", "Operating Expenses"),
    (6500, "Depreciation", "Expense", "PL", "D", "Operating Expenses"),
    (7000, "Income Tax", "Expense", "PL", "D", "Tax"),
]

COST_CENTERS = [
    ("OPS", "Operations"),
    ("SMK", "Sales and Marketing"),
    ("TEC", "Technology"),
    ("RSK", "Risk and Collections"),
    ("COR", "Corporate"),
]
SALARY_SHARE = {"OPS": 0.35, "SMK": 0.15, "TEC": 0.25, "RSK": 0.15, "COR": 0.10}
ACCOUNT_COST_CENTER = {5000: "COR", 5100: "RSK", 6100: "SMK", 6200: "TEC",
                       6300: "COR", 6400: "RSK", 6500: "COR", 7000: "COR"}

VENDOR_COUNTS = {
    "Advertising and Media": 6,
    "Cloud and Software": 6,
    "Facilities": 3,
    "Professional Services": 4,
    "Collections Agency": 4,
}

CASH_MIN = 2_000_000
CASH_MAX = 4_500_000
DEBT_STEP = 500_000


@dataclass
class Params:
    """Model assumptions. Defaults are the budget assumptions."""

    orig_base: float = 5_000_000      # new loans disbursed in month 0
    orig_growth: float = 0.012        # monthly growth in originations
    seasonality: tuple = (0.92, 0.95, 1.02, 1.03, 1.05, 1.04,
                          1.02, 1.03, 1.02, 1.05, 1.00, 0.87)
    repay_rate: float = 0.042         # share of opening book repaid per month
    chargeoff_rate: float = 0.0015    # share of opening book written off per month
    yield_annual: float = 0.16        # interest yield on average book
    fee_rate: float = 0.015           # origination fee on new loans
    cost_of_funds: float = 0.08       # annual rate on opening borrowings
    coverage: float = 0.045           # allowance as share of gross loans
    headcount_start: int = 35
    hire_every: int = 3               # one hire every N months
    cost_per_head: float = 4_000      # monthly cost per employee in 2023
    merit_raise: float = 0.04         # applied each January
    marketing_pct: float = 0.011      # marketing as share of originations
    tech_fixed: float = 38_000
    tech_var_pct: float = 0.004       # technology as share of originations
    rent: float = 22_000
    admin_other: float = 12_000
    admin_infl: float = 0.003         # monthly inflation on fixed costs
    collections_pct: float = 0.0004   # collections cost as share of opening book
    capex: float = 25_000
    dep_rate: float = 0.02            # monthly depreciation on opening net fixed assets
    tax_rate: float = 0.25
    accrual_share: float = 0.30       # share of vendor costs paid the following month


def c(x: float) -> int:
    """Dollars to integer cents."""
    return int(round(x * 100))


def neutral_shocks(n: int) -> pd.DataFrame:
    return pd.DataFrame({
        "orig_mult": np.ones(n), "yield_add": np.zeros(n), "chargeoff_mult": np.ones(n),
        "coverage_add": np.zeros(n), "mkt_mult": np.ones(n), "tech_oneoff": np.zeros(n),
        "hire_gap": np.zeros(n, dtype=int), "salary_mult": np.ones(n),
        "repay_mult": np.ones(n), "capex_mult": np.ones(n),
    })


def actual_shocks(rng: np.random.Generator, n: int) -> pd.DataFrame:
    """Random noise plus injected events. Month index 0 = Jan 2023."""
    s = neutral_shocks(n)
    s["orig_mult"] = np.exp(rng.normal(0, 0.06, n))
    s["yield_add"] = rng.normal(0, 0.0010, n)
    s["chargeoff_mult"] = np.exp(rng.normal(0, 0.10, n))
    s["mkt_mult"] = np.exp(rng.normal(0, 0.05, n))
    s["salary_mult"] = 1 + rng.normal(0, 0.01, n)
    s["repay_mult"] = np.exp(rng.normal(0, 0.04, n))
    s["capex_mult"] = np.exp(rng.normal(0, 0.25, n))

    s.loc[0:11, "orig_mult"] *= 1.03        # 2023: slightly ahead of plan
    s.loc[18:23, "orig_mult"] *= 0.92       # Jul-Dec 2024: competition cuts volume
    s.loc[18:23, "yield_add"] -= 0.006      # ... and forces lower pricing
    s.loc[24:29, "yield_add"] -= 0.003      # Jan-Jun 2025: pricing partly recovers
    s.loc[27:32, "chargeoff_mult"] *= 1.50  # Apr-Sep 2025: credit stress
    s.loc[33:35, "chargeoff_mult"] *= 1.15  # ... easing
    for i, k in enumerate(range(27, 33)):   # reserves built up during the stress
        s.loc[k, "coverage_add"] = 0.0075 * (i + 1) / 6
    s.loc[33:35, "coverage_add"] = 0.0075
    s.loc[24:35, "mkt_mult"] *= 1.10        # 2025: marketing overspend
    s.loc[14, "tech_oneoff"] = 120_000      # Mar 2024: one-off platform project
    s.loc[6:11, "hire_gap"] = 1             # Jul-Dec 2023: a hire delayed
    s.loc[20:26, "hire_gap"] = 2            # Sep 2024-Mar 2025: two hires delayed
    return s


class Ledger:
    """Double-entry ledger in integer cents. Rejects any unbalanced journal."""

    def __init__(self) -> None:
        self.lines: list[dict] = []
        self.bal: dict[int, int] = defaultdict(int)  # debit-positive
        self._seq: dict[str, int] = defaultdict(int)

    def post(self, period: str, source: str, lines: list[tuple]) -> str:
        """lines: (account_id, debit_cents, credit_cents, cost_center, vendor_id, memo)."""
        lines = [ln for ln in lines if ln[1] or ln[2]]
        if not lines:
            return ""
        if any(ln[1] < 0 or ln[2] < 0 for ln in lines):
            raise ValueError(f"negative amount in {source} {period}")
        if sum(ln[1] for ln in lines) != sum(ln[2] for ln in lines):
            raise ValueError(f"unbalanced journal {source} {period}")
        self._seq[period] += 1
        jid = f"JE-{period.replace('-', '')}-{self._seq[period]:03d}"
        for n, (acct, dr, cr, cc, vendor, memo) in enumerate(lines, 1):
            self.lines.append({
                "journal_id": jid, "line_no": n, "period": period, "account_id": acct,
                "debit_c": dr, "credit_c": cr, "cost_center_id": cc,
                "vendor_id": vendor, "source": source, "memo": memo,
            })
            self.bal[acct] += dr - cr
        return jid

    def credit_balance(self, acct: int) -> int:
        return -self.bal[acct]


def make_vendors(seed: int) -> pd.DataFrame:
    Faker.seed(seed)
    fake = Faker("en_US")
    rows, used, n = [], set(), 0
    for category, count in VENDOR_COUNTS.items():
        for _ in range(count):
            name = fake.company()
            while name in used:
                name = fake.company()
            used.add(name)
            n += 1
            rows.append({"vendor_id": f"V{n:03d}", "vendor_name": name, "category": category})
    return pd.DataFrame(rows)


def split_to_vendors(total_c: int, category: str, vendors: pd.DataFrame,
                     rng: np.random.Generator) -> list[tuple[str, int]]:
    """Split an amount across 2-3 vendors of a category, summing exactly to total_c."""
    pool = vendors.loc[vendors["category"] == category, "vendor_id"].tolist()
    k = min(len(pool), int(rng.integers(2, 4)))
    chosen = list(rng.choice(pool, size=k, replace=False))
    weights = rng.dirichlet(np.ones(k))
    parts = [int(total_c * w) for w in weights]
    parts[0] += total_c - sum(parts)
    return list(zip(chosen, parts))


def simulate(p: Params, shocks: pd.DataFrame, periods: list[pd.Period],
             vendors: pd.DataFrame, vendor_rng: np.random.Generator) -> tuple[Ledger, pd.DataFrame]:
    led = Ledger()

    # ---- opening balances at 31 Dec 2022 ----
    cash0, loans0, allow0, fa0, other0 = 3_000_000, 60_000_000, 2_700_000, 1_200_000, 500_000
    debt0, accrued0, taxpay0, capital0 = 40_000_000, 120_000, 80_000, 12_000_000
    assets0 = cash0 + loans0 - allow0 + fa0 + other0
    re0 = assets0 - (debt0 + accrued0 + taxpay0) - capital0
    led.post(OPENING_PERIOD, "OPENING", [
        (1000, c(cash0), 0, None, None, "Opening balance"),
        (1100, c(loans0), 0, None, None, "Opening balance"),
        (1110, 0, c(allow0), None, None, "Opening balance"),
        (1200, c(fa0), 0, None, None, "Opening balance"),
        (1300, c(other0), 0, None, None, "Opening balance"),
        (2000, 0, c(debt0), None, None, "Opening balance"),
        (2100, 0, c(accrued0), None, None, "Opening balance"),
        (2200, 0, c(taxpay0), None, None, "Opening balance"),
        (3000, 0, c(capital0), None, None, "Opening balance"),
        (3100, 0, c(re0), None, None, "Opening balance"),
    ])

    drivers = []
    ytd_pbt, tax_ytd = 0, 0
    for t, per in enumerate(periods):
        sh = shocks.iloc[t]
        pk = str(per)
        if per.month == 1:
            ytd_pbt, tax_ytd = 0, 0

        loans_open = led.bal[1100]
        debt_open = led.credit_balance(2000)
        allow_open = led.credit_balance(1110)
        fa_open = led.bal[1200]
        accrued_open = led.credit_balance(2100)

        # ---- loan book ----
        season = p.seasonality[per.month - 1]
        orig = c(p.orig_base * (1 + p.orig_growth) ** t * season * sh.orig_mult)
        repay = c(loans_open / 100 * p.repay_rate * sh.repay_mult)
        wo = c(loans_open / 100 * p.chargeoff_rate * sh.chargeoff_mult)
        loans_close = loans_open + orig - repay - wo
        avg_book = (loans_open + loans_close) / 2

        # ---- income and funding cost ----
        int_inc = c(avg_book / 100 * (p.yield_annual + sh.yield_add) / 12)
        fees = c(orig / 100 * p.fee_rate)
        int_exp = c(debt_open / 100 * p.cost_of_funds / 12)

        # ---- credit costs ----
        allow_close = c(loans_close / 100 * (p.coverage + sh.coverage_add))
        prov = allow_close - allow_open + wo

        # ---- operating costs ----
        headcount = p.headcount_start + t // p.hire_every - int(sh.hire_gap)
        cost_per_head = p.cost_per_head * (1 + p.merit_raise) ** (per.year - 2023)
        salaries = c(headcount * cost_per_head * sh.salary_mult)
        marketing = c(orig / 100 * p.marketing_pct * sh.mkt_mult)
        tech = c(p.tech_fixed * (1 + p.admin_infl) ** t + orig / 100 * p.tech_var_pct + sh.tech_oneoff)
        rent = c(p.rent * (1 + p.admin_infl) ** t)
        admin = c(p.admin_other * (1 + p.admin_infl) ** t)
        collections = c(loans_open / 100 * p.collections_pct * sh.chargeoff_mult)
        dep = c(fa_open / 100 * p.dep_rate)
        capex = c(p.capex * sh.capex_mult)

        pbt = (int_inc + fees - int_exp - prov - salaries - marketing - tech
               - rent - admin - collections - dep)
        ytd_pbt += pbt
        target_tax = max(0, c(ytd_pbt / 100 * p.tax_rate))
        tax = target_tax - tax_ytd
        tax_ytd = target_tax

        # ---- postings ----
        led.post(pk, "ORIGINATION", [(1100, orig, 0, None, None, "New loans disbursed"),
                                     (1000, 0, orig, None, None, "Disbursement")])
        led.post(pk, "FEE_INCOME", [(1000, fees, 0, None, None, "Origination fees collected"),
                                    (4100, 0, fees, None, None, "Origination fees")])
        led.post(pk, "INTEREST_INCOME", [(1000, int_inc, 0, None, None, "Interest collected"),
                                         (4000, 0, int_inc, None, None, "Interest on loan book")])
        led.post(pk, "REPAYMENT", [(1000, repay, 0, None, None, "Principal repaid"),
                                   (1100, 0, repay, None, None, "Principal repaid")])
        led.post(pk, "CHARGE_OFF", [(1110, wo, 0, None, None, "Loans written off against allowance"),
                                    (1100, 0, wo, None, None, "Loans written off")])
        led.post(pk, "PROVISION", [(5100, prov, 0, "RSK", None, "Credit loss provision"),
                                   (1110, 0, prov, None, None, "Allowance top-up")])
        led.post(pk, "INTEREST_EXPENSE", [(5000, int_exp, 0, "COR", None, "Interest on borrowings"),
                                          (1000, 0, int_exp, None, None, "Interest paid")])
        led.post(pk, "ACCRUAL_SETTLEMENT", [(2100, accrued_open, 0, None, None, "Prior month accruals paid"),
                                            (1000, 0, accrued_open, None, None, "Paid")])

        pay_lines, assigned = [], 0
        ccs = list(SALARY_SHARE)
        for i, cc in enumerate(ccs):
            amt = salaries - assigned if i == len(ccs) - 1 else int(salaries * SALARY_SHARE[cc])
            assigned += amt
            pay_lines.append((6000, amt, 0, cc, None, f"Payroll {cc}"))
        pay_lines.append((1000, 0, salaries, None, None, "Payroll paid"))
        led.post(pk, "PAYROLL", pay_lines)

        opex_jobs = {
            6100: [(marketing, "Advertising and Media", "Marketing spend")],
            6200: [(tech, "Cloud and Software", "Technology spend")],
            6300: [(rent, "Facilities", "Rent and facilities"),
                   (admin, "Professional Services", "Administration")],
            6400: [(collections, "Collections Agency", "Collections fees")],
        }
        for acct, parts in opex_jobs.items():
            lines, total = [], 0
            for amount, category, memo in parts:
                for vendor_id, part in split_to_vendors(amount, category, vendors, vendor_rng):
                    lines.append((acct, part, 0, ACCOUNT_COST_CENTER[acct], vendor_id, memo))
                total += amount
            accrued = c(total / 100 * p.accrual_share)
            lines.append((1000, 0, total - accrued, None, None, "Paid in month"))
            lines.append((2100, 0, accrued, None, None, "Accrued, paid next month"))
            led.post(pk, f"OPEX_{acct}", lines)

        led.post(pk, "DEPRECIATION", [(6500, dep, 0, "COR", None, "Monthly depreciation"),
                                      (1200, 0, dep, None, None, "Depreciation")])
        led.post(pk, "CAPEX", [(1200, capex, 0, None, None, "Equipment purchases"),
                               (1000, 0, capex, None, None, "Paid")])
        if tax >= 0:
            led.post(pk, "TAX", [(7000, tax, 0, "COR", None, "Income tax accrual"),
                                 (2200, 0, tax, None, None, "Tax payable")])
        else:
            led.post(pk, "TAX", [(2200, -tax, 0, None, None, "Tax accrual reversal"),
                                 (7000, 0, -tax, "COR", None, "Income tax reversal")])
        if per.month in (3, 6, 9, 12) and led.credit_balance(2200) > 0:
            owed = led.credit_balance(2200)
            led.post(pk, "TAX_PAYMENT", [(2200, owed, 0, None, None, "Quarterly tax paid"),
                                         (1000, 0, owed, None, None, "Paid")])

        # ---- funding: keep cash inside the target band ----
        cash = led.bal[1000]
        if cash < c(CASH_MIN):
            draw = -(-(c(CASH_MIN) - cash) // c(DEBT_STEP)) * c(DEBT_STEP)
            led.post(pk, "BORROWING_DRAW", [(1000, draw, 0, None, None, "Facility drawdown"),
                                            (2000, 0, draw, None, None, "Borrowings increase")])
        elif cash > c(CASH_MAX):
            repay_debt = min(((cash - c(CASH_MAX)) // c(DEBT_STEP)) * c(DEBT_STEP),
                             led.credit_balance(2000))
            if repay_debt > 0:
                led.post(pk, "BORROWING_REPAY", [(2000, repay_debt, 0, None, None, "Facility repayment"),
                                                 (1000, 0, repay_debt, None, None, "Repaid")])

        drivers.append({
            "period": pk,
            "originations": orig / 100,
            "opening_loan_book": loans_open / 100,
            "repayments": repay / 100,
            "writeoffs": wo / 100,
            "closing_loan_book": loans_close / 100,
            "avg_loan_book": round(avg_book / 100, 2),
            "interest_income": int_inc / 100,
            "yield_annual": int_inc * 12 / avg_book,
            "fee_income": fees / 100,
            "fee_rate": fees / orig,
            "opening_borrowings": debt_open / 100,
            "interest_expense": int_exp / 100,
            "cost_of_funds_annual": int_exp * 12 / debt_open,
            "allowance_closing": allow_close / 100,
            "allowance_coverage": allow_close / loans_close,
            "provision": prov / 100,
            "chargeoff_rate_monthly": wo / loans_open,
            "headcount": headcount,
            "cost_per_head": round(salaries / headcount / 100, 2),
            "marketing_pct_of_originations": marketing / orig,
        })
    return led, pd.DataFrame(drivers)


def ledger_frame(led: Ledger) -> pd.DataFrame:
    df = pd.DataFrame(led.lines)
    df["posting_date"] = pd.PeriodIndex(df["period"], freq="M").to_timestamp(how="end").normalize()
    df["posting_date"] = df["posting_date"].dt.strftime("%Y-%m-%d")
    df["debit"] = df["debit_c"] / 100
    df["credit"] = df["credit_c"] / 100
    return df[["journal_id", "line_no", "posting_date", "period", "account_id", "debit", "credit",
               "cost_center_id", "vendor_id", "source", "memo"]]


def budget_frame(led: Ledger, pl_accounts: pd.DataFrame, periods: list[str],
                 extra_pairs: set[tuple]) -> pd.DataFrame:
    df = pd.DataFrame(led.lines)
    df = df[df["account_id"].isin(pl_accounts["account_id"])].copy()
    types = pl_accounts.set_index("account_id")["account_type"]
    sign = df["account_id"].map(types).map({"Revenue": -1, "Expense": 1})
    df["amount_c"] = (df["debit_c"] - df["credit_c"]) * sign
    df["cost_center_id"] = df["cost_center_id"].fillna("")
    agg = df.groupby(["period", "account_id", "cost_center_id"])["amount_c"].sum()
    pairs = {(a, cc) for _, a, cc in agg.index} | {(a, "" if pd.isna(cc) else cc) for a, cc in extra_pairs}
    grid = pd.MultiIndex.from_product([periods, sorted(pairs)], names=["period", "pair"])
    out = pd.DataFrame(index=grid).reset_index()
    out["account_id"] = out["pair"].str[0]
    out["cost_center_id"] = out["pair"].str[1]
    key = list(zip(out["period"], out["account_id"], out["cost_center_id"]))
    out["budget_amount"] = [agg.get(k, 0) / 100 for k in key]
    out["cost_center_id"] = out["cost_center_id"].replace("", pd.NA)
    return out[["period", "account_id", "cost_center_id", "budget_amount"]]


def generate(seed: int = SEED) -> dict[str, pd.DataFrame]:
    periods = list(pd.period_range(FIRST_MONTH, periods=N_MONTHS, freq="M"))
    period_keys = [str(x) for x in periods]
    accounts = pd.DataFrame(ACCOUNTS, columns=["account_id", "account_name", "account_type",
                                               "statement", "normal_balance", "report_group"])
    pl = accounts[accounts["statement"] == "PL"]
    vendors = make_vendors(seed)

    shock_rng = np.random.default_rng(seed)
    actual_led, actual_drv = simulate(Params(), actual_shocks(shock_rng, N_MONTHS), periods,
                                      vendors, np.random.default_rng(seed + 1))
    budget_led, budget_drv = simulate(Params(), neutral_shocks(N_MONTHS), periods,
                                      vendors, np.random.default_rng(seed + 2))

    actual_pairs = {(a, cc) for a, cc in zip(pd.DataFrame(actual_led.lines)["account_id"],
                                             pd.DataFrame(actual_led.lines)["cost_center_id"])
                    if a in set(pl["account_id"])}
    budget = budget_frame(budget_led, pl, period_keys, actual_pairs)

    actual_drv.insert(1, "scenario", "Actual")
    budget_drv.insert(1, "scenario", "Budget")

    all_periods = pd.period_range(OPENING_PERIOD, periods=N_MONTHS + 1, freq="M")
    dim_period = pd.DataFrame({
        "period": [str(x) for x in all_periods],
        "month_start": [x.start_time.strftime("%Y-%m-%d") for x in all_periods],
        "month_end": [x.end_time.strftime("%Y-%m-%d") for x in all_periods],
        "fiscal_year": [x.year for x in all_periods],
        "quarter": [f"Q{x.quarter}" for x in all_periods],
        "month_name": [x.strftime("%b") for x in all_periods],
        "is_opening": [str(x) == OPENING_PERIOD for x in all_periods],
    })
    return {
        "dim_account": accounts,
        "dim_cost_center": pd.DataFrame(COST_CENTERS, columns=["cost_center_id", "cost_center_name"]),
        "dim_vendor": vendors,
        "dim_period": dim_period,
        "fact_gl_journal": ledger_frame(actual_led),
        "fact_budget": budget,
        "fact_drivers": pd.concat([actual_drv, budget_drv], ignore_index=True),
    }


def write_tables(tables: dict[str, pd.DataFrame], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_csv(out_dir / f"{name}.csv", index=False, float_format="%.6g" if name == "fact_drivers" else "%.2f")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=root / "data" / "synthetic")
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()
    tables = generate(args.seed)
    write_tables(tables, args.out)
    for name, df in tables.items():
        print(f"{name:18s} {len(df):6d} rows  -> {args.out / (name + '.csv')}")


if __name__ == "__main__":
    main()
