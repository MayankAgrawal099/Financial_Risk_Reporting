"""Tests for the monthly refresh runner: status file, log, failure handling and the email step."""
import json
import os
import shutil

import pandas as pd
import pytest

from build_board_report import DATA_DIR
from run_monthly_refresh import run_refresh, summarise_checks


@pytest.fixture()
def data_dir(tmp_path):
    d = tmp_path / "extracts"
    shutil.copytree(DATA_DIR, d, ignore=shutil.ignore_patterns("risk_*.csv"))
    return d


def test_skip_db_run_builds_pdf_and_warns(data_dir, tmp_path):
    out = tmp_path / "reports"
    info = run_refresh(dsn=None, data_dir=data_dir, out_dir=out)
    assert info["status"] == "WARN" and info["month"] == "2025-12" and info["error"] == ""
    assert isinstance(info["management_comment"], bool)
    assert (out / "board_report_2025-12.pdf").exists()
    saved = json.loads((out / "latest_report.json").read_text())
    assert saved["pdf_name"] == "board_report_2025-12.pdf" and "board report - December 2025" in saved["subject"]
    assert "Highlights:" in saved["body"] and "Data checks were not run" in saved["body"]
    assert any("data checks were not run" in w for w in saved["warnings"])
    log = pd.read_csv(out / "refresh_log.csv")
    assert list(log.columns) == ["run_at", "month", "status", "pdf", "checks_failed", "error"] and len(log) == 1


def test_log_appends_and_month_can_be_chosen(data_dir, tmp_path):
    out = tmp_path / "reports"
    run_refresh(dsn=None, data_dir=data_dir, out_dir=out)
    info = run_refresh(month="2024-12", dsn=None, data_dir=data_dir, out_dir=out)
    assert info["month_label"] == "December 2024" and (out / "board_report_2024-12.pdf").exists()
    assert list(pd.read_csv(out / "refresh_log.csv")["month"]) == ["2025-12", "2024-12"]


def test_unknown_month_fails_cleanly(data_dir, tmp_path):
    info = run_refresh(month="2031-03", dsn=None, data_dir=data_dir, out_dir=tmp_path / "r")
    saved = json.loads((tmp_path / "r" / "latest_report.json").read_text())
    assert info["status"] == "FAILED" and "No data for month 2031-03" in saved["error"]
    assert saved["subject"].startswith("FAILED:") and "No report was sent" in saved["body"] and saved["pdf_path"] == ""


def test_failed_data_check_stops_before_the_pdf(data_dir, tmp_path):
    def bad_checks(dsn, out_dir):
        return {"passed": 30, "failed": 2, "skipped": 0, "failed_ids": ["S02", "S03"]}
    out = tmp_path / "r"
    info = run_refresh(dsn="postgresql://x", skip_reload=True, data_dir=data_dir, out_dir=out, checks_runner=bad_checks)
    assert info["status"] == "FAILED" and "S02, S03" in info["error"]
    assert not list(out.glob("*.pdf"))
    assert [s["step"] for s in info["steps"]] == ["data_checks"] and info["steps"][0]["status"] == "ok"


def test_summarise_checks(tmp_path):
    f = tmp_path / "c.csv"
    pd.DataFrame({"id": ["S01", "S02", "R01"], "status": ["PASS", "FAIL", "SKIPPED"]}).to_csv(f, index=False)
    assert summarise_checks(f) == {"passed": 1, "failed": 1, "skipped": 1, "failed_ids": ["S02"]}


def test_email_dry_run_attaches_the_pdf(data_dir, tmp_path):
    out = tmp_path / "r"
    info = run_refresh(dsn=None, data_dir=data_dir, out_dir=out, send_email=True, dry_run_email=True)
    assert info["status"] == "WARN" and info["steps"][-1]["step"] == "email" and info["steps"][-1]["status"] == "ok"
    eml = (out / "outbox" / "board_report_2025-12.eml").read_bytes()
    assert b"board_report_2025-12.pdf" in eml and b"application/pdf" in eml


@pytest.mark.skipif(not os.environ.get("FIN_PG_DSN"), reason="FIN_PG_DSN not set")
def test_full_run_with_database(data_dir, tmp_path):
    info = run_refresh(dsn=os.environ["FIN_PG_DSN"], skip_reload=True, data_dir=data_dir, out_dir=tmp_path / "r")
    assert info["status"] in ("OK", "WARN") and info["checks"]["failed"] == 0 and info["checks"]["passed"] > 25
    assert [s["step"] for s in info["steps"]][:3] == ["data_checks", "export_finance_extracts", "export_risk_extracts"]
    assert "Data checks at the last refresh" in info["body"]
