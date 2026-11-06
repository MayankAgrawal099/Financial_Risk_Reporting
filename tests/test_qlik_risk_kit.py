"""Tests for the Qlik Sense risk kit: export, load script and measures (mock scorecard run, no Qlik needed)."""
import re

import numpy as np
import pandas as pd
import pytest

from export_risk_for_qlik import EXPORTS, MEASURES, PSI_COLOUR, QLIK_DIR, export, measures_markdown
from test_scorecard_stability import build

QVS = (QLIK_DIR / "risk_load_script.qvs").read_text(encoding="utf-8")
FUNCS = {"Sum", "Only", "TOTAL", "If", "RGB"}


def blocks():
    out = []
    for m in re.finditer(r"^(\w+):\s*\nLOAD(.*?)FROM \[\$\(vDataPath\)([\w.]+)\]", QVS, re.S | re.M):
        body = re.sub(r"//.*", "", m.group(2))
        pairs = re.findall(r"(\w+)\s+as\s+(\[[^\]]+\]|\w+)\s*(?:,|$)", re.sub(r"\s+", " ", body))
        out.append((m.group(1), pairs, m.group(3)))
    return out


BLOCKS = blocks()
ALIASES = {a.strip("[]") for _, p, _ in BLOCKS for _, a in p}


# ------------------------------------------------------------------ load script
def test_script_tables_and_files():
    assert [b[0] for b in BLOCKS] == ["Risk_Years", "Risk_Bands", "Feature_PSI", "Score_Deciles", "Model_Compare"]
    assert {b[2] for b in BLOCKS} == set(EXPORTS)


def test_script_columns_match_export_definition():
    for table, pairs, file in BLOCKS:
        assert [s for s, _ in pairs] == EXPORTS[file], table


def test_only_issue_year_is_shared_between_tables():
    seen = {}
    for table, pairs, _ in BLOCKS:
        names = [a.strip("[]") for _, a in pairs]
        assert len(names) == len(set(names)), table
        for n in names:
            seen.setdefault(n, []).append(table)
    shared = {n: t for n, t in seen.items() if len(t) > 1}
    assert shared == {"Issue Year": ["Risk_Years", "Risk_Bands"]}


# ------------------------------------------------------------------ measures
def test_measure_fields_exist_in_the_script():
    for name, expr, fmt, _ in MEASURES + [("psi colour", PSI_COLOUR, "x", "")]:
        for f in re.findall(r"\[([^\]]+)\]", expr):
            assert f in ALIASES, (name, f)
        bare = re.sub(r"\[[^\]]*\]|'[^']*'|\"[^\"]*\"|[{}<>=()*/+\-,0-9.;]", " ", expr)
        for tok in re.findall(r"[A-Za-z_]+", bare):
            assert tok in ALIASES or tok in FUNCS or tok == "TOTAL", (name, tok)
        assert fmt
    assert len({m[0] for m in MEASURES}) == len(MEASURES)
    assert all(f"| {m[0]} |" in measures_markdown() for m in MEASURES)


def test_test_measures_ignore_selections():
    for name, expr, _, _ in MEASURES:
        if name.startswith("Test ") or name == "Calibration Gap":
            assert "{1<Data={'test'}>}" in expr, name


# ------------------------------------------------------------------ export on a mock scorecard run
@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("qrisk")
    r = build(tmp)
    counts = export(tmp / "data", tmp / "docs", tmp / "models", tmp / "out")
    return {"tmp": tmp, "counts": counts, "run": r}


def read(ex, name):
    return pd.read_csv(ex["tmp"] / "out" / name)


def test_export_files_and_columns(exported):
    for name, cols in EXPORTS.items():
        assert list(read(exported, name).columns) == cols


def test_bands_add_up_to_the_scored_loans(exported):
    scored = pd.read_csv(exported["tmp"] / "data" / "lc_scored.csv")
    b = read(exported, "risk_bands.csv")
    assert b["loans"].sum() == len(scored) and b["defaults"].sum() == scored["default"].sum()
    assert np.isclose(b["pd_sum"].sum(), scored["pd"].sum(), rtol=1e-4)
    assert sorted(b["band"].unique()) == list("ABCDE")
    assert b.groupby("band")["score_range"].nunique().max() == 1


def test_band_default_rates_rise_from_a_to_e(exported):
    b = read(exported, "risk_bands.csv")
    for y, g in b.groupby("issue_year"):
        rate = (g.set_index("band")["defaults"] / g.set_index("band")["loans"]).sort_index()
        assert rate.is_monotonic_increasing, y


def test_year_table_matches_band_table(exported):
    y, b = read(exported, "risk_years.csv"), read(exported, "risk_bands.csv")
    got = b.groupby("issue_year")[["loans", "defaults"]].sum()
    for _, r in y.iterrows():
        g = got.loc[r["issue_year"]]
        assert g["loans"] == r["loans"] and abs(g["defaults"] / g["loans"] - r["default_rate"]) < 1e-5
    assert set(y["data"]) == {"train", "test"}


def test_feature_psi_is_long_and_complete(exported):
    f = read(exported, "risk_feature_psi.csv")
    assert set(f["psi_period"]) == {"2013", "2014", "test"}
    assert f.groupby("psi_period")["feature"].nunique().nunique() == 1
    assert (f["psi"] >= 0).all()


def test_deciles_and_models(exported):
    d = read(exported, "risk_score_deciles.csv")
    assert len(d) == 10 and abs(d["train_share"].sum() - 1) < 1e-4 and abs(d["test_share"].sum() - 1) < 1e-4
    m = read(exported, "risk_models.csv")
    assert (m["model"].str.startswith("Scorecard")).sum() == 2 and (m["auc"] > 0.5).all()


def test_headline_measures_by_hand(exported):
    """What the Qlik measures should show for the test year, recomputed from the exported tables."""
    y, b = read(exported, "risk_years.csv"), read(exported, "risk_bands.csv")
    t = y[y["data"] == "test"].iloc[0]
    tb = b[b["issue_year"] == t["issue_year"]]
    gap = tb["defaults"].sum() / tb["loans"].sum() - tb["pd_sum"].sum() / tb["loans"].sum()
    s = exported["run"]["summary"]
    assert abs(gap - (s["actual_test"] - s["pred_test"])) < 1e-4
    assert abs(t["gini"] - s["gini_test"]) < 1e-4 and abs(t["score_psi"] - s["score_psi_test"]) < 1e-4
