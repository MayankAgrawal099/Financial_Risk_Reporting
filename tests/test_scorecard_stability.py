"""Tests for the PSI stability check (mock data with known drift)."""
import json

import numpy as np
import pandas as pd
import pytest

import scorecard_model
import woe_binning
from scorecard_stability import (band_psi, psi_from_counts, psi_from_shares, psi_status, run, score_counts, score_edges,
                                 score_psi)
from test_scorecard_model import make_frames


def build(tmp, shift=0.0):
    """Fit on mock train; the test set optionally has its 'strong' feature shifted (a drifted population)."""
    train, test = make_frames()
    test = test.copy()
    test["strong"] = test["strong"] + shift                      # 'counts' is built from 'strong', so shift it too
    test["counts"] = np.clip(test["counts"] + np.round(2 * shift), 0, 9)
    test["fico_avg"] = test["fico_avg"] - 25 * shift             # the scorecard's strongest feature, built from 'strong'
    train.to_csv(tmp / "tr.csv", index=False)
    test.to_csv(tmp / "te.csv", index=False)
    woe_binning.run(tmp / "tr.csv", tmp / "te.csv", tmp / "models", tmp / "docs", tmp / "data")
    scorecard_model.run(tmp / "tr.csv", tmp / "te.csv", tmp / "models" / "woe_binning.json", tmp / "models", tmp / "docs", tmp / "data")
    return run(tmp / "data" / "lc_scored.csv", tmp / "tr.csv", tmp / "te.csv", tmp / "models" / "woe_binning.json",
               tmp / "models" / "scorecard_model.json", tmp / "models", tmp / "docs")


@pytest.fixture(scope="module")
def calm(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("psi_calm")
    r = build(tmp)
    r["tmp"] = tmp
    return r


@pytest.fixture(scope="module")
def drifted(tmp_path_factory):
    return build(tmp_path_factory.mktemp("psi_drift"), shift=1.5)


# ------------------------------------------------------------------ formula
def test_psi_by_hand():
    # shares 0.5/0.5 -> 0.4/0.6 : (0.4-0.5)ln(0.8) + (0.6-0.5)ln(1.2)
    expected = (-0.1) * np.log(0.8) + 0.1 * np.log(1.2)
    assert psi_from_shares([0.5, 0.5], [0.4, 0.6]) == pytest.approx(expected)
    assert psi_from_counts([50, 50], [40, 60]) == pytest.approx(expected)


def test_psi_zero_for_identical_and_symmetric():
    assert psi_from_shares([0.2, 0.3, 0.5], [0.2, 0.3, 0.5]) == pytest.approx(0.0)
    a, b = [0.7, 0.3], [0.4, 0.6]
    assert psi_from_shares(a, b) == pytest.approx(psi_from_shares(b, a))
    assert psi_from_shares(a, b) > 0


def test_psi_empty_bin_is_finite():
    v = psi_from_shares([0.5, 0.5], [1.0, 0.0])
    assert np.isfinite(v) and v > 0.25


def test_status_thresholds():
    assert psi_status(0.05) == "stable"
    assert psi_status(0.10) == "moderate shift"
    assert psi_status(0.2499) == "moderate shift"
    assert psi_status(0.25) == "large shift"


def test_score_deciles_on_train_are_equal():
    rng = np.random.default_rng(0)
    s = rng.normal(600, 50, 50_000)
    edges = score_edges(s)
    c = score_counts(s, edges)
    assert len(c) == 10 and (c / len(s)).min() > 0.099 and (c / len(s)).max() < 0.101
    assert score_psi(s, s) == pytest.approx(0.0, abs=1e-9)


def test_score_psi_detects_a_shift():
    rng = np.random.default_rng(1)
    s = rng.normal(600, 50, 50_000)
    assert score_psi(s, rng.normal(600, 50, 50_000)) < 0.01
    assert score_psi(s, rng.normal(620, 50, 50_000)) > 0.10        # +0.4 sd shift
    assert score_psi(s, rng.normal(650, 50, 50_000)) > 0.25        # +1 sd shift


def test_band_psi_known():
    train = pd.Series(list("ABCDE") * 100)
    later = pd.Series(list("A" * 100 + "B" * 100 + "C" * 100 + "D" * 100 + "E" * 100))
    assert band_psi(train, later) == pytest.approx(0.0)
    skew = pd.Series(list("A" * 300 + "B" * 100 + "C" * 50 + "D" * 25 + "E" * 25))
    assert band_psi(train, skew) > 0.25


# ------------------------------------------------------------------ pipeline on mock data
def test_calm_population_is_stable(calm):
    s = calm["summary"]
    assert s["score_psi_test"] < 0.10 and s["status"] == "stable"
    assert s["n_large"] == 0 and s["n_stable"] == s["n_features"]


def test_drifted_population_is_flagged(drifted, calm):
    s = drifted["summary"]
    assert s["score_psi_test"] > 0.25 and s["status"] == "large shift"
    assert s["score_psi_test"] > 5 * calm["summary"]["score_psi_test"]
    assert s["top_feature"] in ("fico_avg", "counts") and s["top_feature_psi"] > 0.25


def test_year_table_covers_every_year(calm):
    yt = calm["years"]
    assert list(yt["issue_year"]) == [2013, 2014]
    assert (yt["loans"] > 1000).all()
    assert yt.loc[yt.issue_year == 2013, "score_psi"].iloc[0] < 0.001      # the train year against itself
    assert (yt["AUC"] > 0.5).all() and (yt["Gini"] > 0).all()


def test_bands_rise_in_calm_case(calm):
    assert all(calm["monotonic"].values())


def test_features_sorted_and_deciles_sum_to_one(calm):
    f = calm["features"]
    assert f["test"].is_monotonic_decreasing
    d = calm["deciles"]
    assert d["train"].sum() == pytest.approx(1) and d["test"].sum() == pytest.approx(1)


def test_outputs_written(calm):
    tmp = calm["tmp"]
    for p in ("models/scorecard_stability.json", "docs/psi_features.csv", "docs/psi_by_year.csv", "docs/psi_score_deciles.csv",
              "docs/scorecard_stability_report.md"):
        assert (tmp / p).exists(), p
    j = json.loads((tmp / "models" / "scorecard_stability.json").read_text())
    assert j["thresholds"] == {"stable_below": 0.1, "large_from": 0.25}
    assert (tmp / "docs" / scorecard_model.FIG_DIR / "psi_score_distribution.png").exists()
    assert "Headline" in (tmp / "docs" / "scorecard_stability_report.md").read_text(encoding="utf-8")
