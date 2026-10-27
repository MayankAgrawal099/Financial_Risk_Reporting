import numpy as np
import pandas as pd
import pytest

import audit_lending_club_columns as audit
import check_leakage as chk
import prepare_lending_club as plc


def test_known_leakage_columns_are_classified_as_leakage():
    for col in plc.LEAKAGE_COLUMNS:
        assert audit.classify(col)[0] == "LEAKAGE", col


def test_kept_columns_are_never_leakage():
    for col in plc.KEEP:
        assert audit.classify(col)[0] != "LEAKAGE", col
    assert audit.classify("int_rate")[0] == "REFERENCE_ONLY"
    assert audit.classify("grade")[0] == "REFERENCE_ONLY"
    assert audit.classify("issue_d")[0] == "SPLIT_ONLY"


def test_other_classes():
    assert audit.classify("annual_inc_joint")[0] == "EXCLUDED_SPARSE_JOINT"
    assert audit.classify("sec_app_fico_range_low")[0] == "EXCLUDED_SPARSE_JOINT"
    assert audit.classify("emp_title")[0] == "EXCLUDED_IDENTIFIER_OR_TEXT"
    assert audit.classify("some_new_column")[0] == "NOT_SELECTED_REVIEW"


def test_audit_reads_only_the_header(tmp_path):
    f = tmp_path / "a.csv"
    pd.DataFrame({"id": [1], "loan_status": ["Fully Paid"], "issue_d": ["Dec-2015"],
                  "loan_amnt": [1], "recoveries": [0], "mystery": [1]}).to_csv(f, index=False)
    res = audit.audit(f, tmp_path / "out.csv")
    d = dict(zip(res.column, res.decision))
    assert d["recoveries"] == "LEAKAGE" and d["mystery"] == "NOT_SELECTED_REVIEW"
    assert d["loan_amnt"] == "USED_FEATURE"


def test_auc_function_known_values():
    y = pd.Series([0, 0, 1, 1])
    assert chk.auc(pd.Series([1, 2, 3, 4.0]), y) == 1.0
    assert chk.auc(pd.Series([4, 3, 2, 1.0]), y) == 0.0
    assert np.isnan(chk.auc(pd.Series([1, 1, 1, 1.0]), y))  # constant score: undefined


def test_check_flags_a_leaky_column_and_not_a_random_one(tmp_path):
    rng = np.random.default_rng(0)
    n = 4000
    y = rng.binomial(1, 0.2, n)
    df = pd.DataFrame({
        "id": np.arange(n), "default": y, "loan_status": np.where(y == 1, "Charged Off", "Fully Paid"),
        "random_num": rng.normal(size=n),
        "leaky_num": y * 100 + rng.normal(0, 30, n),          # carries the outcome
        "random_cat": rng.choice(list("ABCDE"), n),
        "leaky_cat": np.where(y == 1, "Recovered", rng.choice(["x", "y"], n)),
    })
    f = tmp_path / "s.csv"
    df.to_csv(f, index=False)
    res = chk.check(f, tmp_path / "o.csv").set_index("column")
    assert res.loc["leaky_num", "flag"] == "SUSPICIOUS"
    assert res.loc["leaky_cat", "flag"] == "SUSPICIOUS"
    assert res.loc["random_num", "flag"] == "ok" and res.loc["random_cat", "flag"] == "ok"
    assert res.loc["random_num", "strength"] < 0.55
