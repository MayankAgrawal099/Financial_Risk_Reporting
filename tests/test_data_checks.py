"""Runs the data checks in src/run_data_checks.py as tests.

Ledger checks need no database. SQL and Lending Club checks run when FIN_PG_DSN is set.
"""
import os

import pytest

from run_data_checks import Checks, ledger_checks, load_tables, run, write_reports


@pytest.fixture(scope="module")
def tables():
    return load_tables()


def test_all_ledger_checks_pass(tables):
    c = Checks()
    ledger_checks(c, tables)
    failed = [(r.id, r.actual) for r in c.results if r.status != "PASS"]
    assert not failed, failed
    assert len(c.results) >= 17


def test_checks_can_fail_when_the_ledger_is_broken(tables):
    """A check that cannot fail proves nothing: corrupt one ledger line and expect failures."""
    broken = {k: v.copy() for k, v in tables.items()}
    gl = broken["fact_gl_journal"]
    i = gl.index[gl["debit"] > 0][0]
    gl.loc[i, "debit"] += 100.0
    c = Checks()
    ledger_checks(c, broken)
    failed = {r.id for r in c.results if r.status == "FAIL"}
    assert {"L01", "L02", "L09"} <= failed


def test_nan_never_passes():
    c = Checks()
    c.number("X", "t", "nan must fail", float("nan"))
    assert c.results[0].status == "FAIL"


def test_reports_are_written(tmp_path):
    results = run(None)
    write_reports(results, tmp_path)
    assert (tmp_path / "data_checks.csv").exists()
    text = (tmp_path / "data_checks.md").read_text(encoding="utf-8")
    assert "| L01 |" in text and "SKIPPED" in text


@pytest.mark.skipif(not os.environ.get("FIN_PG_DSN"), reason="set FIN_PG_DSN to run the SQL checks")
def test_sql_checks_pass_on_the_database():
    results = run(os.environ["FIN_PG_DSN"])
    assert not [r for r in results if r.status == "FAIL"], [(r.id, r.actual) for r in results if r.status == "FAIL"]
    sql = [r for r in results if r.id.startswith("S")]
    assert len(sql) == 11 and all(r.status == "PASS" for r in sql)
