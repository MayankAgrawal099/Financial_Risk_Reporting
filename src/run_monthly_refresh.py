"""Monthly refresh: reload data, run the data checks, export the extracts, build the board report PDF.

This is the one command the Power Automate flow runs. It writes reports/latest_report.json (status, PDF path, email subject
and body) so the flow can read the result, and appends a line to reports/refresh_log.csv.

Status: OK       everything ran and the checks passed
        WARN     the PDF was built but something was skipped (data checks not run, or no scorecard extracts)
        FAILED   nothing should be sent: a step failed or a data check failed (exit code 1)

Run:  python src/run_monthly_refresh.py --dsn postgresql://postgres:PASSWORD@localhost:5432/fin_risk
      python src/run_monthly_refresh.py --skip-db          (no database: rebuild the PDF from the extracts in dashboards/qlik/data)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from build_board_report import COMPANY, DATA_DIR, build_report, headlines, latest_month, load_extracts, month_label
from send_report_email import email_report

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCORECARD_FILES = ["data/processed/lc_scored.csv", "docs/psi_by_year.csv", "docs/psi_features.csv", "docs/psi_score_deciles.csv",
                   "docs/scorecard_metrics.csv", "models/scorecard_model.json"]


def _run(args: list[str]) -> tuple[int, str]:
    p = subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True)
    tail = "\n".join((p.stdout + p.stderr).strip().splitlines()[-6:])
    return p.returncode, tail


def reload_database(dsn: str, lending_club: str | None) -> str:
    args = [str(SRC / "load_to_postgres.py"), "--dsn", dsn] + (["--lending-club"] + ([lending_club] if lending_club != "default" else [])
                                                               if lending_club else [])
    rc, tail = _run(args)
    if rc:
        raise RuntimeError(f"database reload failed: {tail}")
    return "reloaded"


def run_checks(dsn: str, out_dir: Path) -> dict:
    """Runs src/run_data_checks.py and returns {passed, failed, skipped, failed_ids}."""
    rc, tail = _run([str(SRC / "run_data_checks.py"), "--dsn", dsn, "--out-dir", str(out_dir)])
    f = out_dir / "data_checks.csv"
    if not f.exists():
        raise RuntimeError(f"data checks did not run: {tail}")
    return summarise_checks(f)


def summarise_checks(path: Path) -> dict:
    d = pd.read_csv(path)
    s = d["status"].value_counts()
    return {"passed": int(s.get("PASS", 0)), "failed": int(s.get("FAIL", 0)), "skipped": int(s.get("SKIPPED", 0)),
            "failed_ids": list(d.loc[d["status"] == "FAIL", "id"])}


def export_extracts(dsn: str, data_dir: Path) -> str:
    from export_for_qlik import export
    n = export(dsn, data_dir)
    return f"{len(n)} finance files"


def export_risk(data_dir: Path) -> str | None:
    if not all((ROOT / f).exists() for f in SCORECARD_FILES):
        return None
    from export_risk_for_qlik import export
    n = export(ROOT / "data" / "processed", ROOT / "docs", ROOT / "models", data_dir)
    return f"{len(n)} risk files"


def run_refresh(month: str | None = None, dsn: str | None = None, skip_db: bool = False, skip_reload: bool = False,
                data_dir: Path = DATA_DIR, out_dir: Path = ROOT / "reports", lending_club: str | None = None,
                send_email: bool = False, dry_run_email: bool = False, checks_runner=run_checks) -> dict:
    steps, warnings = [], []
    info = {"status": "OK", "month": month, "month_label": "", "pdf_path": "", "pdf_name": "", "subject": "", "body": "",
            "headlines": [], "management_comment": False, "checks": None, "warnings": warnings, "steps": steps, "error": "",
            "generated_at": datetime.now().isoformat(timespec="seconds")}

    def step(name, fn):
        t0 = time.time()
        try:
            r = fn()
            steps.append({"step": name, "status": "ok" if r is not None else "skipped", "seconds": round(time.time() - t0, 1),
                          "detail": r if isinstance(r, str) else ""})
            return r
        except Exception as e:                      # noqa: BLE001 - every failure must end up in the status file
            steps.append({"step": name, "status": "failed", "seconds": round(time.time() - t0, 1), "detail": str(e)})
            raise

    dsn = dsn.strip().strip('"').strip("'") if dsn else dsn      # Windows `set VAR="..."` keeps the quotes
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        use_db = bool(dsn) and not skip_db
        if not use_db:
            warnings.append("Database steps skipped: the report uses the extracts already in the data folder; data checks were not run.")
        if use_db and not skip_reload:
            step("reload_database", lambda: reload_database(dsn, lending_club))
        checks = None
        if use_db:
            checks = step("data_checks", lambda: checks_runner(dsn, out_dir))
            info["checks"] = checks
            if checks["failed"]:
                raise RuntimeError(f"{checks['failed']} data check(s) failed: {', '.join(checks['failed_ids'])}. The report was not built.")
            step("export_finance_extracts", lambda: export_extracts(dsn, data_dir))
        if step("export_risk_extracts", lambda: export_risk(data_dir)) is None:
            warnings.append("Risk extracts were not rebuilt because the scorecard outputs (data/processed) are not on this machine; the committed extracts in dashboards/qlik/data are used.")
        month = month or latest_month(load_extracts(data_dir))
        info["month"], info["month_label"] = month, month_label(month)
        pdf = out_dir / f"board_report_{month}.pdf"
        _, facts = step("build_pdf", lambda: (build_report(month, data_dir, pdf, out_dir / "data_checks.csv" if use_db else None)))
        info.update(pdf_path=str(pdf), pdf_name=pdf.name, headlines=headlines(facts), management_comment=facts.get("has_comment", False))
        info["subject"] = f"{COMPANY} board report - {info['month_label']}"
        chk = (f"Data checks at the last refresh: {checks['passed']} passed, {checks['failed']} failed.\n" if checks
               else "Data checks were not run for this report.\n")
        info["body"] = (f"Hello,\n\nPlease find attached the board report for {info['month_label']}.\n\nHighlights:\n"
                        + "\n".join(f"- {h}" for h in info["headlines"][:3]) + f"\n\n{chk}\n"
                        "This report was generated automatically from the finance and risk extracts.\n")
        if warnings:
            info["status"] = "WARN"
        if send_email:
            step("email", lambda: email_report(info, dry_run=dry_run_email, out_dir=out_dir / "outbox"))
    except Exception as e:                          # noqa: BLE001
        info.update(status="FAILED", error=str(e), subject=f"FAILED: board report refresh {info['month'] or ''}".strip(),
                    body=f"The monthly board report refresh failed.\n\nReason: {e}\n\nNo report was sent.")
    (out_dir / "latest_report.json").write_text(json.dumps(info, indent=2, default=str), encoding="utf-8")
    log = out_dir / "refresh_log.csv"
    new = not log.exists()
    with open(log, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["run_at", "month", "status", "pdf", "checks_failed", "error"])
        w.writerow([info["generated_at"], info["month"], info["status"], info["pdf_name"],
                    (info["checks"] or {}).get("failed", ""), info["error"]])
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--month", help="YYYY-MM (default: latest month in the data)")
    ap.add_argument("--dsn", default=os.environ.get("FIN_PG_DSN"), help="PostgreSQL connection string (or env FIN_PG_DSN)")
    ap.add_argument("--skip-db", action="store_true", help="do not touch the database; rebuild from the extracts")
    ap.add_argument("--skip-reload", action="store_true", help="keep the data already in the database (checks and exports still run)")
    ap.add_argument("--lending-club", nargs="?", const="default", help="also reload the Lending Club sample (optional CSV path)")
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    ap.add_argument("--out-dir", type=Path, default=ROOT / "reports")
    ap.add_argument("--email", action="store_true", help="also send the email from Python (needs SMTP_* and MAIL_TO)")
    ap.add_argument("--dry-run-email", action="store_true", help="with --email: write an .eml file instead of sending")
    a = ap.parse_args()
    info = run_refresh(a.month, a.dsn, a.skip_db, a.skip_reload, a.data_dir, a.out_dir, a.lending_club, a.email, a.dry_run_email)
    for s in info["steps"]:
        print(f"  {s['status']:8} {s['step']:26} {s['seconds']:>5}s  {s['detail']}")
    for w in info["warnings"]:
        print(f"  warning: {w}")
    print(f"STATUS={info['status']}")
    if info["pdf_path"]:
        print(f"REPORT={info['pdf_path']}")
    if info["error"]:
        print(f"ERROR={info['error']}")
    print(f"Status file: {a.out_dir / 'latest_report.json'}")
    return 1 if info["status"] == "FAILED" else 0


if __name__ == "__main__":
    sys.exit(main())
