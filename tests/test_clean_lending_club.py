"""Tests for the scorecard cleaning and the time-based split, on a small dirty mock sample."""
import numpy as np
import pandas as pd
import pytest

from clean_lending_club import (NUMERIC, REFERENCE, SETTINGS, TARGET, apply_cleaning, fit_cleaning, read_sample,
                                run, split_by_issue_year)
from prepare_lending_club import LEAKAGE_COLUMNS, OUTPUT_COLUMNS

STATES = ["CA", "NY", "TX", "FL", "IL", "WA"]


def make_mock(n_per_year=1500, seed=0):
    rng = np.random.default_rng(seed)
    parts = []
    for k, year in enumerate((2013, 2014, 2015)):
        n = n_per_year
        d = pd.DataFrame(index=range(n))
        d["id"] = np.arange(n) + k * 100_000 + 1
        d["issue_year"] = year
        d["issue_d"] = pd.to_datetime([f"{year}-{m:02d}-01" for m in rng.integers(1, 13, n)])
        d["earliest_cr_line"] = d["issue_d"] - pd.to_timedelta(rng.integers(900, 9000, n), unit="D")
        d["default"] = (rng.random(n) < 0.12 + 0.03 * k).astype(int)
        d["loan_status"] = np.where(d["default"] == 1, "Charged Off", "Fully Paid")
        d["term_months"] = 36
        d["emp_length"] = rng.choice(["< 1 year", "1 year", "5 years", "10+ years", None], n, p=[.2, .2, .2, .3, .1])
        d["home_ownership"] = rng.choice(["RENT", "OWN", "MORTGAGE", "ANY"], n, p=[.45, .1, .449, .001])
        d["verification_status"] = rng.choice(["Verified", "Not Verified"], n)
        d["purpose"] = rng.choice(["debt_consolidation", "credit_card", "other"], n)
        d["addr_state"] = rng.choice(STATES + ["WY"], n, p=[.2, .2, .2, .2, .1, .0995, .0005])
        d["initial_list_status"] = rng.choice(["w", "f"], n)
        d["application_type"] = "Individual"
        d["loan_amnt"] = rng.integers(1000, 35000, n).astype(float)
        d["annual_inc"] = rng.lognormal(11, 0.5, n)
        d["dti"] = rng.uniform(0, 40, n)
        d["fico_range_low"] = rng.integers(660, 820, n).astype(float)
        d["fico_range_high"] = d["fico_range_low"] + 4
        d["revol_util"] = rng.uniform(0, 100, n)
        d["int_rate"] = rng.uniform(6, 25, n)
        d["installment"] = d["loan_amnt"] / 36
        d["grade"] = rng.choice(list("ABCDE"), n)
        d["sub_grade"] = d["grade"] + "1"
        for c in NUMERIC:
            if c not in d:
                d[c] = rng.integers(0, 12, n).astype(float)
        d["mths_since_last_delinq"] = np.where(rng.random(n) < 0.6, np.nan, rng.integers(1, 100, n))
        d["mths_since_last_record"] = np.where(rng.random(n) < 0.85, np.nan, rng.integers(1, 100, n))
        d["mort_acc"] = np.where(rng.random(n) < 0.03, np.nan, d["mort_acc"])          # a little missing: median
        d["bc_util"] = np.where(rng.random(n) < 0.12, np.nan, rng.uniform(0, 100, n))  # more than 5%: own bin
        d["num_actv_bc_tl"] = np.where((year == 2013) & (rng.random(n) < 0.9), np.nan, rng.integers(1, 8, n))  # drift, 60%+ in one year
        parts.append(d)
    df = pd.concat(parts, ignore_index=True)
    # dirt
    df.loc[5, "dti"] = -1.0
    df.loc[6, "dti"] = 999.0
    df.loc[7, "annual_inc"] = 0.0
    df.loc[8, "fico_range_low"] = 0.0
    df.loc[9, "id"] = df.loc[10, "id"]                      # duplicate id inside train
    df.loc[3500, "annual_inc"] = 9e9                        # huge outlier in test
    df.loc[3501, "addr_state"] = "ZZ"                       # state never seen in train
    for c in OUTPUT_COLUMNS:
        if c not in df:
            df[c] = np.nan
    return df[OUTPUT_COLUMNS]


@pytest.fixture(scope="module")
def mock_file(tmp_path_factory):
    p = tmp_path_factory.mktemp("lc") / "lending_club_sample.csv"
    df = make_mock()
    for c in ("issue_d", "earliest_cr_line"):
        df[c] = df[c].dt.strftime("%Y-%m-%d")
    df.to_csv(p, index=False)
    return p


@pytest.fixture(scope="module")
def result(mock_file, tmp_path_factory):
    d = tmp_path_factory.mktemp("out")
    return run(mock_file, d / "processed", d / "docs") | {"dir": d}


def test_split_is_by_time_with_no_overlap(mock_file):
    train, test = split_by_issue_year(read_sample(mock_file), (2013, 2014), (2015,))
    assert set(train.issue_year) == {2013, 2014} and set(test.issue_year) == {2015}
    assert train.issue_d.max() < test.issue_d.min()
    assert not set(train.id) & set(test.id)


def test_split_rejects_bad_year_setup(mock_file):
    df = read_sample(mock_file)
    with pytest.raises(ValueError):
        split_by_issue_year(df, (2013, 2014), (2014, 2015))
    with pytest.raises(ValueError):
        split_by_issue_year(df, (2014, 2015), (2013,))


def test_target_and_rows_are_preserved(result, mock_file):
    raw = read_sample(mock_file)
    assert len(result["train"]) == (raw.issue_year <= 2014).sum() - 1      # one duplicate id removed
    assert len(result["test"]) == (raw.issue_year == 2015).sum()
    merged = result["test"].merge(raw[["id", "default"]], on="id", suffixes=("", "_raw"))
    assert (merged["default"] == merged["default_raw"]).all()


def test_params_come_from_train_only(mock_file):
    raw = read_sample(mock_file)
    train, _ = split_by_issue_year(raw, (2013, 2014), (2015,))
    wild = raw.copy()
    wild.loc[wild.issue_year == 2015, ["annual_inc", "dti", "loan_amnt"]] *= 1000
    train2, _ = split_by_issue_year(wild, (2013, 2014), (2015,))
    assert fit_cleaning(train) == fit_cleaning(train2)


def test_missing_policy(result):
    p, train, test = result["params"], result["train"], result["test"]
    assert "mths_since_last_delinq" in p["keep_missing"] and "mths_since_last_record" in p["keep_missing"]
    assert "bc_util" in p["keep_missing"] and train["bc_util"].isna().any()
    assert "mort_acc" in p["impute_median"]
    for df in (train, test):
        assert df["mort_acc"].notna().all()
        assert df[p["categorical_features"]].notna().all().all()
    assert (train["emp_length"] == "Missing").any()


def test_constant_and_mostly_missing_columns_are_dropped(result):
    d = result["params"]["dropped"]
    assert d["term_months"] == "constant in train"
    assert "application_type" in d
    assert "term_months" not in result["train"].columns


def test_mostly_missing_ordinary_column_is_dropped_but_never_columns_are_kept(mock_file):
    raw = read_sample(mock_file)
    train, _ = split_by_issue_year(raw, (2013, 2014), (2015,))
    train = train.copy()
    train["open_acc"] = np.where(np.random.default_rng(1).random(len(train)) < 0.7, np.nan, 5.0)
    train.loc[train.index[:50], "open_acc"] = np.arange(50) % 9
    p = fit_cleaning(train)
    assert "missing in train" in p["dropped"]["open_acc"]
    assert "mths_since_last_record" in p["keep_missing"] and "mths_since_last_record" not in p["dropped"]


def test_impossible_values_become_missing_and_outliers_are_capped(result):
    tr = result["train_stats"]["invalid_set_to_missing"]
    assert tr["dti"] >= 1 and tr["annual_inc"] >= 1
    lo, hi = result["params"]["winsor"]["annual_inc"]
    assert result["test"]["annual_inc"].max() <= hi + 1e-9 and result["test"]["annual_inc"].min() >= lo - 1e-9
    assert result["test_stats"]["capped_at_train_percentile"]["annual_inc"] >= 1
    assert result["train"]["dti"].max() < 999


def test_unseen_and_rare_categories_become_other(result):
    cats = result["params"]["categories"]
    assert "WY" not in cats["addr_state"] and "Other" in cats["addr_state"]
    assert "ANY" not in cats["home_ownership"]
    assert set(result["test"]["addr_state"]) <= set(cats["addr_state"])
    assert result["test_stats"]["grouped_into_Other"]["addr_state"] >= 1


def test_derived_features_exist_and_raw_dates_are_gone(result):
    cols = set(result["train"].columns)
    assert {"credit_history_years", "fico_avg", "loan_to_income"} <= cols
    assert not {"issue_d", "earliest_cr_line", "fico_range_low", "fico_range_high"} & cols
    assert (result["train"]["credit_history_years"].dropna() >= 0).all()


def test_no_leakage_and_reference_columns_are_not_features(result):
    p = result["params"]
    features = set(p["numeric_features"]) | set(p["categorical_features"])
    assert not features & set(LEAKAGE_COLUMNS)
    assert not features & set(REFERENCE)
    assert set(p["reference_columns"]) == set(REFERENCE)
    assert TARGET not in features


def test_train_and_test_have_identical_columns_and_files_are_written(result):
    assert list(result["train"].columns) == list(result["test"].columns)
    d = result["dir"]
    for f in ("processed/lc_train.csv", "processed/lc_test.csv", "docs/scorecard_data_prep.json",
              "docs/scorecard_data_prep.md"):
        assert (d / f).exists(), f
    text = (d / "docs/scorecard_data_prep.md").read_text(encoding="utf-8")
    assert "Time-based split" in text and "Drift flag" in text and "CHECK" in text   # num_actv_bc_tl drifts


def test_apply_is_deterministic(mock_file):
    raw = read_sample(mock_file)
    train, test = split_by_issue_year(raw, (2013, 2014), (2015,))
    p = fit_cleaning(train)
    a, _ = apply_cleaning(test, p)
    b, _ = apply_cleaning(test, p)
    pd.testing.assert_frame_equal(a, b)
    assert SETTINGS["winsor_low"] < SETTINGS["winsor_high"]
