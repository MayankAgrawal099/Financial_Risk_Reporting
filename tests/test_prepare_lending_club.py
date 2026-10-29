"""Tests for the Lending Club prep script, using a small mock file in the real format."""
import gzip
import json

import numpy as np
import pandas as pd
import pytest

import prepare_lending_club as plc


def make_mock(path, n=6000, seed=1, gz=False):
    rng = np.random.default_rng(seed)
    years = rng.integers(2011, 2019, n)
    months = rng.choice(["Jan", "Feb", "Mar", "Jun", "Sep", "Dec"], n)
    status = rng.choice(["Fully Paid", "Charged Off", "Current", "Late (31-120 days)"], n,
                        p=[0.6, 0.2, 0.15, 0.05])
    df = pd.DataFrame({
        "id": np.arange(n).astype(str), "member_id": np.nan,
        "loan_amnt": rng.integers(1000, 35000, n), "term": rng.choice([" 36 months", " 60 months"], n),
        "int_rate": rng.uniform(6, 28, n).round(2), "grade": rng.choice(list("ABCDE"), n),
        "emp_length": "5 years", "home_ownership": "RENT", "annual_inc": 60000.0,
        "verification_status": "Verified", "issue_d": [f"{m}-{y}" for m, y in zip(months, years)],
        "loan_status": status, "purpose": "debt_consolidation", "addr_state": "CA", "dti": 18.5,
        "earliest_cr_line": "Aug-2003", "fico_range_low": 690, "fico_range_high": 694,
        "revol_util": [f"{v:.1f}%" for v in rng.uniform(0, 100, n)],
        # leakage and unwanted columns that must never reach the output
        "total_pymnt": 123.0, "recoveries": 5.0, "last_pymnt_d": "Jan-2019", "funded_amnt": 1000,
        "emp_title": "Engineer", "zip_code": "940xx",
    })
    footer = pd.DataFrame({"id": ["Total amount funded in policy code 1: 123", None]})
    df = pd.concat([df, footer], ignore_index=True)
    if gz:
        with gzip.open(path, "wt") as f:
            df.to_csv(f, index=False)
    else:
        df.to_csv(path, index=False)


@pytest.fixture()
def run(tmp_path):
    src = tmp_path / "accepted.csv"
    make_mock(src)
    out, prof = tmp_path / "sample.csv", tmp_path / "profile.json"
    p = plc.prepare(src, out, prof, years=(2013, 2016), n_per_year=300, chunksize=1000)
    return pd.read_csv(out), p, json.loads(prof.read_text())


def test_allowlist_has_no_leakage_columns():
    assert not set(plc.KEEP) & set(plc.LEAKAGE_COLUMNS)
    assert not set(plc.KEEP) & set(plc.EXCLUDED_OTHER)


def test_output_has_no_leakage_or_text_columns(run):
    df, _, _ = run
    banned = set(plc.LEAKAGE_COLUMNS) | set(plc.EXCLUDED_OTHER)
    assert not banned & set(df.columns)
    assert list(df.columns) == plc.OUTPUT_COLUMNS


def test_only_final_outcomes_and_target_mapping(run):
    df, _, _ = run
    assert set(df.loan_status) <= {"Fully Paid", "Charged Off"}
    assert (df["default"] == (df.loan_status == "Charged Off").astype(int)).all()


def test_year_range_and_per_year_cap(run):
    df, _, _ = run
    assert df.issue_year.between(2013, 2016).all()
    assert df.groupby("issue_year").size().max() <= 300
    assert pd.to_datetime(df.issue_d).dt.year.eq(df.issue_year).all()


def test_footer_rows_removed_and_numbers_parsed(run):
    df, _, _ = run
    assert df.id.notna().all() and not df.id.astype(str).str.startswith("Total").any()
    assert df.revol_util.between(0, 100).all()          # "%" stripped
    assert set(df.term_months) <= {36, 60}


def test_profile_contents(run):
    df, p, prof = run
    assert prof["sample_rows"] == len(df)
    assert prof["funnel"]["final_outcome"] <= prof["funnel"]["in_year_range"] <= prof["funnel"]["rows_read"]
    assert any(r["status_group"] == "Not final" for r in prof["censoring_diagnostic"])
    assert "tot_cur_bal" in prof["columns_requested_but_missing_in_file"]  # not in the mock file


def test_term_filter_and_reproducible(tmp_path):
    src = tmp_path / "a.csv.gz"
    make_mock(src, gz=True)
    a = plc.prepare(src, tmp_path / "a.csv", tmp_path / "a.json", n_per_year=200, term=36, seed=5)
    b = plc.prepare(src, tmp_path / "b.csv", tmp_path / "b.json", n_per_year=200, term=36, seed=5)
    da, db = pd.read_csv(tmp_path / "a.csv"), pd.read_csv(tmp_path / "b.csv")
    assert (da.term_months == 36).all()
    pd.testing.assert_frame_equal(da, db)
    assert a["sample_rows"] == b["sample_rows"]


def test_excel_resaved_file_is_rejected(tmp_path):
    src = tmp_path / "excel.csv"
    pd.DataFrame({"id": ["1", "2"], "issue_d": ["Dec-15", "Nov-15"],
                  "loan_status": ["Fully Paid", "Charged Off"]}).to_csv(src, index=False)
    with pytest.raises(SystemExit, match="Excel"):
        plc.prepare(src, tmp_path / "o.csv", tmp_path / "p.json")


def test_whole_number_columns_have_no_decimal_point(run, tmp_path):
    src = tmp_path / "accepted.csv"
    raw = pd.read_csv(tmp_path / "sample.csv", dtype=str) if (tmp_path / "sample.csv").exists() else None
    assert raw is not None
    for col in plc.INTEGER_COLUMNS:
        vals = raw[col].dropna()
        assert not vals.str.contains(r"\.", regex=True).any(), col
