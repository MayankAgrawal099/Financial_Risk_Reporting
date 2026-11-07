"""Record the scorecard accuracy numbers in the test results file and write the final results summary.

Reads the files written by scorecard_model.py and scorecard_stability.py (and, if present, run_data_checks.py)
and writes one plain-text block called SCORECARD ACCURACY into reports/test_results.txt, below the pytest output.
Running it again replaces that block, so it never doubles up. The same numbers go to reports/scorecard_accuracy.md.

It also writes reports/test_results_summary.md: one readable page with the pytest results per test file (from the
junit file), every data check with its result, and the model metrics. The page says plainly when the run is not
final (a test failed or was skipped, a data check failed or was skipped).

Order:  python -m pytest -v --junitxml=reports\\pytest_results.xml > reports\\test_results.txt
        python src\\run_data_checks.py          (with FIN_PG_DSN set, so the SQL checks run)
        python src\\record_test_results.py
"""
from __future__ import annotations

import argparse
import ast
import json
import platform
import re
import sys
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
START, END = "=== SCORECARD ACCURACY (start) ===", "=== SCORECARD ACCURACY (end) ==="


def _table(df: pd.DataFrame, fmt: dict[str, str]) -> list[str]:
    cols = list(df.columns)
    cells = [[fmt.get(c, "{}").format(r[c]) for c in cols] for _, r in df.iterrows()]
    w = [max(len(c), *(len(row[i]) for row in cells)) for i, c in enumerate(cols)]
    line = lambda row: "  ".join(v.rjust(w[i]) if i else v.ljust(w[i]) for i, v in enumerate(row))
    return [line(cols), "  ".join("-" * x for x in w)] + [line(r) for r in cells]


def build_text(docs: Path, models: Path, reports: Path, today: str | None = None) -> str:
    metrics = pd.read_csv(docs / "scorecard_metrics.csv")
    bands = pd.read_csv(docs / "scorecard_bands.csv")
    stab = json.loads((models / "scorecard_stability.json").read_text(encoding="utf-8"))
    years = pd.read_csv(docs / "psi_by_year.csv")
    model = json.loads((models / "scorecard_model.json").read_text(encoding="utf-8"))

    out = [START, f"Recorded {today or date.today().isoformat()} by src/record_test_results.py", "",
           f"Data: Lending Club 36-month loans. Train issue years {', '.join(map(str, model['train_years']))}, "
           f"test {', '.join(str(int(y)) for y in years.loc[years['data'] == 'test', 'issue_year'])} (time-based split).",
           f"Model: logistic regression on WoE features ({len(model['coefficients'])} features), "
           "score 600 points at 50:1 odds, 20 points to double the odds, higher = safer.", "",
           "1. Discrimination (higher is better)"]
    m = metrics.rename(columns={"set": "model / data"})
    out += _table(m[["model / data", "loans", "default_rate", "AUC", "Gini", "KS"]],
                  {"loans": "{:,.0f}", "default_rate": "{:.2%}", "AUC": "{:.3f}", "Gini": "{:.3f}", "KS": "{:.3f}"})
    out += ["", "2. Default rate by risk band (A = safest)"]
    for name in ("train", "test"):
        b = bands[bands["data"] == name].copy()
        out += [f"{name}:"] + _table(b[["band", "score_range", "loans", "default_rate", "avg_predicted_pd", "lift"]],
                                     {"loans": "{:,.0f}", "default_rate": "{:.2%}", "avg_predicted_pd": "{:.2%}", "lift": "{:.2f}"})
        rates = b["default_rate"].to_numpy()
        out.append(f"bands rise steadily from A to E: {'yes' if (rates[1:] > rates[:-1]).all() else 'NO'}")
        out.append("")
    out += ["3. Stability (PSI, metric M56; under 0.10 stable, 0.10-0.25 moderate, over 0.25 large)",
            f"score PSI test vs train, 10 deciles: {stab['score_psi_test']:.3f} ({stab['status']})",
            f"score PSI test vs train, 5 bands:    {stab['band_psi_test']:.3f}",
            f"features: {stab['n_stable']} stable, {stab['n_moderate']} moderate, {stab['n_large']} large "
            f"(largest: {stab['top_feature']} {stab['top_feature_psi']:.3f})", ""]
    out += _table(years[["issue_year", "data", "default_rate", "avg_predicted_pd", "AUC", "Gini", "KS", "score_psi"]],
                  {"default_rate": "{:.2%}", "avg_predicted_pd": "{:.2%}", "AUC": "{:.3f}", "Gini": "{:.3f}",
                   "KS": "{:.3f}", "score_psi": "{:.3f}", "issue_year": "{:.0f}"})
    gap = stab["actual_test"] - stab["pred_test"]
    out += ["", "4. Calibration",
            f"test year: predicted default rate {stab['pred_test']:.2%}, actual {stab['actual_test']:.2%} "
            f"(gap {gap:+.2%}). " + ("Ranking is stable but the level drifts; an intercept recalibration on recent loans would fix it."
                                    if abs(gap) > 0.01 else "The level is well calibrated.")]
    dc = reports / "data_checks.csv"
    if dc.exists():
        n = pd.read_csv(dc)["status"].value_counts()
        out += ["", "5. Data checks (reports/data_checks.md)",
                f"{n.get('PASS', 0)} passed, {n.get('FAIL', 0)} failed, {n.get('SKIPPED', 0)} skipped"]
    out += ["", END]
    return "\n".join(out)


def _md(df: pd.DataFrame, fmt: dict[str, str] | None = None) -> list[str]:
    fmt = fmt or {}
    esc = lambda v: str(v).replace("|", "\\|").replace("\n", " ")
    rows = [[esc(fmt.get(c, "{}").format(r[c])) for c in df.columns] for _, r in df.iterrows()]
    return ["| " + " | ".join(df.columns) + " |", "|" + "---|" * len(df.columns)] + ["| " + " | ".join(r) + " |" for r in rows]


def read_junit(path: Path) -> dict | None:
    """Per test file counts from a pytest --junitxml file, or None if there is no file."""
    if not path.exists():
        return None
    root = ET.parse(path).getroot()
    files: dict[str, dict[str, int]] = {}
    seconds = 0.0
    for suite in root.iter("testsuite"):
        seconds += float(suite.get("time", 0) or 0)
    for case in root.iter("testcase"):
        m = re.search(r"(test_[A-Za-z0-9_]+)", case.get("classname", ""))
        name = m.group(1) if m else case.get("classname", "?")
        d = files.setdefault(name, {"passed": 0, "failed": 0, "skipped": 0})
        if case.find("skipped") is not None:
            d["skipped"] += 1
        elif case.find("failure") is not None or case.find("error") is not None:
            d["failed"] += 1
        else:
            d["passed"] += 1
    total = {k: sum(f[k] for f in files.values()) for k in ("passed", "failed", "skipped")}
    return {"files": files, "total": total, "seconds": seconds}


def _doc_line(tests_dir: Path, module: str) -> str:
    f = tests_dir / f"{module}.py"
    if f.exists():
        doc = ast.get_docstring(ast.parse(f.read_text(encoding="utf-8"))) or ""
        if doc:
            line = re.sub(r"^Tests? (for|of) ", "", doc.split("\n")[0].rstrip("."))
            return line[:1].upper() + line[1:]
    return module.replace("test_", "").replace("_", " ").capitalize()


def verdict(junit: dict | None, checks: pd.DataFrame | None) -> str:
    issues = []
    if junit is None:
        issues.append("pytest results were not recorded (run pytest with --junitxml, see the commands below)")
    else:
        t = junit["total"]
        if t["failed"]:
            issues.append(f"{t['failed']} test(s) FAILED")
        if t["skipped"]:
            issues.append(f"{t['skipped']} test(s) skipped (the PostgreSQL tests need FIN_PG_DSN to be set)")
    if checks is None:
        issues.append("data checks were not recorded (run src/run_data_checks.py)")
    else:
        n = checks["status"].value_counts()
        if n.get("FAIL", 0):
            issues.append(f"{n['FAIL']} data check(s) FAILED")
        if n.get("SKIPPED", 0):
            ids = ", ".join(checks.loc[checks["status"] == "SKIPPED", "id"])
            issues.append(f"{n['SKIPPED']} data check(s) skipped ({ids}): run with the database connected and the Lending Club sample loaded")
    if issues:
        return "**Result: not final.** " + "; ".join(issues) + "."
    n_tests = sum(junit["total"].values())
    return f"**Result: final.** All {n_tests} tests passed and all {len(checks)} data checks passed, with nothing skipped."


def build_summary(docs: Path, models: Path, reports: Path, tests_dir: Path, junit_path: Path | None = None,
                  today: str | None = None) -> str:
    junit = read_junit(junit_path or reports / "pytest_results.xml")
    dc = reports / "data_checks.csv"
    checks = pd.read_csv(dc, dtype=str).fillna("") if dc.exists() else None
    metrics = pd.read_csv(docs / "scorecard_metrics.csv")
    bands = pd.read_csv(docs / "scorecard_bands.csv")
    years = pd.read_csv(docs / "psi_by_year.csv")
    stab = json.loads((models / "scorecard_stability.json").read_text(encoding="utf-8"))
    model = json.loads((models / "scorecard_model.json").read_text(encoding="utf-8"))
    test_years = ", ".join(str(int(y)) for y in years.loc[years["data"] == "test", "issue_year"])

    out = ["# Test results summary", "", verdict(junit, checks), "",
           f"Recorded {today or date.today().isoformat()} by `src/record_test_results.py` on Python {platform.python_version()} "
           f"({platform.system()}). Raw pytest output: `reports/test_results.txt`. Data check detail: `reports/data_checks.md`. "
           "Accuracy numbers as plain text: `reports/scorecard_accuracy.md`.", "", "## 1. Automated tests (pytest)", ""]
    if junit is None:
        out += ["Not recorded. Run `python -m pytest -v --junitxml=reports/pytest_results.xml` and record again.", ""]
    else:
        t = junit["total"]
        out += [f"**{t['passed']} passed, {t['failed']} failed, {t['skipped']} skipped** in {junit['seconds']:.0f} seconds, "
                f"across {len(junit['files'])} test files.", ""]
        rows = pd.DataFrame([{"Test file": f"`{k}.py`", "What it checks": _doc_line(tests_dir, k), "Passed": v["passed"],
                              "Failed": v["failed"], "Skipped": v["skipped"]} for k, v in sorted(junit["files"].items())])
        out += _md(rows) + [""]
    out += ["## 2. Data checks", ""]
    if checks is None:
        out += ["Not recorded. Run `python src/run_data_checks.py` and record again.", ""]
    else:
        n = checks["status"].value_counts()
        out += [f"**{n.get('PASS', 0)} passed, {n.get('FAIL', 0)} failed, {n.get('SKIPPED', 0)} skipped** of {len(checks)}. "
                "Ledger checks (L) read the synthetic ledger files, SQL checks (S) read the PostgreSQL views, "
                "Lending Club checks (R) read the real loan sample.", ""]
        area = checks.groupby("area")["status"].agg(Checks="size", Passed=lambda s: (s == "PASS").sum(),
                                                    Failed=lambda s: (s == "FAIL").sum(), Skipped=lambda s: (s == "SKIPPED").sum())
        out += _md(area.reset_index().rename(columns={"area": "Area"})) + [""]
        det = checks.rename(columns={"id": "ID", "area": "Area", "description": "Check", "expected": "Expected",
                                     "actual": "Actual", "status": "Result"})
        out += _md(det[["ID", "Area", "Check", "Expected", "Actual", "Result"]]) + [""]
    out += ["## 3. Scorecard model metrics (real Lending Club data)", "",
            f"Lending Club 36-month loans. Train issue years {', '.join(map(str, model['train_years']))}, test {test_years} "
            f"(time-based split, so the test year is the future the model has not seen). Logistic regression on "
            f"{len(model['coefficients'])} WoE features; score 600 points at 50:1 odds, 20 points to double the odds.", "",
            "### Discrimination (higher is better)", ""]
    m = metrics.rename(columns={"set": "Model / data", "loans": "Loans", "default_rate": "Default rate"})
    out += _md(m[["Model / data", "Loans", "Default rate", "AUC", "Gini", "KS"]],
               {"Loans": "{:,.0f}", "Default rate": "{:.2%}", "AUC": "{:.3f}", "Gini": "{:.3f}", "KS": "{:.3f}"}) + [""]
    out += ["### Default rate by risk band, test year (A = safest)", ""]
    b = bands[bands["data"] == "test"].rename(columns={"band": "Band", "score_range": "Score range", "loans": "Loans",
                                                       "default_rate": "Default rate", "avg_predicted_pd": "Predicted PD", "lift": "Lift"})
    out += _md(b[["Band", "Score range", "Loans", "Default rate", "Predicted PD", "Lift"]],
               {"Loans": "{:,.0f}", "Default rate": "{:.2%}", "Predicted PD": "{:.2%}", "Lift": "{:.2f}"}) + [""]
    rates = b["Default rate"].to_numpy()
    out += [f"Default rate rises from band A to band E at every step: **{'yes' if (rates[1:] > rates[:-1]).all() else 'NO'}**.", "",
            "### Stability (PSI, metric M56: under 0.10 stable, 0.10 to 0.25 moderate, over 0.25 large)", "",
            f"Score PSI, test against train: **{stab['score_psi_test']:.3f}** ({stab['status']}). Features: {stab['n_stable']} stable, "
            f"{stab['n_moderate']} moderate, {stab['n_large']} large (largest: {stab['top_feature']}, {stab['top_feature_psi']:.3f}).", ""]
    y = years.rename(columns={"issue_year": "Issue year", "data": "Data", "default_rate": "Default rate",
                              "avg_predicted_pd": "Predicted PD", "score_psi": "Score PSI"})
    out += _md(y[["Issue year", "Data", "Default rate", "Predicted PD", "AUC", "Gini", "KS", "Score PSI"]],
               {"Issue year": "{:.0f}", "Default rate": "{:.2%}", "Predicted PD": "{:.2%}", "AUC": "{:.3f}", "Gini": "{:.3f}",
                "KS": "{:.3f}", "Score PSI": "{:.3f}"}) + [""]
    gap = stab["actual_test"] - stab["pred_test"]
    out += ["### Calibration", "",
            f"Test year: predicted default rate {stab['pred_test']:.2%}, actual {stab['actual_test']:.2%} (gap {gap:+.2%}). "
            + ("The model ranks borrowers consistently but under-predicts the level; an intercept recalibration on recent loans would fix it."
               if gap > 0.01 else "The model over-predicts the level." if gap < -0.01 else "The level is well calibrated."), "",
            "## 4. How these results were produced", "", "```", "python -m pytest -v --junitxml=reports/pytest_results.xml > reports/test_results.txt",
            "python src/run_data_checks.py          # with FIN_PG_DSN set, so the SQL and loan checks run",
            "python src/record_test_results.py", "```", ""]
    return "\n".join(out)


def splice(existing: str, block: str) -> str:
    """Replace the old block if there is one, otherwise append."""
    if START in existing and END in existing:
        a, b = existing.index(START), existing.index(END) + len(END)
        return existing[:a] + block + existing[b:]
    return existing.rstrip("\n") + ("\n\n" if existing.strip() else "") + block + "\n"


def read_text_any(f: Path) -> str:
    """Read a text file whatever the redirect wrote: PowerShell's > gives UTF-16, cmd and bash give UTF-8 or ANSI."""
    raw = f.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def run(docs: Path, models: Path, reports: Path, today: str | None = None, tests_dir: Path = ROOT / "tests",
        junit_path: Path | None = None) -> str:
    reports.mkdir(parents=True, exist_ok=True)
    block = build_text(docs, models, reports, today)
    f = reports / "test_results.txt"
    old = read_text_any(f) if f.exists() else ""
    f.write_text(splice(old, block), encoding="utf-8")
    md = ["# Scorecard accuracy numbers", "", "Same content as the SCORECARD ACCURACY block at the end of `test_results.txt`.", "",
          "```text", block.replace(START, "").replace(END, "").strip(), "```", ""]
    (reports / "scorecard_accuracy.md").write_text("\n".join(md), encoding="utf-8")
    (reports / "test_results_summary.md").write_text(build_summary(docs, models, reports, tests_dir, junit_path, today), encoding="utf-8")
    return block


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--docs-dir", type=Path, default=ROOT / "docs")
    ap.add_argument("--model-dir", type=Path, default=ROOT / "models")
    ap.add_argument("--reports-dir", type=Path, default=ROOT / "reports")
    a = ap.parse_args()
    print(run(a.docs_dir, a.model_dir, a.reports_dir))
    print(f"\nWritten into {a.reports_dir / 'test_results.txt'}, {a.reports_dir / 'scorecard_accuracy.md'} "
          f"and {a.reports_dir / 'test_results_summary.md'}")
    print((a.reports_dir / "test_results_summary.md").read_text(encoding="utf-8").split("\n")[2])


if __name__ == "__main__":
    main()
