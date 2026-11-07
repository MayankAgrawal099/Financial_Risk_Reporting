"""Tests for recording scorecard accuracy numbers in the test results file."""
import pytest

from record_test_results import END, START, read_text_any, run, splice
from test_scorecard_stability import build


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("rec")
    r = build(tmp)
    r["tmp"] = tmp
    return r


def test_splice_appends_then_replaces():
    a = splice("pytest output\n10 passed\n", f"{START}\nv1\n{END}")
    assert a.startswith("pytest output") and "v1" in a
    b = splice(a, f"{START}\nv2\n{END}")
    assert "v2" in b and "v1" not in b and b.count(START) == 1 and "10 passed" in b


def test_record_keeps_pytest_output_and_is_repeatable(built):
    t = built["tmp"]
    (t / "reports").mkdir()
    (t / "reports" / "test_results.txt").write_text("collected 5 items\n5 passed in 1.0s\n", encoding="utf-8")
    run(t / "docs", t / "models", t / "reports", today="2026-11-04")
    run(t / "docs", t / "models", t / "reports", today="2026-11-04")
    text = (t / "reports" / "test_results.txt").read_text(encoding="utf-8")
    assert text.startswith("collected 5 items") and "5 passed in 1.0s" in text
    assert text.count(START) == 1 and text.count(END) == 1


def test_numbers_match_source_files(built):
    t = built["tmp"]
    text = run(t / "docs", t / "models", t / "reports", today="2026-11-04")
    s = built["summary"]
    assert f"{s['score_psi_test']:.3f}" in text
    assert f"{s['actual_test']:.2%}" in text and f"{s['pred_test']:.2%}" in text
    assert "Scorecard, train" in text and "Scorecard, test" in text
    assert "bands rise steadily from A to E: yes" in text
    assert (t / "reports" / "scorecard_accuracy.md").exists()


def test_reads_utf16_from_powershell_redirect(built):
    t = built["tmp"]
    f = t / "reports" / "test_results.txt"
    f.write_bytes("collected 7 items\n7 passed in 2.0s\n".encode("utf-16"))
    assert "7 passed" in read_text_any(f)
    run(t / "docs", t / "models", t / "reports", today="2026-11-04")
    text = f.read_text(encoding="utf-8")
    assert text.startswith("collected 7 items") and text.count(START) == 1


# ------------------------------------------------------------------ results summary (data checks + model metrics)
JUNIT = """<?xml version="1.0"?><testsuites><testsuite name="pytest" tests="5" time="12.5">
<testcase classname="tests.test_ledgers" name="test_a"/><testcase classname="tests.test_ledgers" name="test_b"/>
<testcase classname="tests.test_sql_views" name="test_c"><skipped message="no database"/></testcase>
<testcase classname="tests.test_variance.TestX" name="test_d"/><testcase classname="tests.test_variance" name="test_e"><failure message="x"/></testcase>
</testsuite></testsuites>"""
CHECKS = ("id,area,description,expected,actual,status\nL01,Ledger,Debits equal credits,0.00,0.00,PASS\n"
          "S01,SQL,P&L view matches | the ledger,0.00,0.00,PASS\nR01,Lending Club,Row count,180000,empty table,SKIPPED\n")


def _fake_run(built, tmp_path, junit=JUNIT, checks=CHECKS):
    t = built["tmp"]
    rep = tmp_path / "reports"
    rep.mkdir()
    if junit is not None:
        (rep / "pytest_results.xml").write_text(junit, encoding="utf-8")
    if checks is not None:
        (rep / "data_checks.csv").write_text(checks, encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_ledgers.py").write_text('"""Integrity checks for the ledgers."""\n', encoding="utf-8")
    run(t / "docs", t / "models", rep, today="2026-11-08", tests_dir=tests)
    return (rep / "test_results_summary.md").read_text(encoding="utf-8")


def test_summary_per_file_counts_and_not_final_verdict(built, tmp_path):
    s = _fake_run(built, tmp_path)
    assert "**Result: not final.**" in s and "1 test(s) FAILED" in s and "1 test(s) skipped" in s
    assert "1 data check(s) skipped (R01)" in s
    assert "**3 passed, 1 failed, 1 skipped** in 12 seconds, across 3 test files." in s
    assert "| `test_ledgers.py` | Integrity checks for the ledgers | 2 | 0 | 0 |" in s
    assert "| `test_variance.py` | Variance | 1 | 1 | 0 |" in s          # grouped by file even when a class is involved
    assert "| `test_sql_views.py` | Sql views | 0 | 0 | 1 |" in s


def test_summary_lists_every_check_and_escapes_pipes(built, tmp_path):
    s = _fake_run(built, tmp_path)
    assert "**2 passed, 0 failed, 1 skipped** of 3." in s
    for cid in ("L01", "S01", "R01"):
        assert f"| {cid} |" in s
    assert "P&L view matches \\| the ledger" in s and "| R01 | Lending Club | Row count | 180000 | empty table | SKIPPED |" in s


def test_summary_model_numbers_match_the_source_files(built, tmp_path):
    s = _fake_run(built, tmp_path)
    x = built["summary"]
    assert f"**{x['score_psi_test']:.3f}**" in s
    assert f"actual {x['actual_test']:.2%}" in s and f"predicted default rate {x['pred_test']:.2%}" in s
    assert "| Scorecard, test |" in s and "| Band | Score range |" in s
    assert "from band A to band E at every step: **yes**" in s


def test_summary_is_final_only_when_nothing_failed_or_skipped(built, tmp_path):
    good = JUNIT.replace('<skipped message="no database"/>', "").replace('<failure message="x"/>', "")
    s = _fake_run(built, tmp_path, junit=good, checks=CHECKS.replace("SKIPPED", "PASS"))
    assert "**Result: final.** All 5 tests passed and all 3 data checks passed, with nothing skipped." in s


def test_summary_says_so_when_inputs_are_missing(built, tmp_path):
    s = _fake_run(built, tmp_path, junit=None, checks=None)
    assert "not final" in s and "pytest results were not recorded" in s and "data checks were not recorded" in s
    assert "Not recorded." in s
