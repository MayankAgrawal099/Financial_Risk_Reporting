"""Tests for WoE binning and IV, on mock data where the true relationships are known."""
import numpy as np
import pandas as pd
import pytest

from woe_binning import (SETTINGS, assign_bins, correlated_pairs, fit_all, fit_feature, iv_strength, iv_summary,
                         run, transform_all, transform_feature, woe_iv)


def make_mock(n=40_000, seed=0):
    rng = np.random.default_rng(seed)
    d = pd.DataFrame({"id": np.arange(n), "issue_year": rng.choice([2013, 2014], n)})
    strong = rng.normal(0, 1, n)                                            # higher value -> more defaults
    noise = rng.normal(0, 1, n)
    ushape = rng.normal(0, 1, n)
    cat = rng.choice([f"c{i}" for i in range(25)], n)
    cat_risk = {f"c{i}": -1.2 + 2.4 * i / 24 for i in range(25)}
    has_miss = rng.random(n) < 0.3
    logit = (-1.8 + 0.45 * strong + 0.25 * np.abs(ushape) + np.array([cat_risk[c] for c in cat]) * 0.25
             + np.where(has_miss, 0.6, 0.0))
    d["default"] = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
    d["strong"] = strong
    d["noise"] = noise
    d["ushape"] = ushape
    d["cat"] = cat
    d["with_missing"] = np.where(has_miss, np.nan, rng.normal(0, 1, n))
    d["counts"] = np.clip(np.round(2 + strong + rng.normal(0, 1, n)), 0, 9)   # few distinct values
    d["leaky"] = d["default"] * 2.0 + rng.normal(0, 0.3, n)                   # almost the target itself
    d["int_rate"] = rng.uniform(5, 25, n)                                     # reference column, must be ignored
    return d


@pytest.fixture(scope="module")
def data():
    d = make_mock()
    return d[d.issue_year == 2013].reset_index(drop=True), d[d.issue_year == 2014].reset_index(drop=True)


@pytest.fixture(scope="module")
def specs(data):
    return fit_all(data[0])


def test_woe_and_iv_by_hand():
    good, bad = np.array([700.0, 300.0]), np.array([100.0, 200.0])
    woe, contrib = woe_iv(good, bad)
    pg = (good + 0.5) / (1000 + 1)
    pb = (bad + 0.5) / (300 + 1)
    assert np.allclose(woe, np.log(pg / pb))
    assert np.allclose(contrib, (pg - pb) * woe)
    assert woe[0] > 0 > woe[1]          # the bin with fewer defaults is safer
    assert contrib.min() >= 0           # every bin adds to IV


def test_empty_cell_gives_finite_woe():
    woe, contrib = woe_iv([100, 0], [0, 50])
    assert np.isfinite(woe).all() and np.isfinite(contrib).all()


def test_strength_labels():
    assert [iv_strength(v) for v in (0.01, 0.05, 0.2, 0.4, 0.8)] == ["not useful", "weak", "medium", "strong", "suspicious"]


def test_numeric_woe_is_monotonic_and_bins_are_big_enough(data, specs):
    train = data[0]
    for f in ("strong", "counts", "ushape", "noise"):
        sp = specs[f]
        w = np.array(sp["woe"])
        assert np.all(np.diff(w) >= -1e-12) or np.all(np.diff(w) <= 1e-12), f
        bins = assign_bins(train[f], sp)
        shares = bins.value_counts(normalize=True)
        assert shares.min() >= SETTINGS["min_share_numeric"] - 1e-9, f
        assert sp["n_bins"] <= SETTINGS["max_bins"]


def test_strong_feature_has_high_iv_and_noise_is_rejected(data, specs):
    train, test = data
    s = iv_summary(train, test, specs).set_index("feature")
    assert s.loc["strong", "iv_train"] > 0.1 and s.loc["strong", "selected"]
    assert s.loc["noise", "iv_train"] < 0.02 and not s.loc["noise", "selected"]
    assert s.loc["cat", "iv_train"] > 0.02
    assert s.loc["strong", "iv_train"] > s.loc["ushape", "iv_train"] > s.loc["noise", "iv_train"]


def test_leaky_feature_is_flagged_not_selected(data, specs):
    s = iv_summary(*data, specs).set_index("feature")
    assert s.loc["leaky", "iv_train"] > 0.5
    assert not s.loc["leaky", "selected"] and "leakage" in s.loc["leaky", "note"]


def test_sign_of_woe_follows_risk(data, specs):
    w = specs["strong"]["woe"]
    assert w[0] > 0 > w[-1]       # low values of "strong" are safe, high values are risky


def test_missing_values_get_their_own_informative_bin(data, specs):
    sp = specs["with_missing"]
    assert sp["has_missing_bin"] and sp["missing_woe"] < min(sp["woe"])      # missing group defaults more
    t = transform_feature(pd.Series([np.nan, 0.0]), sp)
    assert t.iloc[0] == sp["missing_woe"] and np.isfinite(t.iloc[1])


def test_tiny_missing_share_joins_nearest_bin():
    rng = np.random.default_rng(1)
    x = pd.Series(rng.normal(0, 1, 5000))
    y = pd.Series((rng.random(5000) < 0.2).astype(int))
    x.iloc[:10] = np.nan
    sp = fit_feature(x, y)
    assert not sp["has_missing_bin"] and sp["missing_woe"] in sp["woe"]


def test_categorical_groups_cover_every_category_once(data, specs):
    sp = specs["cat"]
    cats = [c for g in sp["groups"] for c in g]
    assert sorted(cats) == sorted(data[0]["cat"].unique()) and len(cats) == len(set(cats))
    assert 1 < len(sp["groups"]) <= SETTINGS["max_bins"]
    assert np.all(np.diff(sp["woe"]) <= 1e-12) or np.all(np.diff(sp["woe"]) >= -1e-12)   # ordered by default rate


def test_unseen_category_gets_average_risk_woe(specs):
    sp = specs["cat"]
    t = transform_feature(pd.Series(["c1", "never_seen"]), sp)
    assert np.isfinite(t).all()
    assert abs(t.iloc[1]) == min(abs(w) for w in sp["woe"])


def test_every_loan_gets_a_finite_woe_even_for_extreme_values(data, specs):
    test = data[1].copy()
    test.loc[0, ["strong", "noise", "ushape", "counts"]] = [1e9, -1e9, 1e9, 99]
    out = transform_all(test, specs)
    assert out.filter(like="woe_").notna().all().all()
    assert len(out) == len(test)


def test_bins_come_from_train_only(data):
    train, test = data
    wild = test.copy()
    wild[["strong", "noise", "ushape"]] *= 1000
    wild["default"] = 1 - wild["default"]
    assert fit_all(train) == fit_all(train)                 # deterministic
    # fitting never sees the test frame at all: a different test set cannot change the train bins
    a = fit_all(train)
    s1 = iv_summary(train, test, a)
    s2 = iv_summary(train, wild, a)
    assert a == fit_all(train)
    assert (s1["iv_train"].values == s2.set_index("feature").loc[s1.feature, "iv_train"].values).all()


def test_reference_columns_are_not_binned(specs):
    assert "int_rate" not in specs and "id" not in specs and "default" not in specs


def test_correlated_pairs_found(data, specs):
    t = transform_all(data[0], specs)
    pairs = correlated_pairs(t, ["strong", "counts", "noise"], 0.5)
    assert {"strong", "counts"} == set(pairs.iloc[0][["feature_a", "feature_b"]])


def test_pipeline_writes_all_outputs(tmp_path):
    d = make_mock(20_000, seed=3)
    d[d.issue_year == 2013].to_csv(tmp_path / "tr.csv", index=False)
    d[d.issue_year == 2014].to_csv(tmp_path / "te.csv", index=False)
    r = run(tmp_path / "tr.csv", tmp_path / "te.csv", tmp_path / "models", tmp_path / "docs", tmp_path / "data")
    for f in ("models/woe_binning.json", "docs/woe_bins.csv", "docs/iv_summary.csv", "docs/woe_iv_report.md",
              "docs/screenshots/iv_ranking.png", "docs/screenshots/woe_top_features.png", "data/lc_train_woe.csv",
              "data/lc_test_woe.csv"):
        assert (tmp_path / f).exists(), f
    woe = pd.read_csv(tmp_path / "data/lc_test_woe.csv")
    assert woe.filter(like="woe_").notna().all().all()
    bins = pd.read_csv(tmp_path / "docs/woe_bins.csv")
    per_feature = bins.groupby("feature")["loans"].sum()
    assert (per_feature == r["woe_train"].shape[0]).all()          # bins cover every train loan exactly once
    assert "strong" in r["selected"] and "leaky" not in r["selected"]
    text = (tmp_path / "docs/woe_iv_report.md").read_text(encoding="utf-8")
    assert "Feature ranking" in text and "leakage" in text
