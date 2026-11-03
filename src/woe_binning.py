"""Weight of evidence (WoE) binning and information value (IV) for the credit scorecard.

Input : data/processed/lc_train.csv and lc_test.csv   (written by clean_lending_club.py)
Output: models/woe_binning.json            fitted bins and WoE values (used to score any new loan)
        docs/woe_bins.csv                  every bin: range, loans, defaults, default rate, WoE, IV share
        docs/iv_summary.csv                one row per feature: IV on train, IV on test, strength, selected
        docs/woe_iv_report.md              readable report
        docs/screenshots/iv_ranking.png, woe_top_features.png
        data/processed/lc_train_woe.csv, lc_test_woe.csv   features replaced by their WoE values

Definitions (docs/metric_dictionary.md, M50 and M51):
    WoE of a bin = ln( share of all non-defaults in the bin / share of all defaults in the bin )
    IV of a feature = sum over bins of (share non-defaults - share defaults) x WoE
A positive WoE means a bin with fewer defaults than average (safer); negative means riskier.
A constant of 0.5 is added to every bin's counts so an empty cell never gives an infinite WoE.

How the bins are made (all on the TRAIN loans only):
  * Numeric: start from up to 20 quantile bins, then merge neighbouring bins, always the pair that loses the
    least IV, until (a) every bin holds at least 5% of loans, (b) the default rate moves in ONE direction
    (monotonic WoE, so the scorecard is easy to explain), and (c) there are at most 8 bins.
    Both directions are tried and the one that keeps more IV is used.
  * Categorical: order the categories by default rate and merge neighbours the same way (at least 3% of loans
    per bin, at most 8 bins, no monotonic rule).
  * Blank values ("Missing") get their own bin when they are at least 1% of train; below that they join the bin
    with the nearest default rate.
  * A category never seen in train goes to the bin whose WoE is closest to zero (average risk).

Screening: IV below 0.02 = not useful; 0.02-0.5 = selected; above 0.5 = flagged as suspicious (check for leakage).
IV is also computed on the test loans with the SAME bins, as a stability check.

    python src/woe_binning.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from clean_lending_club import REFERENCE, TARGET

ROOT = Path(__file__).resolve().parents[1]
EPS = 0.5
SETTINGS = {"pre_bins": 20, "min_share_numeric": 0.05, "min_share_categorical": 0.03, "max_bins": 8,
            "missing_min_share": 0.01, "iv_min": 0.02, "iv_suspicious": 0.5, "corr_flag": 0.70}
NOT_FEATURES = ["id", TARGET, "issue_year"] + REFERENCE


def iv_strength(iv: float) -> str:
    if iv < 0.02:
        return "not useful"
    if iv < 0.1:
        return "weak"
    if iv < 0.3:
        return "medium"
    if iv < 0.5:
        return "strong"
    return "suspicious"


# ------------------------------------------------------------------ WoE and IV maths
def woe_iv(good, bad) -> tuple[np.ndarray, np.ndarray]:
    """WoE and IV contribution of each bin from counts of non-defaults (good) and defaults (bad)."""
    good, bad = np.asarray(good, float), np.asarray(bad, float)
    k = len(good)
    pg = (good + EPS) / (good.sum() + EPS * k)
    pb = (bad + EPS) / (bad.sum() + EPS * k)
    woe = np.log(pg / pb)
    return woe, (pg - pb) * woe


def _iv(good, bad) -> float:
    return float(woe_iv(good, bad)[1].sum())


def _merge_bins(good, bad, min_count, max_bins, direction):
    """Merge neighbouring bins. direction +1 / -1 forces default rate to rise / fall; None = no rule.
    Returns the list of groups (each a list of original bin positions)."""
    good, bad = list(map(float, good)), list(map(float, bad))
    groups = [[i] for i in range(len(good))]
    while len(good) > 1:
        g, b = np.array(good), np.array(bad)
        tot = g + b
        rates = (b + EPS) / (tot + 2 * EPS)
        small = [i for i in range(len(g)) if tot[i] < min_count]
        wrong = [i for i in range(len(g) - 1) if direction is not None and (rates[i + 1] - rates[i]) * direction < 0]
        if small:
            i = min(small, key=lambda j: tot[j])
            pairs = [p for p in ((i - 1, i), (i, i + 1)) if p[0] >= 0 and p[1] < len(g)]
        elif wrong:
            pairs = [(i, i + 1) for i in wrong]
        elif len(g) > max_bins:
            pairs = [(i, i + 1) for i in range(len(g) - 1)]
        else:
            break

        def loss(p):
            m_g = np.concatenate([g[:p[0]], [g[p[0]] + g[p[1]]], g[p[1] + 1:]])
            m_b = np.concatenate([b[:p[0]], [b[p[0]] + b[p[1]]], b[p[1] + 1:]])
            return -_iv(m_g, m_b)

        a, c = min(pairs, key=loss)
        good[a] += good.pop(c)
        bad[a] += bad.pop(c)
        groups[a] += groups.pop(c)
    return groups


# ------------------------------------------------------------------ fitting one feature
def _fit_numeric(x: pd.Series, y: pd.Series, s: dict) -> dict:
    n = len(x)
    miss = x.isna()
    xv, yv = x[~miss].to_numpy(float), y[~miss].to_numpy(int)
    uniq = np.unique(xv)
    if len(uniq) <= 1:
        return {"type": "numeric", "cuts": [], "constant": True}
    if len(uniq) <= s["pre_bins"]:
        cuts = uniq[:-1]
    else:
        cuts = np.unique(np.quantile(xv, np.linspace(0, 1, s["pre_bins"] + 1)[1:-1]))
    pos = np.searchsorted(cuts, xv, side="left")
    k = len(cuts) + 1
    bad = np.bincount(pos, weights=yv, minlength=k)
    good = np.bincount(pos, minlength=k) - bad
    best = None
    for direction in (1, -1):
        groups = _merge_bins(good, bad, s["min_share_numeric"] * n, s["max_bins"], direction)
        gg = [good[g].sum() for g in groups]
        bb = [bad[g].sum() for g in groups]
        iv = _iv(gg, bb)
        if best is None or iv > best[0]:
            best = (iv, groups)
    groups = best[1]
    final_cuts = [float(cuts[g[-1]]) for g in groups[:-1]]
    return {"type": "numeric", "cuts": final_cuts, "constant": False}


def _fit_categorical(x: pd.Series, y: pd.Series, s: dict) -> dict:
    n = len(x)
    x = x.astype(str)
    tab = pd.DataFrame({"x": x, "y": y.to_numpy()}).groupby("x")["y"].agg(["sum", "count"])
    tab["rate"] = tab["sum"] / tab["count"]
    tab = tab.sort_values(["rate", "count"], ascending=[True, False])
    bad = tab["sum"].to_numpy(float)
    good = (tab["count"] - tab["sum"]).to_numpy(float)
    groups = _merge_bins(good, bad, s["min_share_categorical"] * n, s["max_bins"], None)
    cats = tab.index.to_numpy()
    return {"type": "categorical", "groups": [[str(c) for c in cats[g]] for g in groups], "constant": len(groups) <= 1}


# ------------------------------------------------------------------ assigning bins
def assign_bins(x: pd.Series, spec: dict) -> pd.Series:
    """Bin number for each value; -1 = Missing bin. Uses only the fitted spec."""
    if spec["type"] == "numeric":
        out = pd.Series(np.searchsorted(np.array(spec["cuts"]), x.to_numpy(float), side="left"), index=x.index)
        return out.mask(x.isna(), -1)
    lookup = {c: i for i, g in enumerate(spec["groups"]) for c in g}
    return x.astype(str).map(lookup).fillna(-2).astype(int)        # -2 = never seen in train


def _bin_counts(bins: pd.Series, y: pd.Series, n_bins: int, missing: bool):
    good, bad = np.zeros(n_bins + int(missing)), np.zeros(n_bins + int(missing))
    for b, g in pd.DataFrame({"b": bins.to_numpy(), "y": y.to_numpy()}).groupby("b")["y"]:
        if b >= 0:
            idx = int(b)
        elif b == -1 and missing:
            idx = n_bins
        else:
            continue
        bad[idx], good[idx] = g.sum(), len(g) - g.sum()
    return good, bad


def fit_feature(x: pd.Series, y: pd.Series, s: dict = SETTINGS) -> dict:
    """Fit bins and WoE for one feature on train data."""
    spec = _fit_numeric(x, y, s) if pd.api.types.is_numeric_dtype(x) else _fit_categorical(x, y, s)
    n_bins = len(spec["cuts"]) + 1 if spec["type"] == "numeric" else len(spec["groups"])
    spec["n_bins"] = n_bins
    bins = assign_bins(x, spec)
    has_missing = spec["type"] == "numeric" and bool((bins == -1).any())
    missing_share = float((bins == -1).mean()) if has_missing else 0.0
    keep_missing = has_missing and missing_share >= s["missing_min_share"]
    good, bad = _bin_counts(bins, y, n_bins, keep_missing)
    spec["has_missing_bin"] = bool(keep_missing)
    if has_missing and not keep_missing:                       # tiny: join the nearest default rate
        miss_rate = float(y[bins == -1].mean())
        rates = bad[:n_bins] / np.maximum(good[:n_bins] + bad[:n_bins], 1)
        j = int(np.argmin(np.abs(rates - miss_rate)))
        spec["missing_joins_bin"] = j
        bad[j] += y[bins == -1].sum()
        good[j] += (bins == -1).sum() - y[bins == -1].sum()
    woe, contrib = woe_iv(good, bad)
    spec["woe"] = [float(w) for w in woe[:n_bins]]
    spec["missing_woe"] = float(woe[n_bins]) if keep_missing else (
        spec["woe"][spec["missing_joins_bin"]] if has_missing else None)
    spec["unseen_woe"] = float(spec["woe"][int(np.argmin(np.abs(spec["woe"])))]) if spec["woe"] else 0.0
    spec["iv"] = float(contrib.sum())
    return spec


def transform_feature(x: pd.Series, spec: dict) -> pd.Series:
    """Replace each value by the WoE of its bin."""
    bins = assign_bins(x, spec)
    woe = np.array(spec["woe"] + [0.0])
    out = pd.Series(np.where(bins >= 0, woe[np.clip(bins, 0, len(woe) - 1)], np.nan), index=x.index)
    out[bins == -1] = spec["missing_woe"] if spec["missing_woe"] is not None else spec["unseen_woe"]
    out[bins == -2] = spec["unseen_woe"]
    return out


# ------------------------------------------------------------------ whole table
def feature_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c not in NOT_FEATURES]


def fit_all(train: pd.DataFrame, s: dict = SETTINGS) -> dict:
    y = train[TARGET]
    specs = {}
    for c in feature_columns(train):
        specs[c] = fit_feature(train[c], y, s)
    return specs


def transform_all(df: pd.DataFrame, specs: dict) -> pd.DataFrame:
    out = df[[c for c in ("id", TARGET, "issue_year") if c in df]].copy()
    for c, spec in specs.items():
        out[f"woe_{c}"] = transform_feature(df[c], spec)
    return out


def bin_table(df: pd.DataFrame, specs: dict, label: str = "train") -> pd.DataFrame:
    """One row per bin, with counts taken from df (train or test) using the fitted bins."""
    y = df[TARGET]
    rows = []
    for c, spec in specs.items():
        bins = assign_bins(df[c], spec)
        n_bins = spec["n_bins"]
        has_m = spec["has_missing_bin"]
        good, bad = _bin_counts(bins, y, n_bins, has_m)
        woe, contrib = woe_iv(good, bad)
        fit_woe = spec["woe"] + ([spec["missing_woe"]] if has_m else [])
        labels = _labels(spec) + (["Missing"] if has_m else [])
        for i in range(len(good)):
            tot = good[i] + bad[i]
            rows.append({"feature": c, "bin": i + 1, "range": labels[i], "loans": int(tot),
                         "share": tot / len(df), "defaults": int(bad[i]),
                         "default_rate": bad[i] / tot if tot else np.nan,
                         "woe": float(fit_woe[i]), f"woe_{label}": float(woe[i]), "iv_part": float(contrib[i])})
    return pd.DataFrame(rows)


def _fmt(v: float) -> str:
    return f"{v:.4g}"


def _labels(spec: dict) -> list[str]:
    if spec["type"] == "categorical":
        out = []
        for g in spec["groups"]:
            out.append(", ".join(g) if len(g) <= 4 else ", ".join(g[:3]) + f", ... (+{len(g) - 3})")
        return out
    cuts = spec["cuts"]
    if not cuts:
        return ["all values"]
    out = [f"<= {_fmt(cuts[0])}"]
    out += [f"> {_fmt(cuts[i - 1])} to {_fmt(cuts[i])}" for i in range(1, len(cuts))]
    out.append(f"> {_fmt(cuts[-1])}")
    return out


def iv_summary(train: pd.DataFrame, test: pd.DataFrame, specs: dict, s: dict = SETTINGS) -> pd.DataFrame:
    tr, te = bin_table(train, specs, "train"), bin_table(test, specs, "test")
    rows = []
    for c, spec in specs.items():
        iv_train = spec["iv"]
        # IV on test with the SAME bins: test's own WoE per bin
        iv_test = float(te.loc[te.feature == c].eval("iv_part").sum())
        rows.append({"feature": c, "type": spec["type"], "bins": len(tr[tr.feature == c]), "iv_train": iv_train,
                     "iv_test": iv_test, "strength": iv_strength(iv_train),
                     "missing_bin": spec["has_missing_bin"]})
    df = pd.DataFrame(rows).sort_values("iv_train", ascending=False).reset_index(drop=True)
    df["selected"] = (df["iv_train"] >= s["iv_min"]) & (df["iv_train"] <= s["iv_suspicious"])
    df["note"] = np.where(df["iv_train"] > s["iv_suspicious"], "IV above 0.5: check for leakage",
                          np.where(df["iv_train"] < s["iv_min"], "below 0.02: dropped",
                                   np.where(df["iv_test"] < 0.5 * df["iv_train"], "IV halves on test: unstable", "")))
    return df


def correlated_pairs(woe_train: pd.DataFrame, features: list[str], threshold: float) -> pd.DataFrame:
    cols = [f"woe_{c}" for c in features]
    corr = woe_train[cols].corr()
    rows = [(a[4:], b[4:], float(corr.loc[a, b])) for i, a in enumerate(cols) for b in cols[i + 1:]
            if abs(corr.loc[a, b]) >= threshold]
    return pd.DataFrame(rows, columns=["feature_a", "feature_b", "correlation"]).sort_values(
        "correlation", key=abs, ascending=False)


# ------------------------------------------------------------------ outputs
def make_figures(summary: pd.DataFrame, bins: pd.DataFrame, fig_dir: Path, s: dict = SETTINGS, top: int = 8) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig_dir.mkdir(parents=True, exist_ok=True)
    d = summary.sort_values("iv_train")
    fig, ax = plt.subplots(figsize=(8, max(4, 0.28 * len(d))))
    colors = np.where(d["selected"], "#1F3864", "#B0B7C3")
    ax.barh(d["feature"], d["iv_train"], color=colors)
    ax.axvline(s["iv_min"], color="#C00000", ls="--", lw=1)
    ax.text(s["iv_min"], len(d) - 0.4, " IV = 0.02 cut-off", color="#C00000", fontsize=8, va="bottom")
    ax.set_xlabel("Information value (train loans, 2013-2014)")
    ax.set_title("Which features separate defaults from non-defaults")
    fig.tight_layout()
    fig.savefig(fig_dir / "iv_ranking.png", dpi=150)
    plt.close(fig)

    feats = summary[summary.selected].head(top)["feature"].tolist()
    if not feats:
        return
    cols = 2
    rows = int(np.ceil(len(feats) / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(11, 2.6 * rows), squeeze=False)
    for ax, f in zip(axes.ravel(), feats):
        b = bins[bins.feature == f]
        ax.bar(range(len(b)), b["woe"], color=np.where(b["woe"] >= 0, "#2E7D32", "#C62828"))
        ax.set_xticks(range(len(b)))
        ax.set_xticklabels([r if len(r) < 16 else r[:14] + ".." for r in b["range"]], rotation=35, ha="right", fontsize=7)
        ax.axhline(0, color="black", lw=0.6)
        ax.set_title(f"{f} (IV {summary.set_index('feature').loc[f, 'iv_train']:.3f})", fontsize=9)
        ax.set_ylabel("WoE", fontsize=8)
    for ax in axes.ravel()[len(feats):]:
        ax.axis("off")
    fig.suptitle("WoE by bin: green = fewer defaults than average, red = more", fontsize=10)
    fig.tight_layout()
    fig.savefig(fig_dir / "woe_top_features.png", dpi=150)
    plt.close(fig)


def write_report(path: Path, summary: pd.DataFrame, bins: pd.DataFrame, pairs: pd.DataFrame, n_train: int,
                 n_test: int, s: dict = SETTINGS) -> None:
    sel = summary[summary.selected]
    L = ["# WoE binning and information value", "",
         "Written by `src/woe_binning.py`. Bins and WoE values were learned on the **train** loans (issue years "
         f"2013-2014, {n_train:,} loans) and applied unchanged to the **test** loans (2015, {n_test:,}). "
         "Definitions: M50 (WoE) and M51 (IV) in `docs/metric_dictionary.md`. Every bin: `docs/woe_bins.csv`.", "",
         "## 1. Feature ranking by information value", "",
         f"{len(summary)} features were binned; **{len(sel)} selected** (IV between {s['iv_min']} and {s['iv_suspicious']}).", "",
         "| Rank | Feature | Type | Bins | IV train | IV test | Strength | Selected | Note |", "|---|---|---|---|---|---|---|---|---|"]
    for i, r in summary.iterrows():
        L.append(f"| {i + 1} | `{r.feature}` | {r.type} | {r.bins} | {r.iv_train:.4f} | {r.iv_test:.4f} | {r.strength} | "
                 f"{'yes' if r.selected else 'no'} | {r.note} |")
    L += ["", "How to read IV: under 0.02 not useful, 0.02-0.1 weak, 0.1-0.3 medium, 0.3-0.5 strong, over 0.5 suspicious. "
          "Credit data without the lender's own grade usually gives many weak and a few medium features; that is normal.",
          "", "![IV ranking](screenshots/iv_ranking.png)", "", "## 2. Bins of the strongest features", ""]
    for f in sel.head(6)["feature"]:
        b = bins[bins.feature == f]
        L += [f"### `{f}`", "", "| Bin | Range | Loans | Share | Default rate | WoE |", "|---|---|---|---|---|---|"]
        L += [f"| {r.bin} | {r.range} | {r.loans:,} | {r.share:.1%} | {r.default_rate:.1%} | {r.woe:+.3f} |" for r in b.itertuples()]
        L.append("")
    L += ["![WoE by bin](screenshots/woe_top_features.png)", "", "## 3. Selected features that move together", "",
          f"Pairs of selected features whose WoE values correlate at {s['corr_flag']:.0%} or more on the train loans. "
          "Keep the one with the higher IV when fitting the model.", ""]
    if pairs.empty:
        L.append("None.")
    else:
        L += ["| Feature A | Feature B | Correlation |", "|---|---|---|"]
        L += [f"| `{r.feature_a}` | `{r.feature_b}` | {r.correlation:+.2f} |" for r in pairs.itertuples()]
    L += ["", "## 4. Method notes", "",
          f"- Numeric features start from up to {s['pre_bins']} quantile bins; neighbours are merged, least IV lost first, until each "
          f"bin has at least {s['min_share_numeric']:.0%} of loans, the default rate moves in one direction, and there are at most "
          f"{s['max_bins']} bins.",
          f"- Categorical features: categories are ordered by default rate and merged the same way (at least "
          f"{s['min_share_categorical']:.0%} per bin).",
          f"- Blank values get their own bin when they are at least {s['missing_min_share']:.0%} of train loans.",
          "- A count of 0.5 is added to each bin so an empty cell cannot give an infinite WoE.",
          "- IV test is computed with the train bins on the 2015 loans. A big drop means the relationship changed over time.",
          "- `int_rate`, `grade`, `sub_grade` and `installment` are not binned: they are Lending Club's own price of risk."]
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def run(train_path: Path, test_path: Path, model_dir: Path, docs_dir: Path, data_dir: Path, s: dict = SETTINGS) -> dict:
    train, test = pd.read_csv(train_path, keep_default_na=False, na_values=[""]), \
        pd.read_csv(test_path, keep_default_na=False, na_values=[""])
    specs = fit_all(train, s)
    summary = iv_summary(train, test, specs, s)
    bins = bin_table(train, specs, "train")
    selected = summary[summary.selected]["feature"].tolist()
    woe_train, woe_test = transform_all(train, specs), transform_all(test, specs)
    pairs = correlated_pairs(woe_train, selected, s["corr_flag"])

    for d in (model_dir, docs_dir, data_dir):
        d.mkdir(parents=True, exist_ok=True)
    (model_dir / "woe_binning.json").write_text(json.dumps(
        {"settings": s, "train_years": sorted(map(int, train["issue_year"].unique())), "selected": selected,
         "features": specs}, indent=2), encoding="utf-8")
    bins.round(6).to_csv(docs_dir / "woe_bins.csv", index=False)
    summary.round(6).to_csv(docs_dir / "iv_summary.csv", index=False)
    woe_train.to_csv(data_dir / "lc_train_woe.csv", index=False)
    woe_test.to_csv(data_dir / "lc_test_woe.csv", index=False)
    make_figures(summary, bins, docs_dir / "screenshots", s)
    write_report(docs_dir / "woe_iv_report.md", summary, bins, pairs, len(train), len(test), s)
    return {"specs": specs, "summary": summary, "bins": bins, "pairs": pairs, "woe_train": woe_train,
            "woe_test": woe_test, "selected": selected}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--train", type=Path, default=ROOT / "data" / "processed" / "lc_train.csv")
    ap.add_argument("--test", type=Path, default=ROOT / "data" / "processed" / "lc_test.csv")
    ap.add_argument("--model-dir", type=Path, default=ROOT / "models")
    ap.add_argument("--docs-dir", type=Path, default=ROOT / "docs")
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data" / "processed")
    a = ap.parse_args()
    r = run(a.train, a.test, a.model_dir, a.docs_dir, a.data_dir)
    sm = r["summary"]
    print(f"{len(sm)} features binned, {len(r['selected'])} selected (IV 0.02 to 0.5).")
    print(sm[["feature", "bins", "iv_train", "iv_test", "strength", "selected", "note"]].head(15).to_string(index=False,
          float_format=lambda v: f"{v:.4f}"))
    print(f"\nReport: {a.docs_dir / 'woe_iv_report.md'}")


if __name__ == "__main__":
    main()
