"""Export the scorecard results to CSV files for the Qlik Sense risk sheet, and write the master-measure list.

Reads what scorecard_model.py and scorecard_stability.py already wrote (no database needed):
  data/processed/lc_scored.csv, docs/psi_by_year.csv, docs/psi_features.csv, docs/psi_score_deciles.csv,
  docs/scorecard_metrics.csv, models/scorecard_model.json

Writes to dashboards/qlik/data/:
  risk_years.csv         one row per issue year: loans, default rate, predicted PD, AUC, Gini, KS, score PSI   (M52-M54, M56)
  risk_bands.csv         one row per issue year and risk band: loans, defaults, sums for PD and score          (M55)
  risk_feature_psi.csv   PSI of each scorecard feature against train, for each issue year and for the test set (M56)
  risk_score_deciles.csv share of train and test loans in each train score decile                              (M56)
  risk_models.csv        scorecard and Lending Club benchmarks: AUC, Gini, KS                                  (M52-M54)

Run:  python src/export_risk_for_qlik.py
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from clean_lending_club import TARGET
from scorecard_model import band_table

ROOT = Path(__file__).resolve().parents[1]
QLIK_DIR = ROOT / "dashboards" / "qlik"

EXPORTS: dict[str, list[str]] = {
    "risk_years.csv": ["issue_year", "data", "loans", "default_rate", "avg_predicted_pd", "auc", "gini", "ks", "score_psi",
                       "band_psi", "psi_status"],
    "risk_bands.csv": ["issue_year", "band", "score_range", "loans", "defaults", "pd_sum", "score_sum"],
    "risk_feature_psi.csv": ["feature", "iv_train", "psi_period", "psi"],
    "risk_score_deciles.csv": ["decile", "train_share", "test_share"],
    "risk_models.csv": ["model", "loans", "default_rate", "auc", "gini", "ks"],
}


def build_tables(scored: pd.DataFrame, by_year: pd.DataFrame, feats: pd.DataFrame, deciles: pd.DataFrame,
                 metrics: pd.DataFrame, cuts: list[float]) -> dict[str, pd.DataFrame]:
    ranges = band_table(scored, cuts)[["band", "score_range"]]
    g = scored.groupby(["issue_year", "band"]).agg(loans=(TARGET, "size"), defaults=(TARGET, "sum"), pd_sum=("pd", "sum"),
                                                   score_sum=("score", "sum")).reset_index()
    bands = g.merge(ranges, on="band")[EXPORTS["risk_bands.csv"]].sort_values(["issue_year", "band"])
    bands["pd_sum"] = bands["pd_sum"].round(4)
    bands["score_sum"] = bands["score_sum"].round(2)

    years = by_year.rename(columns={"AUC": "auc", "Gini": "gini", "KS": "ks", "score_status": "psi_status"})
    years = years[EXPORTS["risk_years.csv"]]

    value_cols = [c for c in feats.columns if c not in ("feature", "iv_train")]
    long = feats.melt(id_vars=["feature", "iv_train"], value_vars=value_cols, var_name="psi_period", value_name="psi")
    long["psi_period"] = long["psi_period"].astype(str)
    long = long[EXPORTS["risk_feature_psi.csv"]].sort_values(["psi_period", "psi"], ascending=[True, False])

    dec = deciles.rename(columns={"train": "train_share", "test": "test_share"})[EXPORTS["risk_score_deciles.csv"]]
    models = metrics.rename(columns={"set": "model", "AUC": "auc", "Gini": "gini", "KS": "ks"})[EXPORTS["risk_models.csv"]]
    return {"risk_years.csv": years, "risk_bands.csv": bands, "risk_feature_psi.csv": long,
            "risk_score_deciles.csv": dec, "risk_models.csv": models}


def export(data_dir: Path, docs_dir: Path, model_dir: Path, out_dir: Path = QLIK_DIR / "data") -> dict[str, int]:
    import json
    scored = pd.read_csv(data_dir / "lc_scored.csv")
    cuts = json.loads((model_dir / "scorecard_model.json").read_text(encoding="utf-8"))["band_cuts"]
    tables = build_tables(scored, pd.read_csv(docs_dir / "psi_by_year.csv"), pd.read_csv(docs_dir / "psi_features.csv"),
                          pd.read_csv(docs_dir / "psi_score_deciles.csv"), pd.read_csv(docs_dir / "scorecard_metrics.csv"), cuts)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_csv(out_dir / name, index=False)
    return {k: len(v) for k, v in tables.items()}


_TEST = "{1<Data={'test'}>}"      # ignores the user's selections: the headline numbers always describe the test year

# (name, expression, format, meaning)
MEASURES: list[tuple[str, str, str, str]] = [
    ("Test Gini", f"Only({_TEST} Gini)", "0.000", "Gini on the test year (M53). Fixed to test; ignores selections."),
    ("Test AUC", f"Only({_TEST} AUC)", "0.000", "AUC on the test year (M52). Fixed to test."),
    ("Test KS", f"Only({_TEST} KS)", "0.000", "KS on the test year (M54). Fixed to test."),
    ("Test Score PSI", f"Only({_TEST} [Score PSI])", "0.000",
     "Score PSI, test vs train, 10 train deciles (M56). Under 0.10 stable, 0.10-0.25 moderate, over 0.25 large."),
    ("Calibration Gap",
     f"Sum({_TEST} [Band Defaults]) / Sum({_TEST} [Band Loans]) - Sum({_TEST} [Band PD Sum]) / Sum({_TEST} [Band Loans])",
     "+0.0%;-0.0%", "Actual minus predicted default rate in the test year. Positive = the model under-predicts."),
    ("Loans", "Sum([Band Loans])", "#,##0", "Number of loans in the selection."),
    ("Share of Loans", "Sum([Band Loans]) / Sum(TOTAL [Band Loans])", "0.0%", "Band's share of the selected loans."),
    ("Default Rate", "Sum([Band Defaults]) / Sum([Band Loans])", "0.0%", "Observed default rate (M55)."),
    ("Predicted PD", "Sum([Band PD Sum]) / Sum([Band Loans])", "0.0%", "Average model probability of default."),
    ("Lift", "(Sum([Band Defaults]) / Sum([Band Loans])) / (Sum(TOTAL [Band Defaults]) / Sum(TOTAL [Band Loans]))", "0.00",
     "Band default rate divided by the overall default rate. Below 1 = safer than average."),
    ("Average Score", "Sum([Band Score Sum]) / Sum([Band Loans])", "0", "Average scorecard score (higher = safer)."),
    ("Year Loans", "Only([Year Loans])", "#,##0", "Loans in the issue year."),
    ("Year Default Rate", "Only([Year Default Rate])", "0.0%", "Actual default rate of the issue year."),
    ("Year Predicted PD", "Only([Year Avg Predicted PD])", "0.0%", "Average predicted PD of the issue year."),
    ("Year Gini", "Only(Gini)", "0.000", "Gini of the issue year."),
    ("Year AUC", "Only(AUC)", "0.000", "AUC of the issue year."),
    ("Year KS", "Only(KS)", "0.000", "KS of the issue year."),
    ("Year Score PSI", "Only([Score PSI])", "0.000", "Score PSI of the issue year against train."),
    ("Feature PSI (test)", "Sum({<[PSI Period]={'test'}>} PSI)", "0.000", "PSI of a feature, test vs train (M56). Use with the Feature dimension."),
    ("Decile Train Share", "Sum([Train Share])", "0.0%", "Share of train loans in the score decile."),
    ("Decile Test Share", "Sum([Test Share])", "0.0%", "Share of test loans in the score decile."),
    ("Model AUC", "Only({<Model={\"*, test\"}>} [Model AUC])", "0.000", "AUC on the test year of the scorecard or a benchmark."),
    ("Model Gini", "Only({<Model={\"*, test\"}>} [Model Gini])", "0.000", "Gini on the test year of the scorecard or a benchmark."),
]

_P = "Sum({<[PSI Period]={'test'}>} PSI)"
PSI_COLOUR = f"If({_P} < 0.1, RGB(45,164,78), If({_P} < 0.25, RGB(191,135,0), RGB(209,36,47)))"


def measures_markdown() -> str:
    lines = ["# Risk sheet: master measures", "",
             "Generated by `src/export_risk_for_qlik.py`. Create each one in Qlik Sense under Master items > Measures: "
             "paste the expression, set the number format with the format pattern, and use the name as shown.", "",
             "Measures starting with `Test` use `{1<Data={'test'}>}`, so they always describe the test year and ignore selections. "
             "All other measures follow the selections.", "",
             "| Name | Expression | Format pattern | Meaning |", "|---|---|---|---|"]
    for n, e, f, d in MEASURES:
        lines.append(f"| {n} | `{e}` | `{f}` | {d} |")
    lines += ["", "Colour expression for PSI bars (Colors and legend > By expression, tick Expression is a color code):", "",
              f"`{PSI_COLOUR}`", ""]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data" / "processed")
    ap.add_argument("--docs-dir", type=Path, default=ROOT / "docs")
    ap.add_argument("--model-dir", type=Path, default=ROOT / "models")
    ap.add_argument("--out-dir", type=Path, default=QLIK_DIR / "data")
    a = ap.parse_args()
    counts = export(a.data_dir, a.docs_dir, a.model_dir, a.out_dir)
    QLIK_DIR.mkdir(parents=True, exist_ok=True)
    (QLIK_DIR / "risk_measures.md").write_text(measures_markdown(), encoding="utf-8")
    for f, n in counts.items():
        print(f"  {a.out_dir / f}: {n:,} rows")
    print(f"  {QLIK_DIR / 'risk_measures.md'}")


if __name__ == "__main__":
    main()
