"""Tests for the Qlik Sense finance kit: load script, measures and the exported CSV files.

The measure expressions are re-computed in pandas from the committed CSVs, so the numbers Qlik should show are checked
without Qlik. The export itself is compared with a fresh export when FIN_PG_DSN is set.
"""
import os
import re

import pandas as pd
import pytest

from export_for_qlik import EXPORTS, MEASURES, ORDER_BY, QLIK_DIR, WATERFALL_LINES, export, measures_markdown

DATA = QLIK_DIR / "data"
QVS = (QLIK_DIR / "finance_load_script.qvs").read_text(encoding="utf-8")


def load_blocks():
    """(table, [(source column, alias)], csv file) for each LOAD statement in the script."""
    out = []
    for m in re.finditer(r"^(\w+):\s*\nLOAD(?: DISTINCT)?(.*?)FROM \[\$\(vDataPath\)([\w.]+)\]", QVS, re.S | re.M):
        body = re.sub(r"//.*", "", m.group(2))
        items = re.findall(r"([^,]+?)\s+as\s+(\[[^\]]+\]|\w+)\s*(?:,|$)", re.sub(r"\s+", " ", body))
        out.append((m.group(1), [(s.strip(), a.strip("[]")) for s, a in items], m.group(3)))
    return out


BLOCKS = load_blocks()
csv = lambda name: pd.read_csv(DATA / name)


# ------------------------------------------------------------------ load script
def test_script_has_the_four_tables():
    assert [b[0] for b in BLOCKS] == ["BvA_Lines", "Calendar", "BvA_Accounts", "Drivers"]


def test_script_reads_only_exported_files_and_columns():
    all_cols = {c for _, cols in EXPORTS.values() for c in cols}
    for table, items, file in BLOCKS:
        assert file in EXPORTS, file
        block = re.search(rf"^{table}:\s*\nLOAD(.*?)FROM", QVS, re.S | re.M).group(1)
        body = re.sub(r"//.*|'[^']*'|\[[^\]]*\]", "", block)
        used = set(re.findall(r"\b[a-z]+(?:_[a-z]+)*\b", body)) & all_cols
        assert used, table
        assert used <= set(EXPORTS[file][1]), (table, used - set(EXPORTS[file][1]))


def test_no_synthetic_keys_only_period_is_shared():
    seen = {}
    for table, items, _ in BLOCKS:
        for _, alias in items:
            seen.setdefault(alias, []).append(table)
    shared = {a: t for a, t in seen.items() if len(t) > 1}
    assert set(shared) == {"Period"}, shared
    assert sorted(shared["Period"]) == ["BvA_Accounts", "BvA_Lines", "Calendar", "Drivers"]


def test_no_duplicate_alias_inside_a_table():
    for table, items, _ in BLOCKS:
        names = [a for _, a in items]
        assert len(names) == len(set(names)), table


# ------------------------------------------------------------------ exported files
@pytest.mark.parametrize("name", list(EXPORTS))
def test_csv_columns_match_export_definition(name):
    df = csv(name)
    assert list(df.columns) == EXPORTS[name][1]
    assert len(df) >= 37


def test_measure_fields_exist_in_the_script():
    aliases = {a for _, items, _ in BLOCKS for _, a in items}
    lines = set(csv("bva_lines.csv")["line_name"])
    for name, expr, fmt, _ in MEASURES:
        for f in re.findall(r"\[([^\]=]+)\]", expr):
            assert f in aliases, (name, f)
        for f in set(re.findall(r"\b(Actual|Budget|Variance)\b", re.sub(r"\[[^\]]*\]|'[^']*'", "", expr))):
            assert f in aliases, (name, f)
        for v in re.findall(r"\[P&L Line\]=\{'([^']+)'\}", expr):
            assert v in lines, (name, v)
        assert fmt
    assert len({m[0] for m in MEASURES}) == len(MEASURES)
    assert all(w in lines for w in WATERFALL_LINES)


def test_measures_markdown_lists_every_measure():
    md = measures_markdown()
    assert all(f"| {m[0]} |" in md for m in MEASURES)


# ------------------------------------------------------------------ numbers Qlik should show (pandas stand-in)
def line(df, name, col):
    return df[df.line_name == name].set_index("period")[col]


def test_total_revenue_is_interest_plus_fee_income():
    d = csv("bva_lines.csv")
    for col in ("actual", "budget"):
        got = line(d, "Total Revenue", col)
        want = line(d, "Interest Income", col) + line(d, "Fee Income", col)
        assert (got - want).abs().max() < 0.011


def test_waterfall_bridges_budget_to_actual_net_income():
    d = csv("bva_lines.csv")
    contrib = sum(line(d, n, "variance") for n in WATERFALL_LINES)
    bridge = line(d, "Net Income", "budget") + contrib
    assert (bridge - line(d, "Net Income", "actual")).abs().max() < 0.06


def test_variance_sign_is_favourable_positive():
    d = csv("bva_lines.csv")
    cost = d[d.is_cost == 1]
    assert ((cost.budget - cost.actual) - cost.variance).abs().max() < 0.011
    rev = d[d.is_cost == 0]
    assert ((rev.actual - rev.budget) - rev.variance).abs().max() < 0.011


def test_account_detail_adds_to_net_income_variance():
    a, d = csv("bva_accounts.csv"), csv("bva_lines.csv")
    got = a.groupby("period")["variance"].sum()
    assert (got - line(d, "Net Income", "variance")).abs().max() < 0.06


def test_driver_effects_add_to_variance():
    x = csv("variance_drivers.csv")
    total = x["volume_effect"].fillna(0) + x["rate_effect"].fillna(0) + x["other_effect"].fillna(0)
    assert (total - x["variance"]).abs().max() < 0.02


def test_fy2025_reference_numbers():
    d = csv("bva_lines.csv")
    d = d[d.fiscal_year == 2025]
    ni = d[d.line_name == "Net Income"]
    assert round(ni.actual.sum(), 2) == 1962349.09 and round(ni.budget.sum(), 2) == 3580855.40
    prov = d[d.line_name == "Provision for Credit Losses"]
    assert round(prov.variance.sum(), 2) == -1636914.02
    assert round(ni.variance.sum() / ni.budget.sum(), 3) == -0.452


def test_opex_cost_centre_measure_filter():
    a = csv("bva_accounts.csv")
    opex = a[(a.account_id >= 6000) & (a.account_id <= 6500)]
    assert opex["cost_center_name"].notna().all()
    assert opex["cost_center_name"].nunique() >= 3


# ------------------------------------------------------------------ the export itself (needs a database)
@pytest.mark.skipif(not os.environ.get("FIN_PG_DSN"), reason="FIN_PG_DSN not set")
def test_committed_csv_matches_a_fresh_export(tmp_path):
    export(os.environ["FIN_PG_DSN"], tmp_path)
    for name in EXPORTS:
        a = pd.read_csv(tmp_path / name)
        b = pd.read_csv(DATA / name)
        pd.testing.assert_frame_equal(a, b, check_exact=False, rtol=1e-9)
    assert ORDER_BY.keys() == EXPORTS.keys()
