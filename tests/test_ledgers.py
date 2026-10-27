"""Integrity checks for the synthetic ledgers (run: pytest)."""
import pandas as pd
import pytest

from generate_ledgers import generate


@pytest.fixture(scope="module")
def t():
    return generate(42)


@pytest.fixture(scope="module")
def gl(t):
    return t["fact_gl_journal"].merge(t["dim_account"], on="account_id")


def test_every_journal_balances(t):
    g = t["fact_gl_journal"].assign(d=lambda x: (x.debit * 100).round().astype("int64"),
                                    c=lambda x: (x.credit * 100).round().astype("int64"))
    by = g.groupby("journal_id")[["d", "c"]].sum()
    assert (by.d == by.c).all()


def test_trial_balance_is_zero(t):
    g = t["fact_gl_journal"]
    assert round(g.debit.sum() - g.credit.sum(), 2) == 0


def test_balance_sheet_balances_every_month(gl):
    gl = gl.assign(net=gl.debit - gl.credit)
    bs = gl[gl.statement == "BS"].groupby(["period", "account_id"]).net.sum().unstack().fillna(0).cumsum()
    ni = -gl[gl.statement == "PL"].groupby("period").net.sum().reindex(bs.index).fillna(0).cumsum()
    assert ((bs.sum(axis=1) + (-ni)).abs() < 0.01).all()  # assets - liabilities - equity - cumulative NI = 0


def test_loan_roll_forward(t):
    d = t["fact_drivers"].query("scenario == 'Actual'")
    calc = d.opening_loan_book + d.originations - d.repayments - d.writeoffs
    assert ((calc - d.closing_loan_book).abs() < 0.01).all()
    assert (d.closing_loan_book.shift(1).dropna().values - d.opening_loan_book.iloc[1:].values < 0.01).all()


def test_cash_and_debt_stay_sensible(gl):
    gl = gl.assign(net=gl.debit - gl.credit)
    cash = gl[gl.account_id == 1000].groupby("period").net.sum().cumsum()
    debt = -gl[gl.account_id == 2000].groupby("period").net.sum().cumsum()
    assert cash.min() >= 1_900_000 and cash.max() <= 4_600_000
    assert (debt >= 0).all()


def test_pl_ties_to_drivers(t, gl):
    d = t["fact_drivers"].query("scenario == 'Actual'").set_index("period")
    pl = gl[gl.statement == "PL"].assign(net=lambda x: x.credit - x.debit)
    ii = pl[pl.account_id == 4000].groupby("period").net.sum()
    assert ((ii - d.interest_income).abs() < 0.01).all()
    prov = -pl[pl.account_id == 5100].groupby("period").net.sum()
    assert ((prov - d.provision).abs() < 0.01).all()


def test_budget_covers_every_period_and_matches_budget_drivers(t):
    b = t["fact_budget"]
    assert b.period.nunique() == 36
    d = t["fact_drivers"].query("scenario == 'Budget'").set_index("period")
    ii = b[b.account_id == 4000].groupby("period").budget_amount.sum()
    assert ((ii - d.interest_income).abs() < 0.01).all()


def test_actual_differs_from_budget_where_events_were_injected(t):
    d = t["fact_drivers"].pivot(index="period", columns="scenario", values="chargeoff_rate_monthly")
    stress = d.loc["2025-04":"2025-09"]
    assert (stress.Actual.mean() / stress.Budget.mean()) > 1.2


def test_foreign_keys_and_ranges(t):
    g = t["fact_gl_journal"]
    assert set(g.account_id) <= set(t["dim_account"].account_id)
    assert set(g.cost_center_id.dropna()) <= set(t["dim_cost_center"].cost_center_id)
    assert set(g.vendor_id.dropna()) <= set(t["dim_vendor"].vendor_id)
    assert set(g.period) <= set(t["dim_period"].period)
    a = t["fact_drivers"].query("scenario == 'Actual'")
    assert a.allowance_coverage.between(0.03, 0.07).all()
    assert a.yield_annual.between(0.12, 0.18).all()


def test_deterministic_for_same_seed():
    a, b = generate(7), generate(7)
    pd.testing.assert_frame_equal(a["fact_gl_journal"], b["fact_gl_journal"])
    pd.testing.assert_frame_equal(a["fact_drivers"], b["fact_drivers"])
