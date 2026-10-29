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

from finance_metrics import annual_summary, balance_sheet, pl_by_period
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
        df = pd.DataFrame(cur.fetchall(), columns=cols)
    for c in df.columns:  # some database encodings (SQL_ASCII) return text as bytes
        if df[c].dtype == object:
            df[c] = df[c].map(lambda v: v.decode("utf-8") if isinstance(v, bytes) else v)
    return df


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


def test_balance_sheet_monthly_matches_reference(conn, tables):
    sql = query(conn, "SELECT * FROM fin.v_balance_sheet_monthly ORDER BY period").set_index("period")
    ref = balance_sheet(tables)
    assert len(sql) == 37 and sql.is_opening.sum() == 1
    for col, ref_col in [("cash", "cash"), ("gross_loans", "gross_loans"), ("allowance", "allowance"),
                         ("net_loans", "net_loans"), ("total_assets", "total_assets"),
                         ("borrowings", "borrowings"), ("total_liabilities", "total_liabilities"),
                         ("total_equity", "equity")]:
        assert np.allclose(sql[col].astype(float), ref[ref_col], atol=0.01), col
    assert np.allclose(sql.allowance_coverage.astype(float), ref.allowance_coverage, atol=1e-5)
    assert np.allclose(sql.debt_to_equity.astype(float), ref.debt_to_equity, atol=1e-3)


def test_balance_sheet_balances_every_month_in_sql(conn):
    bad = query(conn, "SELECT period FROM fin.v_balance_sheet_monthly WHERE ABS(balance_check) > 0.005")
    assert bad.empty


def test_year_end_view_and_roe_match_reference(conn, tables):
    sql = query(conn, "SELECT * FROM fin.v_balance_sheet_year_end WHERE fiscal_year >= 2023 ORDER BY fiscal_year")
    ref = annual_summary(tables)
    assert list(sql.period) == ["2023-12", "2024-12", "2025-12"]
    assert np.allclose(sql.total_equity.astype(float), ref.equity_ye, atol=0.01)
    assert np.allclose(sql.return_on_equity.astype(float), ref.roe, atol=1e-5)


def test_statement_lines_add_up(conn):
    df = query(conn, "SELECT period, line_name, line_type, amount FROM fin.v_balance_sheet_statement")
    df["amount"] = df.amount.astype(float)
    assets = df[df.line_type == "asset"].groupby("period").amount.sum()
    total = df[df.line_name == "Total Assets"].set_index("period").amount
    tle = df[df.line_name == "Total Liabilities and Equity"].set_index("period").amount
    assert np.allclose(assets, total.reindex(assets.index), atol=0.01)
    assert np.allclose(total, tle.reindex(total.index), atol=0.01)
