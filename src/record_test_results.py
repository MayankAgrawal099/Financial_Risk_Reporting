"""Record the scorecard accuracy numbers in the test results file.

Reads the files written by scorecard_model.py and scorecard_stability.py (and, if present, run_data_checks.py)
and writes one plain-text block called SCORECARD ACCURACY into reports/test_results.txt, below the pytest output.
Running it again replaces that block, so it never doubles up. The same numbers go to reports/scorecard_accuracy.md.

Order:  python -m pytest -v > reports\\test_results.txt   then   python src\\record_test_results.py
"""
from __future__ import annotations

import argparse
import json
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


def run(docs: Path, models: Path, reports: Path, today: str | None = None) -> str:
    reports.mkdir(parents=True, exist_ok=True)
    block = build_text(docs, models, reports, today)
    f = reports / "test_results.txt"
    old = read_text_any(f) if f.exists() else ""
    f.write_text(splice(old, block), encoding="utf-8")
    md = ["# Scorecard accuracy numbers", "", "Same content as the SCORECARD ACCURACY block at the end of `test_results.txt`.", "",
          "```text", block.replace(START, "").replace(END, "").strip(), "```", ""]
    (reports / "scorecard_accuracy.md").write_text("\n".join(md), encoding="utf-8")
    return block


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--docs-dir", type=Path, default=ROOT / "docs")
    ap.add_argument("--model-dir", type=Path, default=ROOT / "models")
    ap.add_argument("--reports-dir", type=Path, default=ROOT / "reports")
    a = ap.parse_args()
    print(run(a.docs_dir, a.model_dir, a.reports_dir))
    print(f"\nWritten into {a.reports_dir / 'test_results.txt'} and {a.reports_dir / 'scorecard_accuracy.md'}")


if __name__ == "__main__":
    main()
