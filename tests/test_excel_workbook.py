"""Tests for the Excel workbook: tables, P&L and balance sheet formulas, DAX measure list and reference check."""
from openpyxl import load_workbook

from build_excel_workbook import DAX, build


def test_workbook_builds_with_expected_sheets(tmp_path):
    out = tmp_path / "m.xlsx"
    build(out, tmp_path / "dax.md")
    wb = load_workbook(out)
    for name in ("Summary", "PL_Monthly", "BS_Monthly", "Reference_Check", "DAX_Measures", "fact_gl_journal"):
        assert name in wb.sheetnames
    assert wb["fact_gl_journal"].tables  # Excel Table present for the Data Model


def test_dax_measure_names_unique():
    names = [m[0] for m in DAX]
    assert len(names) == len(set(names))
