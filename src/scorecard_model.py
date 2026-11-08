"""Credit scorecard: logistic regression on WoE features, points, AUC / KS / Gini and risk bands.

Input : data/processed/lc_train.csv, lc_test.csv   (cleaned loans, from clean_lending_club.py)
        models/woe_binning.json                    (bins and WoE values, from woe_binning.py)
Output: models/scorecard_model.json                coefficients, scaling, band cut points
        docs/scorecard_points.csv                  points for every bin of every feature in the model
        docs/scorecard_metrics.csv                 AUC, Gini, KS on train and test, plus benchmarks
        docs/scorecard_bands.csv                   default rate per risk band (train and test)
        docs/scorecard_report.md                   readable report; charts go to docs/screenshots/
        data/processed/lc_scored.csv               every loan with score, PD and band (git-ignored)

Method
  1. Features: the IV-selected features from woe_binning.py. Where two features move together
     (WoE correlation of 0.70 or more) only the one with the higher IV is kept.
  2. Logistic regression of default on the WoE values, fitted on the TRAIN loans (2013-2014) only.
     Features are then removed one at a time while any coefficient has the wrong sign (WoE is ln(good/bad), so
     every coefficient must be negative) or a p-value above 0.05.
  3. Scorecard points (industry convention): score = Offset + Factor x ln(odds of not defaulting), with
     Factor = PDO / ln 2. Default: 600 points at odds of 50 to 1, and every +20 points doubles the odds.
     Higher score = safer. The intercept is shared equally across features so each bin carries its own points.
  4. Metrics (docs/metric_dictionary.md): AUC (M52), Gini = 2 x AUC - 1 (M53), KS (M54), on train and on the
     2015 test loans. Lending Club's own sub-grade is shown as a benchmark.
  5. Risk bands (M55): five bands A (safest) to E, cut at the 20th, 40th, 60th and 80th percentile of TRAIN scores
     and applied unchanged to the test loans. The default rate should rise steadily from A to E.

    python src/scorecard_model.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, roc_curve

from clean_lending_club import TARGET
from woe_binning import _labels, correlated_pairs, transform_all

ROOT = Path(__file__).resolve().parents[1]
FIG_DIR = "screenshots"      # charts are written to docs/<FIG_DIR>/ ; change here if you rename the folder
BASE_SCORE, BASE_ODDS, PDO = 600, 50, 20
P_VALUE_MAX, CORR_MAX, N_BANDS = 0.05, 0.70, 5
BAND_NAMES = ["A", "B", "C", "D", "E"]          # A = lowest risk


# ------------------------------------------------------------------ metrics (M52-M54)
def auc(y, pd_or_risk) -> float:
    """Area under the ROC curve; input is anything that is higher for riskier loans."""
    return float(roc_auc_score(y, pd_or_risk))


def gini(y, pd_or_risk) -> float:
    return 2 * auc(y, pd_or_risk) - 1


def ks_statistic(y, pd_or_risk) -> float:
    """Largest gap between the cumulative share of defaults and of non-defaults across the ranking."""
    y, r = np.asarray(y), np.asarray(pd_or_risk, float)
    return float(stats.ks_2samp(r[y == 1], r[y == 0]).statistic)


# ------------------------------------------------------------------ logistic regression with p-values
def fit_logit(X: pd.DataFrame, y: pd.Series) -> dict:
    """Unpenalised logistic regression; p-values from the Wald test (no extra library needed)."""
    # C = 1e10 is "no regularisation" in practice (same coefficients as C = infinity). A finite C is used on purpose:
    # penalty=None and C=np.inf both trigger a UserWarning in scikit-learn 1.8.
    m = LogisticRegression(C=1e10, solver="lbfgs", max_iter=5000, tol=1e-8)
    m.fit(X.to_numpy(), y.to_numpy())
    X1 = np.column_stack([np.ones(len(X)), X.to_numpy()])
    p = m.predict_proba(X.to_numpy())[:, 1]
    cov = np.linalg.inv((X1 * (p * (1 - p))[:, None]).T @ X1)
    beta = np.concatenate([m.intercept_, m.coef_[0]])
    se = np.sqrt(np.diag(cov))
    z = beta / se
    pval = 2 * (1 - stats.norm.cdf(np.abs(z)))
    names = ["intercept"] + list(X.columns)
    return {"coef": dict(zip(names, beta)), "se": dict(zip(names, se)), "p": dict(zip(names, pval)),
            "model": m}


def prune_by_correlation(woe: pd.DataFrame, features: list[str], iv: dict, threshold: float = CORR_MAX):
    """Greedy: go through features from highest IV down, keep one only if it is not correlated with a kept one."""
    kept, dropped = [], {}
    for f in sorted(features, key=lambda c: -iv[c]):
        corr = [(k, float(woe[f"woe_{f}"].corr(woe[f"woe_{k}"]))) for k in kept]
        hit = [(k, r) for k, r in corr if abs(r) >= threshold]
        if hit:
            k, r = max(hit, key=lambda t: abs(t[1]))
            dropped[f] = f"correlated {r:+.2f} with `{k}` (higher IV)"
        else:
            kept.append(f)
    return kept, dropped


def select_and_fit(woe_train: pd.DataFrame, y: pd.Series, features: list[str], iv: dict):
    """Correlation pruning, then backward elimination on wrong sign / p-value. Returns fit, features, log."""
    log: list[tuple[str, str]] = []
    feats, dropped = prune_by_correlation(woe_train, features, iv)
    log += [(f, why) for f, why in dropped.items()]
    while True:
        fit = fit_logit(woe_train[[f"woe_{f}" for f in feats]], y)
        wrong = [f for f in feats if fit["coef"][f"woe_{f}"] > 0]
        weak = [f for f in feats if fit["p"][f"woe_{f}"] > P_VALUE_MAX]
        if wrong:
            f = max(wrong, key=lambda c: fit["coef"][f"woe_{c}"])
            log.append((f, f"wrong sign: coefficient {fit['coef'][f'woe_{f}']:+.3f} (must be negative)"))
        elif weak:
            f = max(weak, key=lambda c: fit["p"][f"woe_{c}"])
            log.append((f, f"p-value {fit['p'][f'woe_{f}']:.3f} above {P_VALUE_MAX}"))
        else:
            return fit, feats, log
        feats = [c for c in feats if c != f]
        if not feats:
            raise ValueError("no feature survived the selection")


# ------------------------------------------------------------------ scaling and points
def scaling() -> tuple[float, float]:
    factor = PDO / np.log(2)
    offset = BASE_SCORE - factor * np.log(BASE_ODDS)
    return float(factor), float(offset)


def score_from_logit(logit_default) -> np.ndarray:
    """Exact (unrounded) score for the model's log-odds of default."""
    factor, offset = scaling()
    return offset - factor * np.asarray(logit_default, float)


def feature_points(woe: pd.Series | float, coef: float, intercept: float, n_features: int):
    factor, offset = scaling()
    return np.round(offset / n_features - factor * (coef * np.asarray(woe, float) + intercept / n_features))


def score_woe_frame(woe: pd.DataFrame, model: dict) -> pd.Series:
    """Integer score = sum of the points of each feature's bin."""
    total = 0
    for f, c in model["coefficients"].items():
        total = total + feature_points(woe[f"woe_{f}"], c, model["intercept"], len(model["coefficients"]))
    return pd.Series(total, index=woe.index).astype(int)


def points_table(specs: dict, model: dict) -> pd.DataFrame:
    rows = []
    n = len(model["coefficients"])
    for f, c in model["coefficients"].items():
        spec = specs[f]
        labels = _labels(spec) + (["Missing"] if spec["has_missing_bin"] else [])
        woes = spec["woe"] + ([spec["missing_woe"]] if spec["has_missing_bin"] else [])
        for i, (lab, w) in enumerate(zip(labels, woes), 1):
            rows.append({"feature": f, "bin": i, "range": lab, "woe": w, "coefficient": c,
                         "points": int(feature_points(w, c, model["intercept"], n))})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ bands (M55)
def band_cuts(train_scores: pd.Series) -> list[float]:
    return [float(np.percentile(train_scores, q)) for q in np.linspace(0, 100, N_BANDS + 1)[1:-1]]


def assign_band(scores: pd.Series, cuts: list[float]) -> pd.Series:
    idx = np.searchsorted(np.array(cuts), scores.to_numpy(float), side="right")      # 0 = lowest scores = riskiest
    names = BAND_NAMES[::-1]                                                          # E ... A
    return pd.Series(np.array(names)[idx], index=scores.index)


def band_table(df: pd.DataFrame, cuts: list[float]) -> pd.DataFrame:
    rows = []
    lows = [-np.inf] + cuts
    highs = cuts + [np.inf]
    for i, name in enumerate(BAND_NAMES[::-1]):
        g = df[df["band"] == name]
        lo, hi = lows[i], highs[i]
        rng = (f"below {hi:.0f}" if np.isinf(lo) else f"{lo:.0f} and above" if np.isinf(hi) else f"{lo:.0f} to {hi - 1:.0f}")
        rows.append({"band": name, "score_range": rng, "loans": len(g), "share": len(g) / len(df),
                     "defaults": int(g[TARGET].sum()), "default_rate": float(g[TARGET].mean()) if len(g) else np.nan,
                     "avg_predicted_pd": float(g["pd"].mean()) if len(g) else np.nan,
                     "avg_score": float(g["score"].mean()) if len(g) else np.nan})
    t = pd.DataFrame(rows).iloc[::-1].reset_index(drop=True)      # A first
    t["lift"] = t["default_rate"] / df[TARGET].mean()
    return t


# ------------------------------------------------------------------ benchmarks
def subgrade_rank(s: pd.Series) -> pd.Series:
    """A1 = 1 ... G5 = 35; higher means riskier."""
    letter = s.astype(str).str.upper().str[0].map(lambda c: ord(c) - 64)
    number = pd.to_numeric(s.astype(str).str[1:], errors="coerce")
    return (letter - 1) * 5 + number


def metrics_table(scored: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for name, d in scored.items():
        rows.append({"set": f"Scorecard, {name}", "loans": len(d), "default_rate": d[TARGET].mean(),
                     "AUC": auc(d[TARGET], d["pd"]), "Gini": gini(d[TARGET], d["pd"]),
                     "KS": ks_statistic(d[TARGET], d["pd"])})
    for name, d in scored.items():
        if name != "test":
            continue
        for label, col in (("Lending Club sub_grade (benchmark)", "sub_grade_rank"), ("Lending Club int_rate (benchmark)", "int_rate"),
                           ("FICO score alone (benchmark)", "fico_risk")):
            if col in d and d[col].notna().all():
                rows.append({"set": f"{label}, {name}", "loans": len(d), "default_rate": d[TARGET].mean(),
                             "AUC": auc(d[TARGET], d[col]), "Gini": gini(d[TARGET], d[col]),
                             "KS": ks_statistic(d[TARGET], d[col])})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ charts
def make_charts(scored: dict[str, pd.DataFrame], bands: dict[str, pd.DataFrame], fig_dir: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig_dir.mkdir(parents=True, exist_ok=True)
    navy, red, grey = "#1F3864", "#C62828", "#8A94A6"
    test = scored["test"]

    fig, ax = plt.subplots(figsize=(5.5, 5))
    for label, y, r, c, ls in (("Scorecard, test", test[TARGET], test["pd"], navy, "-"),
                               ("Scorecard, train", scored["train"][TARGET], scored["train"]["pd"], navy, ":"),
                               ("Lending Club sub-grade, test", test[TARGET], test.get("sub_grade_rank"), red, "--")):
        if r is None or r.isna().any():
            continue
        fpr, tpr, _ = roc_curve(y, r)
        ax.plot(fpr, tpr, color=c, ls=ls, label=f"{label} (AUC {auc(y, r):.3f})")
    ax.plot([0, 1], [0, 1], color=grey, lw=0.8)
    ax.set_xlabel("Share of non-defaults flagged as risky")
    ax.set_ylabel("Share of defaults caught")
    ax.set_title("ROC curve")
    ax.legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    fig.savefig(fig_dir / "scorecard_roc.png", dpi=150)
    plt.close(fig)

    t = test.sort_values("score")
    cum_bad = np.cumsum(t[TARGET].to_numpy()) / t[TARGET].sum()
    cum_good = np.cumsum(1 - t[TARGET].to_numpy()) / (1 - t[TARGET]).sum()
    k = int(np.argmax(np.abs(cum_bad - cum_good)))
    fig, ax = plt.subplots(figsize=(6, 4.5))
    ax.plot(t["score"].to_numpy(), cum_bad, color=red, label="Defaults")
    ax.plot(t["score"].to_numpy(), cum_good, color=navy, label="Non-defaults")
    ax.vlines(t["score"].iloc[k], cum_good[k], cum_bad[k], color="black", lw=1.2)
    ax.text(t["score"].iloc[k], (cum_good[k] + cum_bad[k]) / 2, f"  KS = {abs(cum_bad[k] - cum_good[k]):.3f}", fontsize=9)
    ax.set_xlabel("Score (low = risky)")
    ax.set_ylabel("Cumulative share of loans")
    ax.set_title("KS chart, test loans (2015)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(fig_dir / "scorecard_ks.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 4.5))
    x = np.arange(len(BAND_NAMES))
    ax.bar(x - 0.2, bands["train"]["default_rate"] * 100, 0.4, color=grey, label="Train 2013-14")
    ax.bar(x + 0.2, bands["test"]["default_rate"] * 100, 0.4, color=navy, label="Test 2015")
    for i, v in enumerate(bands["test"]["default_rate"] * 100):
        ax.text(i + 0.2, v + 0.3, f"{v:.1f}%", ha="center", fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{b}\n{r}" for b, r in zip(bands["test"]["band"], bands["test"]["score_range"])], fontsize=8)
    ax.set_ylabel("Default rate (%)")
    ax.set_title("Default rate rises from the safest band (A) to the riskiest (E)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(fig_dir / "scorecard_bands.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 4.2))
    bins = np.linspace(test["score"].min(), test["score"].max(), 40)
    ax.hist(test.loc[test[TARGET] == 0, "score"], bins=bins, alpha=0.65, color=navy, label="Non-defaults", density=True)
    ax.hist(test.loc[test[TARGET] == 1, "score"], bins=bins, alpha=0.65, color=red, label="Defaults", density=True)
    ax.set_xlabel("Score")
    ax.set_ylabel("Density")
    ax.set_title("Score distribution, test loans: defaults sit lower")
    ax.legend()
    fig.tight_layout()
    fig.savefig(fig_dir / "scorecard_distribution.png", dpi=150)
    plt.close(fig)


# ------------------------------------------------------------------ report
def write_report(path: Path, model: dict, fit: dict, log: list, metrics: pd.DataFrame, bands: dict, points: pd.DataFrame,
                 iv: dict, calib: dict) -> None:
    f, o = scaling()
    L = ["# Credit scorecard", "",
         "Written by `src/scorecard_model.py`. Logistic regression on weight-of-evidence features, fitted on loans issued "
         "2013-2014 and tested on loans issued 2015. Higher score = safer borrower. "
         "Definitions: M52 (AUC), M53 (Gini), M54 (KS), M55 (default rate by band) in `docs/metric_dictionary.md`.", "",
         "## 1. Model quality", "", "| Model / data | Loans | Default rate | AUC | Gini | KS |", "|---|---|---|---|---|---|"]
    L += [f"| {r.set} | {r.loans:,} | {r.default_rate:.2%} | {r.AUC:.3f} | {r.Gini:.3f} | {r.KS:.3f} |" for r in metrics.itertuples()]
    L += ["", "AUC is the chance a random defaulter is ranked riskier than a random non-defaulter (0.5 = no skill). "
          "Gini = 2 x AUC - 1. KS is the largest gap between the cumulative shares of defaults and non-defaults.",
          "The benchmark rows show how Lending Club's own sub-grade, which uses extra information we deliberately leave out, "
          "ranks the same loans.", "",
          "![ROC curve](" + FIG_DIR + "/scorecard_roc.png)", "![KS chart](" + FIG_DIR + "/scorecard_ks.png)", "",
          "## 2. Calibration across time", "",
          f"| Loans | Actual default rate | Average predicted default rate |", "|---|---|---|",
          f"| Train (2013-2014) | {calib['train_actual']:.2%} | {calib['train_pred']:.2%} |",
          f"| Test (2015) | {calib['test_actual']:.2%} | {calib['test_pred']:.2%} |", "",
          "The scorecard ranks borrowers; it does not know that later loan years default more often. When the test row "
          "shows actual above predicted, the PD should be read as a relative ranking, or recalibrated on recent data "
          "before it is used as an absolute probability.", "",
          "## 3. Risk bands", "",
          "Bands are cut at the 20th, 40th, 60th and 80th percentile of the train scores and applied unchanged to the test loans.", ""]
    for name in ("test", "train"):
        b = bands[name]
        L += [f"**{name.capitalize()} loans**", "", "| Band | Score range | Loans | Share | Defaults | Default rate | Lift vs average |",
              "|---|---|---|---|---|---|---|"]
        L += [f"| {r.band} | {r.score_range} | {r.loans:,} | {r.share:.1%} | {r.defaults:,} | {r.default_rate:.1%} | {r.lift:.2f}x |"
              for r in b.itertuples()]
        steady = bool(np.all(np.diff(b["default_rate"]) > 0))
        L += ["", f"Default rate rises steadily from A to E: **{'yes' if steady else 'NO, check the bands'}**.", ""]
    L += ["![Default rate by band](" + FIG_DIR + "/scorecard_bands.png)", "![Score distribution](" + FIG_DIR + "/scorecard_distribution.png)", "",
          "## 4. Model features and coefficients", "",
          f"{len(model['coefficients'])} features in the final model. Every coefficient is negative: a higher WoE (safer bin) lowers the "
          "chance of default.", "", "| Feature | IV | Coefficient | Std. error | p-value |", "|---|---|---|---|---|"]
    for ft, c in sorted(model["coefficients"].items(), key=lambda kv: kv[1]):
        L.append(f"| `{ft}` | {iv[ft]:.4f} | {c:+.3f} | {fit['se']['woe_' + ft]:.3f} | {fit['p']['woe_' + ft]:.4f} |")
    L += [f"| intercept | | {model['intercept']:+.3f} | {fit['se']['intercept']:.3f} | {fit['p']['intercept']:.4f} |", "",
          "### Features removed while building the model", "", "| Feature | Reason |", "|---|---|"]
    L += [f"| `{ft}` | {why} |" for ft, why in log] or ["| none | |"]
    L += ["", "## 5. Scaling", "",
          f"Score = {o:.2f} - {f:.2f} x (log-odds of default). {BASE_SCORE} points at odds of {BASE_ODDS} to 1 (non-default to default); "
          f"every {PDO} points doubles the odds. Points per bin are in `docs/scorecard_points.csv`; the score of a loan is the sum of the "
          f"points of the bin it falls in for each of the {len(model['coefficients'])} features.", "",
          "## 6. Points of the strongest features", ""]
    for ft in sorted(model["coefficients"], key=lambda c: -iv[c])[:4]:
        p = points[points.feature == ft]
        L += [f"**`{ft}`**", "", "| Bin | Range | WoE | Points |", "|---|---|---|---|"]
        L += [f"| {r.bin} | {r.range} | {r.woe:+.3f} | {r.points} |" for r in p.itertuples()]
        L.append("")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ pipeline
def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, keep_default_na=False, na_values=[""])


def run(train_path: Path, test_path: Path, binning_path: Path, model_dir: Path, docs_dir: Path, data_dir: Path) -> dict:
    train, test = _read(train_path), _read(test_path)
    spec_file = json.loads(binning_path.read_text(encoding="utf-8"))
    specs, selected = spec_file["features"], spec_file["selected"]
    iv = {f: specs[f]["iv"] for f in specs}
    woe_tr, woe_te = transform_all(train, specs), transform_all(test, specs)

    fit, feats, log = select_and_fit(woe_tr, train[TARGET], selected, iv)
    model = {"intercept": float(fit["coef"]["intercept"]),
             "coefficients": {f: float(fit["coef"][f"woe_{f}"]) for f in feats},
             "scaling": {"base_score": BASE_SCORE, "base_odds": BASE_ODDS, "pdo": PDO}}

    scored = {}
    for name, df, woe in (("train", train, woe_tr), ("test", test, woe_te)):
        X = woe[[f"woe_{f}" for f in feats]].to_numpy()
        p = fit["model"].predict_proba(X)[:, 1]
        d = pd.DataFrame({"id": df["id"].to_numpy(), TARGET: df[TARGET].to_numpy(), "issue_year": df["issue_year"].to_numpy(),
                          "pd": p})
        d["score"] = score_woe_frame(woe, model).to_numpy()
        for col in ("grade", "sub_grade", "int_rate"):
            if col in df:
                d[col] = df[col].to_numpy()
        if "sub_grade" in d:
            d["sub_grade_rank"] = subgrade_rank(d["sub_grade"])
        if "fico_avg" in df:
            d["fico_risk"] = -df["fico_avg"].to_numpy()
        scored[name] = d

    cuts = band_cuts(scored["train"]["score"])
    for d in scored.values():
        d["band"] = assign_band(d["score"], cuts)
    bands = {k: band_table(d, cuts) for k, d in scored.items()}
    metrics = metrics_table(scored)
    points = points_table(specs, model)
    calib = {"train_actual": scored["train"][TARGET].mean(), "train_pred": scored["train"]["pd"].mean(),
             "test_actual": scored["test"][TARGET].mean(), "test_pred": scored["test"]["pd"].mean()}
    model["band_cuts"] = cuts
    model["band_names_best_to_worst"] = BAND_NAMES
    model["train_years"] = sorted(map(int, train["issue_year"].unique()))

    for d in (model_dir, docs_dir, data_dir):
        d.mkdir(parents=True, exist_ok=True)
    (model_dir / "scorecard_model.json").write_text(json.dumps(model, indent=2), encoding="utf-8")
    points.to_csv(docs_dir / "scorecard_points.csv", index=False)
    metrics.round(6).to_csv(docs_dir / "scorecard_metrics.csv", index=False)
    pd.concat([b.assign(data=k) for k, b in bands.items()]).round(6).to_csv(docs_dir / "scorecard_bands.csv", index=False)
    pd.concat([d.assign(data=k) for k, d in scored.items()]).to_csv(data_dir / "lc_scored.csv", index=False)
    make_charts(scored, bands, docs_dir / FIG_DIR)
    write_report(docs_dir / "scorecard_report.md", model, fit, log, metrics, bands, points, iv, calib)
    return {"model": model, "fit": fit, "log": log, "scored": scored, "bands": bands, "metrics": metrics,
            "points": points, "calib": calib, "cuts": cuts}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--train", type=Path, default=ROOT / "data" / "processed" / "lc_train.csv")
    ap.add_argument("--test", type=Path, default=ROOT / "data" / "processed" / "lc_test.csv")
    ap.add_argument("--binning", type=Path, default=ROOT / "models" / "woe_binning.json")
    ap.add_argument("--model-dir", type=Path, default=ROOT / "models")
    ap.add_argument("--docs-dir", type=Path, default=ROOT / "docs")
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data" / "processed")
    a = ap.parse_args()
    r = run(a.train, a.test, a.binning, a.model_dir, a.docs_dir, a.data_dir)
    print(f"Features in the model ({len(r['model']['coefficients'])}): {', '.join(r['model']['coefficients'])}")
    print(f"Removed: {[f for f, _ in r['log']]}")
    print(r["metrics"].to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    print("\nTest loans by risk band:")
    print(r["bands"]["test"][["band", "score_range", "loans", "default_rate", "lift"]].to_string(
        index=False, float_format=lambda v: f"{v:.3f}"))
    print(f"\nReport: {a.docs_dir / 'scorecard_report.md'}")


if __name__ == "__main__":
    main()
