"""Scorecard stability check: PSI of the score and of each model feature, earlier vs later issue years (metric M56).

PSI = sum over bins of (share_later - share_train) x ln(share_later / share_train)
Rule of thumb: under 0.10 stable, 0.10 to 0.25 moderate shift, over 0.25 large shift.

What it does
  1. Score PSI on 10 bins whose edges are the train score deciles (and on the 5 risk bands), for the test set
     and for every issue year against the train set.
  2. Feature PSI for each feature in the scorecard, on the WoE bins fitted on train (a Missing bin and an
     "unseen" bin are counted too).
  3. Performance by issue year: AUC, Gini, KS, default rate, average predicted PD, default rate per band.
Everything is fitted on train; later years are only measured against it.

Run:  python src/scorecard_stability.py
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from clean_lending_club import TARGET
from scorecard_model import BAND_NAMES, FIG_DIR, auc, gini, ks_statistic
from woe_binning import assign_bins

ROOT = Path(__file__).resolve().parents[1]
STABLE, MODERATE = 0.10, 0.25
N_SCORE_BINS = 10
FLOOR = 1e-4            # share floor so an empty bin does not give ln(0)


# ------------------------------------------------------------------ PSI
def psi_status(v: float) -> str:
    return "stable" if v < STABLE else "moderate shift" if v < MODERATE else "large shift"


def psi_from_shares(expected, actual) -> float:
    e = np.clip(np.asarray(expected, float), FLOOR, None)
    a = np.clip(np.asarray(actual, float), FLOOR, None)
    return float(np.sum((a - e) * np.log(a / e)))


def psi_from_counts(expected_counts, actual_counts) -> float:
    e, a = np.asarray(expected_counts, float), np.asarray(actual_counts, float)
    return psi_from_shares(e / e.sum(), a / a.sum())


def score_edges(train_scores, n_bins: int = N_SCORE_BINS) -> np.ndarray:
    """Inner edges at the train percentiles; duplicates removed so ties cannot create empty bins."""
    return np.unique(np.percentile(train_scores, np.linspace(0, 100, n_bins + 1)[1:-1]))


def score_counts(scores, edges) -> np.ndarray:
    idx = np.searchsorted(edges, np.asarray(scores, float), side="right")
    return np.bincount(idx, minlength=len(edges) + 1)


def score_psi(train_scores, other_scores, n_bins: int = N_SCORE_BINS) -> float:
    edges = score_edges(train_scores, n_bins)
    return psi_from_counts(score_counts(train_scores, edges), score_counts(other_scores, edges))


def band_psi(train_bands: pd.Series, other_bands: pd.Series) -> float:
    e = train_bands.value_counts().reindex(BAND_NAMES).fillna(0).to_numpy()
    a = other_bands.value_counts().reindex(BAND_NAMES).fillna(0).to_numpy()
    return psi_from_counts(e, a)


def decile_table(train_scores, sets: dict[str, pd.Series], n_bins: int = N_SCORE_BINS) -> pd.DataFrame:
    edges = score_edges(train_scores, n_bins)
    out = {"decile": range(1, len(edges) + 2)}
    out["train"] = score_counts(train_scores, edges) / len(train_scores)
    for k, s in sets.items():
        out[k] = score_counts(s, edges) / len(s)
    return pd.DataFrame(out)       # decile 1 = lowest scores = riskiest


def bin_shares(bins: pd.Series, labels: list[int]) -> np.ndarray:
    c = bins.value_counts().reindex(labels).fillna(0).to_numpy()
    return c / c.sum()


def feature_psi(train: pd.DataFrame, sets: dict[str, pd.DataFrame], specs: dict, features: list[str]) -> pd.DataFrame:
    rows = []
    for f in features:
        tr_bins = assign_bins(train[f], specs[f])
        others = {k: assign_bins(d[f], specs[f]) for k, d in sets.items()}
        labels = sorted(set(tr_bins) | {b for s in others.values() for b in s})
        e = bin_shares(tr_bins, labels)
        row = {"feature": f, "iv_train": specs[f]["iv"]}
        for k, b in others.items():
            row[k] = psi_from_shares(e, bin_shares(b, labels))
        rows.append(row)
    t = pd.DataFrame(rows)
    key = "test" if "test" in t else t.columns[-1]
    return t.sort_values(key, ascending=False).reset_index(drop=True)


# ------------------------------------------------------------------ performance by year
def year_table(scored: pd.DataFrame, train_scores, bands_ref: pd.Series) -> pd.DataFrame:
    rows = []
    for y, d in scored.groupby("issue_year"):
        row = {"issue_year": int(y), "data": "/".join(sorted(d["data"].unique())), "loans": len(d),
               "default_rate": d[TARGET].mean(), "avg_predicted_pd": d["pd"].mean(),
               "AUC": auc(d[TARGET], d["pd"]), "Gini": gini(d[TARGET], d["pd"]), "KS": ks_statistic(d[TARGET], d["pd"]),
               "score_psi": score_psi(train_scores, d["score"]), "band_psi": band_psi(bands_ref, d["band"])}
        for b in BAND_NAMES:
            g = d[d["band"] == b]
            row[f"rate_{b}"] = g[TARGET].mean() if len(g) else np.nan
        rows.append(row)
    t = pd.DataFrame(rows)
    t["score_status"] = t["score_psi"].map(psi_status)
    return t


def monotonic_by_year(yt: pd.DataFrame) -> dict[int, bool]:
    cols = [f"rate_{b}" for b in BAND_NAMES]
    return {int(r["issue_year"]): bool(np.all(np.diff(r[cols].to_numpy(float)) > 0)) for _, r in yt.iterrows()}


# ------------------------------------------------------------------ charts
def make_charts(deciles: pd.DataFrame, fpsi: pd.DataFrame, yt: pd.DataFrame, fig_dir: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig_dir.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(8, 4))
    w = 0.38
    x = deciles["decile"].to_numpy()
    ax.bar(x - w / 2, deciles["train"] * 100, w, label="Train", color="#9aa5b1")
    ax.bar(x + w / 2, deciles["test"] * 100, w, label="Test", color="#1f6feb")
    ax.axhline(10, color="black", lw=0.8, ls="--")
    ax.set_xticks(x)
    ax.set_xlabel("Score decile (1 = lowest scores, riskiest)")
    ax.set_ylabel("% of loans")
    ax.set_title("Score distribution, train vs test")
    ax.legend()
    fig.tight_layout()
    fig.savefig(fig_dir / "psi_score_distribution.png", dpi=150)
    plt.close(fig)

    top = fpsi.head(12).iloc[::-1]
    key = "test" if "test" in top else top.columns[-1]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.barh(top["feature"], top[key], color=["#d1242f" if v >= MODERATE else "#bf8700" if v >= STABLE else "#2da44e" for v in top[key]])
    ax.axvline(STABLE, color="grey", ls="--", lw=0.8)
    ax.axvline(MODERATE, color="grey", ls="--", lw=0.8)
    ax.set_xlabel("PSI, test vs train")
    ax.set_title("Feature stability (largest shifts)")
    fig.tight_layout()
    fig.savefig(fig_dir / "psi_features.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 4))
    for b in BAND_NAMES:
        ax.plot(yt["issue_year"], yt[f"rate_{b}"] * 100, marker="o", label=f"Band {b}")
    ax.set_xticks(yt["issue_year"])
    ax.set_xlabel("Issue year")
    ax.set_ylabel("Default rate, %")
    ax.set_title("Default rate by risk band and issue year")
    ax.legend(ncol=5, fontsize=8)
    fig.tight_layout()
    fig.savefig(fig_dir / "psi_band_rates_by_year.png", dpi=150)
    plt.close(fig)


# ------------------------------------------------------------------ report
def _md(df: pd.DataFrame, fmt: dict[str, str]) -> str:
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, r in df.iterrows():
        out.append("| " + " | ".join(fmt.get(c, "{}").format(r[c]) for c in cols) + " |")
    return "\n".join(out)


def write_report(path: Path, summary: dict, deciles: pd.DataFrame, fpsi: pd.DataFrame, yt: pd.DataFrame, mono: dict) -> None:
    s = summary
    f_cols = [c for c in fpsi.columns if c not in ("feature", "iv_train")]
    ft = fpsi.copy()
    ft["status (test)"] = ft["test"].map(psi_status) if "test" in ft else ""
    yv = yt[["issue_year", "data", "loans", "default_rate", "avg_predicted_pd", "AUC", "Gini", "KS", "score_psi", "score_status"]].copy()
    bt = yt[["issue_year"] + [f"rate_{b}" for b in BAND_NAMES] + ["band_psi"]].copy()
    bt["bands rise A to E"] = bt["issue_year"].map(lambda y: "yes" if mono[int(y)] else "no")
    fmt3 = {c: "{:.3f}" for c in ["AUC", "Gini", "KS", "score_psi", "band_psi", "iv_train"] + f_cols}
    pct = {c: "{:.2%}" for c in ["default_rate", "avg_predicted_pd"] + [f"rate_{b}" for b in BAND_NAMES]}
    dt = deciles.copy()
    dfmt = {c: "{:.1%}" for c in dt.columns if c != "decile"}
    dfmt["decile"] = "{:.0f}"
    lines = [
        "# Scorecard stability check (PSI)",
        "",
        "Generated by `src/scorecard_stability.py`. Metric definition: M56 in `metric_dictionary.md`.",
        "",
        "PSI compares how loans are spread across score ranges (or feature bins) in a later period with the train period. "
        "Under 0.10 = stable, 0.10 to 0.25 = moderate shift, over 0.25 = large shift. All bin edges come from the train data only.",
        "",
        "## 1. Headline",
        "",
        f"- Score PSI, test vs train (10 train deciles): **{s['score_psi_test']:.3f}** ({psi_status(s['score_psi_test'])}).",
        f"- Score PSI, test vs train (5 risk bands): **{s['band_psi_test']:.3f}** ({psi_status(s['band_psi_test'])}).",
        f"- Features measured: {s['n_features']}; stable {s['n_stable']}, moderate {s['n_moderate']}, large {s['n_large']} (test vs train).",
        f"- Largest feature shift: `{s['top_feature']}` at {s['top_feature_psi']:.3f}.",
        f"- Gini {'fell' if s['gini_test'] < s['gini_train'] else 'rose'} from {s['gini_train']:.3f} (train) to {s['gini_test']:.3f} (test); "
        f"predicted default rate {s['pred_test']:.2%} vs actual {s['actual_test']:.2%} in the test year.",
        "",
        "## 2. Score distribution (train deciles)",
        "",
        "If the population were unchanged every cell would be about 10%.",
        "",
        _md(dt, dfmt),
        "",
        "![Score distribution](screenshots/psi_score_distribution.png)".replace("screenshots", FIG_DIR),
        "",
        "## 3. Performance and score PSI by issue year",
        "",
        _md(yv, {**fmt3, **pct}),
        "",
        "## 4. Default rate by band and year",
        "",
        _md(bt, {**fmt3, **pct}),
        "",
        "![Band rates by year](screenshots/psi_band_rates_by_year.png)".replace("screenshots", FIG_DIR),
        "",
        "## 5. Feature PSI against train",
        "",
        _md(ft, fmt3),
        "",
        "![Feature PSI](screenshots/psi_features.png)".replace("screenshots", FIG_DIR),
        "",
        "## 6. How to read this",
        "",
        "- A high score PSI says the borrower mix changed. It does not say the model is wrong: check the bands still rise "
        "from A to E (section 4) and the Gini (section 3).",
        "- A high feature PSI points to what changed. If the shift is in a feature with a large IV, the model leans on it, so it matters more.",
        "- The calibration gap (predicted vs actual default rate) is a separate issue from stability: the ranking can stay good "
        "while the level drifts. The usual remedy is to recalibrate the intercept on recent loans.",
        "- Decision rule suggested for the board pack: PSI under 0.10 keep; 0.10 to 0.25 watch and re-check next quarter; over 0.25 investigate and consider refit.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


# ------------------------------------------------------------------ run
def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, keep_default_na=False, na_values=[""])


def run(scored_path: Path, train_path: Path, test_path: Path, binning_path: Path, model_path: Path,
        model_dir: Path, docs_dir: Path) -> dict:
    scored = _read(scored_path)
    train_s, test_s = scored[scored["data"] == "train"], scored[scored["data"] == "test"]
    train, test = _read(train_path), _read(test_path)
    specs = json.loads(binning_path.read_text(encoding="utf-8"))["features"]
    model = json.loads(model_path.read_text(encoding="utf-8"))
    feats = list(model["coefficients"])

    tr_scores = train_s["score"].to_numpy()
    sets = {str(int(y)): d for y, d in pd.concat([train, test]).groupby("issue_year")}
    sets["test"] = test
    fpsi = feature_psi(train, sets, specs, feats)
    dec = decile_table(tr_scores, {"test": test_s["score"]})
    yt = year_table(scored, tr_scores, train_s["band"])
    mono = monotonic_by_year(yt)

    key = fpsi["test"]
    top = fpsi.iloc[0]
    summary = {
        "score_psi_test": score_psi(tr_scores, test_s["score"]),
        "band_psi_test": band_psi(train_s["band"], test_s["band"]),
        "n_features": len(fpsi),
        "n_stable": int((key < STABLE).sum()),
        "n_moderate": int(((key >= STABLE) & (key < MODERATE)).sum()),
        "n_large": int((key >= MODERATE).sum()),
        "top_feature": str(top["feature"]), "top_feature_psi": float(top["test"]),
        "gini_train": gini(train_s[TARGET], train_s["pd"]), "gini_test": gini(test_s[TARGET], test_s["pd"]),
        "pred_test": float(test_s["pd"].mean()), "actual_test": float(test_s[TARGET].mean()),
        "bands_monotonic_by_year": {str(k): v for k, v in mono.items()},
        "thresholds": {"stable_below": STABLE, "large_from": MODERATE},
    }
    summary["status"] = psi_status(summary["score_psi_test"])

    model_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "scorecard_stability.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    fpsi.round(6).to_csv(docs_dir / "psi_features.csv", index=False)
    yt.round(6).to_csv(docs_dir / "psi_by_year.csv", index=False)
    dec.round(6).to_csv(docs_dir / "psi_score_deciles.csv", index=False)
    make_charts(dec, fpsi, yt, docs_dir / FIG_DIR)
    write_report(docs_dir / "scorecard_stability_report.md", summary, dec, fpsi, yt, mono)
    return {"summary": summary, "features": fpsi, "years": yt, "deciles": dec, "monotonic": mono}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p = ROOT / "data" / "processed"
    ap.add_argument("--scored", type=Path, default=p / "lc_scored.csv")
    ap.add_argument("--train", type=Path, default=p / "lc_train.csv")
    ap.add_argument("--test", type=Path, default=p / "lc_test.csv")
    ap.add_argument("--binning", type=Path, default=ROOT / "models" / "woe_binning.json")
    ap.add_argument("--model", type=Path, default=ROOT / "models" / "scorecard_model.json")
    ap.add_argument("--model-dir", type=Path, default=ROOT / "models")
    ap.add_argument("--docs-dir", type=Path, default=ROOT / "docs")
    a = ap.parse_args()
    r = run(a.scored, a.train, a.test, a.binning, a.model, a.model_dir, a.docs_dir)
    s = r["summary"]
    print(f"Score PSI, test vs train: {s['score_psi_test']:.3f} ({s['status']}); bands: {s['band_psi_test']:.3f}")
    print(f"Features: {s['n_stable']} stable, {s['n_moderate']} moderate, {s['n_large']} large; top {s['top_feature']} {s['top_feature_psi']:.3f}")
    print(r["years"][["issue_year", "loans", "default_rate", "AUC", "Gini", "KS", "score_psi"]].to_string(
        index=False, float_format=lambda v: f"{v:.3f}"))
    print(f"\nReport: {a.docs_dir / 'scorecard_stability_report.md'}")


if __name__ == "__main__":
    main()
