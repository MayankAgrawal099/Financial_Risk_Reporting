"""Tests for the board report PDF: numbers tie to the extracts, pages are present, risk page is optional."""
import shutil

import pandas as pd
import pytest
from pypdf import PdfReader

from build_board_report import (DATA_DIR, board_facts, build_report, check_summary, headlines, latest_month, line_table, load_extracts,
                                metric_definitions, month_label, signed, usd, commentary, management_comment)
from export_for_qlik import WATERFALL_LINES
from export_risk_for_qlik import export as risk_export
from test_scorecard_stability import build


FINANCE_FILES = ("bva_lines.csv", "bva_accounts.csv", "variance_drivers.csv", "balance_sheet.csv")


@pytest.fixture(scope="module")
def finance(tmp_path_factory):
    """Only the finance extracts, so the tests do not change when the risk extracts are present in the repo."""
    d = tmp_path_factory.mktemp("finance_only")
    for f in FINANCE_FILES:
        shutil.copy(DATA_DIR / f, d / f)
    return d


@pytest.fixture(scope="module")
def ex(finance):
    return load_extracts(finance)


@pytest.fixture(scope="module")
def with_risk(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("board_risk")
    build(tmp)
    d = tmp / "extracts"
    d.mkdir()
    for f in FINANCE_FILES:
        shutil.copy(DATA_DIR / f, d / f)
    risk_export(tmp / "data", tmp / "docs", tmp / "models", d)
    return d


def text(path):
    return "\n".join(p.extract_text() for p in PdfReader(path).pages)


# ------------------------------------------------------------------ formatting and data
def test_formatting_helpers():
    assert usd(1962349.4) == "$1,962,349" and usd(-1618506) == "-$1,618,506"
    assert signed(21588) == "+$21,588" and signed(-324082) == "-$324,082"
    assert month_label("2025-12") == "December 2025"


def test_latest_month_and_unknown_month(ex):
    assert latest_month(ex) == "2025-12"
    with pytest.raises(ValueError, match="No data for month 2030-01"):
        line_table(ex, "2030-01")


def test_fy2025_facts_match_the_dashboard(ex):
    f = board_facts(ex, "2025-12")
    assert round(f["ni_ytd"]["actual"], 2) == 1962349.09 and round(f["ni_ytd"]["budget"], 2) == 3580855.40
    assert f["ni_ytd"]["pct"] == pytest.approx(-0.452, abs=0.001)
    assert f["worst"][0] == "Provision for Credit Losses" and round(f["worst"][1], 2) == -1636914.02
    assert f["best"][0] == "Income Tax" and round(f["best"][1]) == 539502
    assert f["months_ytd"] == 12 and f["fy"] == 2025
    assert f["driver"]["account"] == "Provision for Credit Losses" and abs(f["driver"]["rate_effect"]) > abs(f["driver"]["volume_effect"])


def test_ytd_is_cumulative_and_month_is_one_month(ex):
    jun = board_facts(ex, "2025-06")
    lines = ex["bva_lines"]
    ni = lines[(lines.line_name == "Net Income")]
    assert jun["ni"]["actual"] == pytest.approx(ni[ni.period == "2025-06"].actual.iloc[0])
    assert jun["ni_ytd"]["actual"] == pytest.approx(ni[(ni.fiscal_year == 2025) & (ni.period <= "2025-06")].actual.sum())
    assert jun["months_ytd"] == 6


def test_contributors_bridge_budget_to_actual(ex):
    f = board_facts(ex, "2025-09")
    t = f["table"].set_index("line_name")
    bridge = t.loc["Net Income", "y_budget"] + sum(t.loc[n, "y_variance"] for n in WATERFALL_LINES)
    assert bridge == pytest.approx(t.loc["Net Income", "y_actual"], abs=0.1)


def test_balance_facts_balance(ex):
    for m in ("2023-06", "2024-12", "2025-12"):
        b = board_facts(ex, m)["bs"]
        assert abs(b["check"]) < 0.005
    assert round(board_facts(ex, "2025-12")["bs"]["total_assets"]) == 129229642


def test_headlines_state_the_numbers(ex):
    h = " ".join(headlines(board_facts(ex, "2025-12")))
    for s in ("$1,962,349", "$3,580,855", "-$1,618,506", "Provision for Credit Losses", "+$539,502", "$129,229,642", "3.67x"):
        assert s in h, s
    assert "Credit scorecard" not in h          # no risk extracts in the repo's finance data folder


def test_metric_definitions_and_check_summary(tmp_path):
    d = metric_definitions(DATA_DIR.parents[2] / "docs" / "metric_dictionary.md", ["Net Income", "Variance", "Nope"])
    assert [x[0] for x in d] == ["M11", "M30"]
    c = tmp_path / "c.csv"
    pd.DataFrame({"status": ["PASS", "PASS", "FAIL", "SKIPPED"]}).to_csv(c, index=False)
    assert check_summary(c) == {"passed": 2, "failed": 1, "skipped": 1, "total": 4}
    assert check_summary(tmp_path / "missing.csv") is None


# ------------------------------------------------------------------ the PDF
def test_pdf_without_risk_has_six_pages(finance, tmp_path):
    p, f = build_report("2025-12", finance, tmp_path / "r.pdf", generated="01 Jan 2026 09:00")
    r = PdfReader(p)
    assert len(r.pages) == 6 and f["pages"] == 6
    assert r.metadata.title == "Kestrel Credit Ltd board report December 2025"
    t = text(p)
    for s in ("Summary", "Commentary", "Profit and loss", "Variance drivers", "Balance sheet", "Notes and definitions", "Page 6 of 6",
              "1,962,349", "3,580,855", "Provision for Credit Losses", "129,229,642", "Difference 0.00"):
        assert s in t, s
    assert "Credit risk: scorecard" not in t


def test_pdf_with_risk_has_seven_pages(with_risk, tmp_path):
    p, f = build_report("2025-12", with_risk, tmp_path / "r6.pdf")
    t = text(p)
    assert len(PdfReader(p).pages) == 7 and "Credit risk: scorecard" in t and "Page 7 of 7" in t
    assert "Gini" in t and "Population Stability Index" in t and "not reconciled" in t


def test_pdf_for_an_earlier_month(finance, tmp_path):
    p, f = build_report("2024-06", finance, tmp_path / "j.pdf")
    t = text(p)
    assert "June 2024" in t and f["months_ytd"] == 6 and "December 2025" not in t


def test_data_checks_line_on_the_notes_page(finance, tmp_path):
    c = tmp_path / "data_checks.csv"
    pd.DataFrame({"status": ["PASS"] * 31 + ["FAIL"]}).to_csv(c, index=False)
    p, _ = build_report("2025-12", finance, tmp_path / "c.pdf", checks_csv=c)
    assert "31 of 32 passed, 1 failed" in text(p)


# ------------------------------------------------------------------ commentary
def test_commentary_states_the_numbers_and_stays_short(ex):
    c = commentary(board_facts(ex, "2025-12"))
    says = " ".join(c["says"])
    for s in ("-$1,618,506", "-45.2%", "Provision for Credit Losses", "-$1,661,256", "+$24,342", "$129,229,642", "3.67x"):
        assert s in says, s
    assert "costs and credit losses, not from lower revenue" in says
    assert len(c["says"]) <= 6 and 1 <= len(c["watch"]) <= 4
    assert any("largest unfavourable item" in w for w in c["watch"])
    assert any("Data checks were not run" in w for w in c["watch"])          # no check results passed in


def test_commentary_for_a_favourable_month_has_nothing_to_escalate(ex):
    f = board_facts(ex, "2023-12")
    f["checks"] = {"passed": 32, "failed": 0, "skipped": 0, "total": 32}
    f["bs"]["prev"] = f["bs"]["cur"]
    c = commentary(f)
    assert f["ni_ytd"]["variance"] > 0 and "favourable" in " ".join(c["says"])
    assert all("largest unfavourable" not in w for w in c["watch"])


def test_management_comment_is_read_cleaned_and_capped(tmp_path):
    assert management_comment("2025-12", tmp_path) is None
    (tmp_path / "2025-12.md").write_text("# Heading\n<!-- note -->\nCredit losses were high.\n\nReview in Q1.\n", encoding="utf-8")
    assert management_comment("2025-12", tmp_path) == "Credit losses were high. Review in Q1."
    (tmp_path / "2025-11.md").write_text("word " * 400, encoding="utf-8")
    long = management_comment("2025-11", tmp_path)
    assert len(long) <= 705 and long.endswith("...")
    (tmp_path / "2025-10.md").write_text("# only a heading\n", encoding="utf-8")
    assert management_comment("2025-10", tmp_path) is None


def test_management_comment_appears_on_the_commentary_page_only_when_present(finance, tmp_path):
    c = tmp_path / "c"
    c.mkdir()
    (c / "2025-12.md").write_text("Example comment: provisions are being reviewed.", encoding="utf-8")
    p, f = build_report("2025-12", finance, tmp_path / "with.pdf", commentary_dir=c)
    assert f["has_comment"] and "Management comment" in text(p) and "provisions are being reviewed" in text(p)
    p, f = build_report("2025-12", finance, tmp_path / "without.pdf", commentary_dir=tmp_path / "none")
    assert not f["has_comment"] and "Management comment" not in text(p) and "What the numbers say" in text(p)
