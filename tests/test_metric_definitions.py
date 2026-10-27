"""The metric dictionary must agree with the data: identities hold and reference values match."""
from pathlib import Path

import numpy as np
import pytest

from finance_metrics import balance_sheet, pl_by_period, reference_rows, volume_rate_effects
from generate_ledgers import generate

DOC = Path(__file__).resolve().parents[1] / "docs" / "metric_dictionary.md"


@pytest.fixture(scope="module")
def t():
    return generate(42)


def test_net_income_identity(t):
    gl = t["fact_gl_journal"].merge(t["dim_account"], on="account_id")
    pl = gl[gl.statement == "PL"]
    ni_ledger = (pl.credit - pl.debit).groupby(pl.period).sum()
    m = pl_by_period(t)
    assert np.allclose(m.net_income, ni_ledger.reindex(m.index), atol=0.01)


def test_net_revenue_two_ways(t):
    m = pl_by_period(t)
    assert np.allclose(m.net_revenue, m.total_revenue - m.interest_expense, atol=0.01)


def test_balance_sheet_balances_every_month(t):
    bs = balance_sheet(t)
    assert (bs.total_assets - bs.total_liabilities - bs.equity).abs().max() < 0.01


def test_allowance_coverage_matches_drivers(t):
    bs = balance_sheet(t)
    drv = t["fact_drivers"].query("scenario == 'Actual'").set_index("period")
    assert np.allclose(bs.loc[drv.index, "allowance_coverage"], drv.allowance_coverage, atol=1e-6)


@pytest.mark.parametrize("budget_account,volume,rate,scale", [
    (4000, "avg_loan_book", "yield_annual", 1 / 12),
    (4100, "originations", "fee_rate", 1),
    (5000, "opening_borrowings", "cost_of_funds_annual", 1 / 12),
])
def test_volume_plus_rate_equals_total_variance(t, budget_account, volume, rate, scale):
    drv = t["fact_drivers"]
    a = drv.query("scenario == 'Actual'").set_index("period")
    b = drv.query("scenario == 'Budget'").set_index("period")
    vol, rt = volume_rate_effects(a[volume], a[rate] * scale, b[volume], b[rate] * scale)
    gl = t["fact_gl_journal"]
    g = gl[gl.account_id == budget_account]
    actual = (g.credit - g.debit if budget_account != 5000 else g.debit - g.credit).groupby(g.period).sum()
    fb = t["fact_budget"].query("account_id == @budget_account").groupby("period").budget_amount.sum()
    assert np.allclose(vol + rt, actual.reindex(a.index) - fb.reindex(a.index), atol=0.05)


def test_reference_values_in_doc_match_data(t):
    text = DOC.read_text(encoding="utf-8").splitlines()
    rows = [ln for ln in text if ln.startswith("| FY20")]
    a, b = reference_rows(t)
    assert rows == a + b


def test_every_metric_id_is_defined_once():
    ids = [ln.split("|")[1].strip() for ln in DOC.read_text(encoding="utf-8").splitlines()
           if ln.startswith("| M") and ln.split("|")[1].strip()[1:].isdigit()]
    assert len(ids) == len(set(ids)) and len(ids) >= 30
