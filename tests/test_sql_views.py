"""Checks the P&L views against the Python reference implementation.

Needs a loaded database. Skipped unless FIN_PG_DSN is set, for example:
    set FIN_PG_DSN=postgresql://postgres:PASSWORD@localhost:5432/fin_risk
    python src/load_to_postgres.py
    python -m pytest tests/test_sql_views.py -v
"""
import os

import numpy as np
import pandas as pd
import pytest

from finance_metrics import annual_summary, pl_by_period
from generate_ledgers import generate

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("FIN_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="set FIN_PG_DSN to test the SQL views")


@pytest.fixture(scope="module")
def conn():
    with psycopg.connect(DSN) as c:
        yield c


def query(conn, sql):
    with conn.cursor() as cur:
        cur.execute(sql)
        cols = [d.name for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


@pytest.fixture(scope="module")
def tables():
    return generate(42)


def test_row_counts_match_the_csv_files(conn, tables):
    for name, df in tables.items():
        n = query(conn, f"SELECT COUNT(*) AS n FROM fin.{name}").n[0]
        assert n == len(df), name


def test_monthly_view_matches_reference(conn, tables):
    sql = query(conn, "SELECT * FROM fin.v_pl_monthly ORDER BY period").set_index("period")
    ref = pl_by_period(tables)
    assert len(sql) == 36
    for col, ref_col in [("interest_income", "interest_income"), ("fee_income", "fee_income"),
                         ("total_revenue", "total_revenue"), ("interest_expense", "interest_expense"),
                         ("net_interest_income", "nii"), ("net_revenue", "net_revenue"),
                         ("provision", "provision"), ("operating_expenses", "opex"),
                         ("pre_tax_profit", "pbt"), ("income_tax", "tax"), ("net_income", "net_income")]:
        assert np.allclose(sql[col].astype(float), ref[ref_col], atol=0.01), col


def test_annual_view_matches_reference(conn, tables):
    sql = query(conn, "SELECT * FROM fin.v_pl_annual ORDER BY fiscal_year").set_index("fiscal_year")
    ref = annual_summary(tables)
    ref.index = ref.index.astype(int)
    assert (sql.months == 12).all()
    for col, ref_col in [("total_revenue", "total_revenue"), ("net_income", "net_income"),
                         ("net_interest_income", "nii"), ("operating_expenses", "opex")]:
        assert np.allclose(sql[col].astype(float), ref[ref_col], atol=0.01), col
    assert np.allclose(sql.cost_to_income_ratio.astype(float), ref.cost_to_income, atol=1e-5)
    assert np.allclose(sql.net_interest_margin.astype(float), ref.nim, atol=1e-5)
    assert np.allclose(sql.yield_on_loans.astype(float), ref.yield_on_loans, atol=1e-5)


def test_income_statement_net_income_equals_monthly_view(conn):
    a = query(conn, "SELECT period, amount FROM fin.v_income_statement WHERE line_name = 'Net Income' ORDER BY 1")
    b = query(conn, "SELECT period, net_income FROM fin.v_pl_monthly ORDER BY 1")
    assert np.allclose(a.amount.astype(float), b.net_income.astype(float), atol=0.001)


def test_quarters_add_up_to_years(conn):
    q = query(conn, "SELECT fiscal_year, SUM(net_income) AS ni FROM fin.v_pl_quarterly GROUP BY 1 ORDER BY 1")
    y = query(conn, "SELECT fiscal_year, net_income AS ni FROM fin.v_pl_annual ORDER BY 1")
    assert np.allclose(q.ni.astype(float), y.ni.astype(float), atol=0.01)
