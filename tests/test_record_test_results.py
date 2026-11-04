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
