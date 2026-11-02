"""Reference implementation of the finance metrics defined in docs/metric_dictionary.md.

Computes P&L and balance sheet metrics straight from the general ledger tables. Use it to
cross-check the SQL views and the dashboards: the numbers must match.

    from generate_ledgers import generate
    from finance_metrics import pl_by_period, balance_sheet, annual_summary
"""

from __future__ import annotations

import pandas as pd

OPEX_ACCOUNTS = [6000, 6100, 6200, 6300, 6400, 6500]


def _net(gl: pd.DataFrame, accounts, sign: int) -> pd.Series:
    """Sum of sign*(debit - credit) per period for the given accounts."""
    g = gl[gl["account_id"].isin(accounts)]
    return (sign * (g["debit"] - g["credit"])).groupby(g["period"]).sum()


def pl_by_period(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    gl = tables["fact_gl_journal"]
    periods = sorted(p for p in gl["period"].unique() if p != "2022-12")
    df = pd.DataFrame(index=periods)
    df["interest_income"] = -_net(gl, [4000], 1)
    df["fee_income"] = -_net(gl, [4100], 1)
    df["interest_expense"] = _net(gl, [5000], 1)
    df["provision"] = _net(gl, [5100], 1)
    df["opex"] = _net(gl, OPEX_ACCOUNTS, 1)
    df["tax"] = _net(gl, [7000], 1)
    df = df.fillna(0.0)
    df["total_revenue"] = df.interest_income + df.fee_income                 # M03
    df["nii"] = df.interest_income - df.interest_expense                      # M05
    df["net_revenue"] = df.nii + df.fee_income                                # M06
    df["pbt"] = df.net_revenue - df.provision - df.opex                       # M09
    df["net_income"] = df.pbt - df.tax                                        # M11
    return df.round(2)


def balance_sheet(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Month-end balances (cumulative), including the opening position at 2022-12."""
    gl = tables["fact_gl_journal"]
    net = (gl["debit"] - gl["credit"]).groupby([gl["period"], gl["account_id"]]).sum().unstack().fillna(0)
    bal = net.cumsum()
    pl = pl_by_period(tables)
    cum_ni = pl["net_income"].reindex(bal.index).fillna(0).cumsum()
    out = pd.DataFrame(index=bal.index)
    out["cash"] = bal[1000]
    out["gross_loans"] = bal[1100]                                            # M20
    out["allowance"] = -bal[1110]                                             # M21
    out["net_loans"] = out.gross_loans - out.allowance                        # M22
    out["allowance_coverage"] = out.allowance / out.gross_loans               # M23
    out["total_assets"] = bal[[1000, 1100, 1110, 1200, 1300]].sum(axis=1)     # M24
    out["borrowings"] = -bal[2000]
    out["total_liabilities"] = -bal[[2000, 2100, 2200]].sum(axis=1)           # M25
    out["equity"] = -bal[3000] - bal[3100] + cum_ni                           # M26
    out["debt_to_equity"] = out.borrowings / out.equity                       # M27
    return out.round(6)


def annual_summary(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    pl, bs = pl_by_period(tables), balance_sheet(tables)
    drv = tables["fact_drivers"].query("scenario == 'Actual'").set_index("period")
    pl = pl.assign(fy=pl.index.str[:4])
    fy = pl.groupby("fy")[["interest_income", "fee_income", "total_revenue", "interest_expense", "nii",
                           "net_revenue", "provision", "opex", "pbt", "tax", "net_income"]].sum()
    fy["cost_to_income"] = fy.opex / fy.net_revenue                           # M13
    fy["net_profit_margin"] = fy.net_income / fy.total_revenue                # M12
    avg_book = drv["avg_loan_book"].groupby(drv.index.str[:4]).mean()
    fy["nim"] = fy.nii / avg_book                                             # M16 (12 months)
    fy["yield_on_loans"] = fy.interest_income / avg_book                      # M14
    year_end = bs.loc[[f"{y}-12" for y in fy.index]]
    fy["gross_loans_ye"] = year_end["gross_loans"].values
    fy["borrowings_ye"] = year_end["borrowings"].values
    fy["equity_ye"] = year_end["equity"].values
    fy["debt_to_equity_ye"] = year_end["debt_to_equity"].values
    eq = bs["equity"][bs.index != "2022-12"]                                  # month-end equity, opening excluded
    fy["roe"] = fy.net_income / eq.groupby(eq.index.str[:4]).mean()           # M28 (12 months)
    return fy


def volume_rate_effects(actual_volume, actual_rate, budget_volume, budget_rate):
    """M32 and M33: price-volume split of a variance (works on numbers or pandas Series)."""
    volume = (actual_volume - budget_volume) * budget_rate
    rate = (actual_rate - budget_rate) * actual_volume
    return volume, rate


def reference_rows(tables: dict[str, pd.DataFrame]) -> tuple[list[str], list[str]]:
    """Markdown table rows for the reference values in docs/metric_dictionary.md."""
    fy = annual_summary(tables)
    usd = lambda v: f"{v:,.0f}"      # noqa: E731
    pct = lambda v: f"{v * 100:.1f}%"  # noqa: E731
    a, b = [], []
    for y, r in fy.iterrows():
        a.append(f"| FY{y} | {usd(r.total_revenue)} | {usd(r.interest_expense)} | {usd(r.nii)} | "
                 f"{usd(r.net_revenue)} | {usd(r.provision)} | {usd(r.opex)} | {usd(r.pbt)} | "
                 f"{usd(r.tax)} | {usd(r.net_income)} |")
        b.append(f"| FY{y} | {pct(r.cost_to_income)} | {pct(r.net_profit_margin)} | {pct(r.yield_on_loans)} | "
                 f"{pct(r.nim)} | {usd(r.gross_loans_ye)} | {usd(r.borrowings_ye)} | {usd(r.equity_ye)} | "
                 f"{r.debt_to_equity_ye:.2f}x | {pct(r.roe)} |")
    return a, b


# ---------------------------------------------------------------- budget vs actual (M30-M33)
DRIVER_LINES = {   # account_id: (label, volume column, sign: +1 revenue / -1 cost)
    4000: ("Interest Income", "avg_loan_book", 1),
    4100: ("Fee Income", "originations", 1),
    5000: ("Interest Expense", "opening_borrowings", -1),
    5100: ("Provision for Credit Losses", "opening_loan_book", -1),
    6000: ("Salaries and Benefits", "headcount", -1),
    6100: ("Marketing", "originations", -1),
}


def bva_account_month(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Actual, budget and favourable-positive variance (M30) by period and P&L account."""
    gl = tables["fact_gl_journal"]
    gl = gl[gl["account_id"] >= 4000]
    sign = (gl["account_id"] < 5000).map({True: 1, False: -1})
    actual = (sign * (gl["credit"] - gl["debit"])).groupby([gl["period"], gl["account_id"]]).sum().rename("actual")
    bu = tables["fact_budget"]
    budget = bu.groupby(["period", "account_id"])["budget_amount"].sum().rename("budget")
    df = pd.concat([actual, budget], axis=1).fillna(0.0).reset_index()
    s = (df["account_id"] < 5000).map({True: 1, False: -1})
    df["variance"] = s * (df["actual"] - df["budget"])
    return df


def variance_drivers(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Monthly volume effect (M32), rate effect (M33) and other effect per driver line, favourable positive."""
    df = bva_account_month(tables)
    drv = tables["fact_drivers"].set_index(["period", "scenario"])
    rows = []
    for r in df.itertuples():
        if r.account_id not in DRIVER_LINES:
            rows.append((r.period, r.account_id, r.variance, None, None, round(r.variance, 2)))
            continue
        _, col, sgn = DRIVER_LINES[r.account_id]
        va, vb = drv.loc[(r.period, "Actual"), col], drv.loc[(r.period, "Budget"), col]
        rate_b = r.budget / vb
        vol = round(sgn * (va - vb) * rate_b, 2)
        rows.append((r.period, r.account_id, r.variance, vol, round(r.variance - vol, 2), 0.0))
    return pd.DataFrame(rows, columns=["period", "account_id", "variance", "volume_effect", "rate_effect", "other_effect"])


def bva_net_income(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Annual net income actual, budget and variance; and total of all line variances (must equal it)."""
    df = bva_account_month(tables)
    s = (df["account_id"] < 5000).map({True: 1, False: -1})
    df["fy"] = df["period"].str[:4]
    df["a"], df["b"] = s * df["actual"], s * df["budget"]
    g = df.groupby("fy")[["a", "b", "variance"]].sum()
    g.columns = ["net_income_actual", "net_income_budget", "sum_of_line_variances"]
    g["net_income_variance"] = g["net_income_actual"] - g["net_income_budget"]
    return g
