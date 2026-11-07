"""The metric dictionary must agree with the data: identities hold and reference values match."""
import importlib
import re
from pathlib import Path

import numpy as np
import pytest

from finance_metrics import (balance_sheet, charge_off_rates, charge_off_reference_rows, pl_by_period, reference_rows,
                             volume_rate_effects)
from generate_ledgers import generate

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "metric_dictionary.md"


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
    assert rows == a + b + charge_off_reference_rows(t)


def test_every_metric_id_is_defined_once():
    ids = [ln.split("|")[1].strip() for ln in DOC.read_text(encoding="utf-8").splitlines()
           if ln.startswith("| M") and ln.split("|")[1].strip()[1:].isdigit()]
    assert len(ids) == len(set(ids)) and len(ids) >= 30


# ------------------------------------------------------------------ the finished dictionary is complete and consistent
def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _defined_ids() -> set[str]:
    return set(re.findall(r"^\| (M\d\d) \|", _doc(), re.M))


def _section(title: str) -> list[list[str]]:
    """Rows (cells) of the first table under the heading that starts with title."""
    text = _doc()
    body = text[text.index(title):].split("\n## ")[0]
    rows = [ln for ln in body.splitlines() if ln.startswith("|")][2:]
    return [[c.strip() for c in r.strip("|").split("|")] for r in rows]


def test_charge_off_rate_matches_the_drivers_column(t):
    d = t["fact_drivers"].query("scenario == 'Actual'")
    assert np.allclose(d.writeoffs / d.opening_loan_book, d.chargeoff_rate_monthly, atol=1e-5)
    c = charge_off_rates(t)
    assert (c.annualised > c.avg_monthly * 11).all() and (c.annualised < c.avg_monthly * 13).all()


def test_no_metric_is_left_as_draft():
    assert "| Draft" not in _doc() and "to be confirmed" not in _doc().lower()


def test_every_metric_id_used_in_the_project_is_defined():
    defined, cited = _defined_ids(), set()
    files = [*(ROOT / "src").glob("*.py"), *(ROOT / "sql").glob("*.sql"), *(ROOT / "dashboards").rglob("*.md"),
             *(ROOT / "dashboards").rglob("*.qvs"), ROOT / "README.md", ROOT / "docs" / "dax_measures.md"]
    for f in files:
        cited |= {"M" + m for m in re.findall(r"\bM(\d\d)\b", f.read_text(encoding="utf-8"))}
    assert cited - defined == set()


def _expand(spec: str) -> set[str]:
    ids = set()
    for part in re.findall(r"M\d\d(?:-M\d\d)?", spec):
        a, _, b = part.partition("-")
        ids |= {f"M{n:02d}" for n in range(int(a[1:]), int((b or a)[1:]) + 1)}
    return ids


def test_every_metric_appears_in_the_implementation_index():
    indexed = set().union(*(_expand(r[0]) for r in _section("## 8. Where each metric")))
    assert _defined_ids() <= indexed


def test_names_in_the_implementation_index_exist():
    import export_for_qlik
    import export_risk_for_qlik
    qlik = {m[0] for m in export_for_qlik.MEASURES} | {m[0] for m in export_risk_for_qlik.MEASURES}
    dax = (ROOT / "docs" / "dax_measures.md").read_text(encoding="utf-8")
    sql = " ".join(f.read_text(encoding="utf-8") for f in (ROOT / "sql").glob("*.sql"))
    missing = []
    for row in _section("## 8. Where each metric"):
        names = [re.findall(r"`([^`]+)`", cell) for cell in row[1:5]]
        missing += [f"SQL {n}" for n in names[0] if f"CREATE OR REPLACE VIEW {n}" not in sql]
        for n in names[1]:
            mod, _, fn = n.partition(".")
            if not hasattr(importlib.import_module(mod), fn):
                missing.append(f"Python {n}")
        missing += [f"DAX {n}" for n in names[2] if f"| {n} |" not in dax]
        missing += [f"Qlik {n}" for n in names[3] if n not in qlik]
    assert missing == []


def test_scorecard_reference_values_match_the_scorecard_run_when_present():
    metrics, years = ROOT / "docs" / "scorecard_metrics.csv", ROOT / "docs" / "psi_by_year.csv"
    if not (metrics.exists() and years.exists()):
        pytest.skip("scorecard outputs not present (run the scorecard first)")
    import pandas as pd
    m = pd.read_csv(metrics).set_index("set").loc["Scorecard, test"]
    y = pd.read_csv(years).set_index("issue_year")
    ref = {r[0]: r[1] for r in _section("## 7. Scorecard reference values")}
    assert float(ref["AUC (M52)"]) == pytest.approx(m.AUC, abs=0.0006)
    assert float(ref["Gini (M53)"]) == pytest.approx(m.Gini, abs=0.0006)
    assert float(ref["KS (M54)"]) == pytest.approx(m.KS, abs=0.0006)
    assert float(ref["Score PSI, test vs train (M56)"]) == pytest.approx(y.loc[2015, "score_psi"], abs=0.0006)
    text = _doc()
    for yr in (2013, 2014, 2015):
        row = re.search(rf"^\| {yr} \([a-z]+\) \| ([\d.]+)% \| ([\d.]+)% \| ([+-][\d.]+) points", text, re.M)
        assert float(row.group(1)) == pytest.approx(y.loc[yr, "default_rate"] * 100, abs=0.006)
        assert float(row.group(2)) == pytest.approx(y.loc[yr, "avg_predicted_pd"] * 100, abs=0.006)
        assert float(row.group(3)) == pytest.approx((y.loc[yr, "default_rate"] - y.loc[yr, "avg_predicted_pd"]) * 100, abs=0.011)
