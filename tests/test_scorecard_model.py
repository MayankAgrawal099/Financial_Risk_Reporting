"""Tests for the scorecard: logistic regression, scaling, AUC / KS / Gini and risk bands (mock data)."""
import json

import numpy as np
import pandas as pd
import pytest

import woe_binning
from scorecard_model import (BASE_ODDS, BASE_SCORE, PDO, assign_band, auc, band_cuts, band_table, feature_points, fit_logit,
                             gini, ks_statistic, prune_by_correlation, run, score_from_logit, scaling, subgrade_rank)
from test_woe_binning import make_mock


def make_frames(seed=0, n=60_000):
    d = make_mock(n, seed)
    d = d.drop(columns=["leaky", "int_rate"])
    rng = np.random.default_rng(seed + 1)
    latent = 0.9 * d["strong"] + rng.normal(0, 1.2, len(d))                       # a noisy "lender grade"
    d["sub_grade"] = [f"{'ABCDEFG'[int(np.clip(v // 1.1 + 3, 0, 6))]}{rng.integers(1, 6)}" for v in latent]
    d["grade"] = d["sub_grade"].str[0]
    d["int_rate"] = 6 + 2 * latent
    d["fico_avg"] = 700 - 25 * d["strong"]
    return d[d.issue_year == 2013].reset_index(drop=True), d[d.issue_year == 2014].reset_index(drop=True)


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("sc")
    train, test = make_frames()
    train.to_csv(tmp / "tr.csv", index=False)
    test.to_csv(tmp / "te.csv", index=False)
    woe_binning.run(tmp / "tr.csv", tmp / "te.csv", tmp / "models", tmp / "docs", tmp / "data")
    r = run(tmp / "tr.csv", tmp / "te.csv", tmp / "models" / "woe_binning.json", tmp / "models", tmp / "docs", tmp / "data")
    r["tmp"] = tmp
    return r


# ------------------------------------------------------------------ metrics
def test_auc_gini_ks_perfect_and_random():
    y = np.array([0] * 50 + [1] * 50)
    assert auc(y, np.r_[np.zeros(50), np.ones(50)]) == 1.0
    assert gini(y, np.r_[np.zeros(50), np.ones(50)]) == 1.0
    assert ks_statistic(y, np.r_[np.zeros(50), np.ones(50)]) == 1.0
    rng = np.random.default_rng(0)
    y = rng.integers(0, 2, 20_000)
    r = rng.random(20_000)
    assert abs(auc(y, r) - 0.5) < 0.02 and abs(gini(y, r)) < 0.04 and ks_statistic(y, r) < 0.03


def test_ks_by_hand():
    y = np.array([0, 0, 0, 1, 0, 1, 1, 1])
    r = np.array([1, 2, 3, 4, 5, 6, 7, 8.0])
    # cumulative good: .25 .5 .75 .75 1 ; cumulative bad: 0 0 0 .25 .25 .5 .75 1 ; biggest gap at r=3: 0.75 - 0
    assert ks_statistic(y, r) == pytest.approx(0.75)
    assert auc(y, r) == pytest.approx(0.9375)
    assert gini(y, r) == pytest.approx(0.875)


# ------------------------------------------------------------------ scaling and points
def test_scaling_base_score_and_pdo():
    p = BASE_ODDS / (BASE_ODDS + 1)                  # odds of 50 to 1 in favour of not defaulting
    logit_default = np.log((1 - p) / p)
    assert score_from_logit(logit_default) == pytest.approx(BASE_SCORE)
    assert score_from_logit(logit_default - np.log(2)) - score_from_logit(logit_default) == pytest.approx(PDO)
    f, o = scaling()
    assert f == pytest.approx(PDO / np.log(2))


def test_points_add_up_to_the_exact_score_within_rounding(result):
    m = result["model"]
    n = len(m["coefficients"])
    scored = result["scored"]["test"]
    logit = np.log(scored["pd"] / (1 - scored["pd"]))
    exact = score_from_logit(logit)
    assert np.abs(exact - scored["score"]).max() <= 0.5 * n + 1e-6


def test_points_table_matches_scores(result):
    pts = result["points"]
    assert set(pts.feature) == set(result["model"]["coefficients"])
    best = pts.groupby("feature")["points"].max().sum()
    worst = pts.groupby("feature")["points"].min().sum()
    sc = result["scored"]["train"]["score"]
    assert worst <= sc.min() and sc.max() <= best


def test_higher_score_means_lower_pd(result):
    d = result["scored"]["test"]
    assert d["score"].corr(d["pd"], method="spearman") < -0.99


# ------------------------------------------------------------------ model
def test_all_coefficients_negative_and_significant(result):
    fit = result["fit"]
    for f in result["model"]["coefficients"]:
        assert fit["coef"][f"woe_{f}"] < 0
        assert fit["p"][f"woe_{f}"] <= 0.05


def test_wald_p_values_agree_with_known_effect():
    rng = np.random.default_rng(0)
    x = pd.DataFrame({"a": rng.normal(size=20_000), "b": rng.normal(size=20_000)})
    y = pd.Series((rng.random(20_000) < 1 / (1 + np.exp(-(-1 + 0.8 * x["a"])))).astype(int))
    fit = fit_logit(x, y)
    assert fit["coef"]["a"] == pytest.approx(0.8, abs=0.08) and fit["p"]["a"] < 1e-6
    assert fit["p"]["b"] > 0.01 and abs(fit["coef"]["b"]) < 0.08


def test_correlated_features_are_pruned_keeping_higher_iv():
    rng = np.random.default_rng(1)
    a = rng.normal(size=5000)
    woe = pd.DataFrame({"woe_a": a, "woe_b": a + rng.normal(0, 0.3, 5000), "woe_c": rng.normal(size=5000)})
    kept, dropped = prune_by_correlation(woe, ["a", "b", "c"], {"a": 0.2, "b": 0.1, "c": 0.05})
    assert kept == ["a", "c"] and "b" in dropped
    kept, _ = prune_by_correlation(woe, ["a", "b", "c"], {"a": 0.1, "b": 0.2, "c": 0.05})
    assert kept == ["b", "c"]


def test_no_retained_pair_is_highly_correlated(result):
    woe = woe_binning.transform_all(result["scored"]["train"].merge(
        pd.read_csv(result["tmp"] / "tr.csv"), on="id", suffixes=("_s", "")), json.loads(
        (result["tmp"] / "models" / "woe_binning.json").read_text())["features"])
    feats = list(result["model"]["coefficients"])
    c = woe[[f"woe_{f}" for f in feats]].corr().abs().to_numpy().copy()
    np.fill_diagonal(c, 0)
    assert c.max() < 0.70


def test_model_ranks_well_and_beats_noise_features(result):
    m = result["metrics"].set_index("set")
    assert m.loc["Scorecard, test", "AUC"] > 0.62
    assert abs(m.loc["Scorecard, train", "AUC"] - m.loc["Scorecard, test", "AUC"]) < 0.03
    assert m.loc["Scorecard, test", "Gini"] == pytest.approx(2 * m.loc["Scorecard, test", "AUC"] - 1)
    assert 0 < m.loc["Scorecard, test", "KS"] < 1
    assert "noise" not in result["model"]["coefficients"]
    assert any("sub_grade" in s for s in m.index) and any("FICO" in s for s in m.index)


def test_test_data_never_changes_the_fit(tmp_path):
    train, test = make_frames(seed=5, n=30_000)
    wild = test.copy()
    wild["default"] = 1 - wild["default"]
    train.to_csv(tmp_path / "tr.csv", index=False)
    test.to_csv(tmp_path / "te.csv", index=False)
    wild.to_csv(tmp_path / "te2.csv", index=False)
    woe_binning.run(tmp_path / "tr.csv", tmp_path / "te.csv", tmp_path / "m", tmp_path / "d", tmp_path / "x")
    a = run(tmp_path / "tr.csv", tmp_path / "te.csv", tmp_path / "m" / "woe_binning.json", tmp_path / "m", tmp_path / "d", tmp_path / "x")
    b = run(tmp_path / "tr.csv", tmp_path / "te2.csv", tmp_path / "m" / "woe_binning.json", tmp_path / "m2", tmp_path / "d2", tmp_path / "x2")
    assert a["model"]["coefficients"] == b["model"]["coefficients"]
    assert a["cuts"] == b["cuts"]


# ------------------------------------------------------------------ bands
def test_bands_are_five_and_rate_rises_from_a_to_e(result):
    for name in ("train", "test"):
        b = result["bands"][name]
        assert list(b["band"]) == ["A", "B", "C", "D", "E"]
        assert np.all(np.diff(b["default_rate"]) > 0), (name, b["default_rate"].tolist())
        assert b["loans"].sum() == len(result["scored"][name])
    assert result["bands"]["train"]["share"].between(0.15, 0.25).all()          # quintiles of the train scores


def test_band_cuts_come_from_train_scores_only(result):
    assert result["cuts"] == band_cuts(result["scored"]["train"]["score"])
    assert len(result["cuts"]) == 4 and result["cuts"] == sorted(result["cuts"])


def test_assign_band_edges():
    s = pd.Series([400, 500, 500.0, 600, 700, 900])
    assert list(assign_band(s, [500, 550, 600, 650])) == ["E", "D", "D", "B", "A", "A"]


def test_band_table_lift():
    d = pd.DataFrame({"default": [0, 0, 0, 1, 0, 1, 1, 1], "score": [9, 8, 7, 6, 5, 4, 3, 2.0],
                      "pd": [.1, .1, .1, .3, .4, .6, .8, .9]})
    d["band"] = assign_band(d["score"], [3.5, 5.5, 7.5, 8.5])
    t = band_table(d, [3.5, 5.5, 7.5, 8.5])
    assert t.loc[t.band == "A", "default_rate"].iloc[0] == 0 and t.loc[t.band == "E", "default_rate"].iloc[0] == 1
    assert t.loc[t.band == "E", "lift"].iloc[0] == pytest.approx(2.0)


def test_subgrade_rank_order():
    r = subgrade_rank(pd.Series(["A1", "A5", "B1", "G5"]))
    assert list(r) == [1, 5, 6, 35]


def test_unseen_values_in_new_loans_still_get_a_score(result):
    spec = json.loads((result["tmp"] / "models" / "woe_binning.json").read_text())["features"]
    new = pd.read_csv(result["tmp"] / "te.csv").head(5).copy()
    new.loc[0, "strong"] = 1e9
    new.loc[1, "cat"] = "brand_new"
    new.loc[2, "with_missing"] = np.nan
    woe = woe_binning.transform_all(new, spec)
    from scorecard_model import score_woe_frame
    s = score_woe_frame(woe, result["model"])
    assert s.notna().all() and (s > 0).all()


# ------------------------------------------------------------------ outputs
def test_outputs_are_written(result):
    t = result["tmp"]
    for f in ("models/scorecard_model.json", "docs/scorecard_points.csv", "docs/scorecard_metrics.csv",
              "docs/scorecard_bands.csv", "docs/scorecard_report.md", "data/lc_scored.csv",
              "docs/Screenshots/scorecard_roc.png", "docs/Screenshots/scorecard_ks.png",
              "docs/Screenshots/scorecard_bands.png", "docs/Screenshots/scorecard_distribution.png"):
        assert (t / f).exists(), f
    text = (t / "docs/scorecard_report.md").read_text(encoding="utf-8")
    for h in ("Model quality", "Calibration across time", "Risk bands", "Scaling"):
        assert h in text
    saved = json.loads((t / "models/scorecard_model.json").read_text())
    assert saved["scaling"] == {"base_score": 600, "base_odds": 50, "pdo": 20} and len(saved["band_cuts"]) == 4
    scored = pd.read_csv(t / "data/lc_scored.csv")
    assert set(scored["data"]) == {"train", "test"} and scored["band"].isin(list("ABCDE")).all()
