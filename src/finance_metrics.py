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
                 f"{r.debt_to_equity_ye:.2f}x |")
    return a, b
