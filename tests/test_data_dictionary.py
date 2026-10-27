"""The data dictionary must document exactly the columns that exist."""
import re
from pathlib import Path

import pytest

import prepare_lending_club as plc
from generate_ledgers import generate

DOC = Path(__file__).resolve().parents[1] / "docs" / "data_dictionary.md"


def documented_columns():
    tables, current = {}, None
    for line in DOC.read_text(encoding="utf-8").splitlines():
        h = re.match(r"^###? .*?`(\w+)`", line)
        if line.startswith("## 2."):
            current = "lending_club_sample"
            tables.setdefault(current, [])
        elif h and line.startswith("### "):
            current = h.group(1)
            tables.setdefault(current, [])
        elif line.startswith("## "):
            current = None
        m = re.match(r"^\| `(\w+)` \|", line)
        if m and current:
            tables[current].append(m.group(1))
    return tables


def test_every_synthetic_table_column_is_documented():
    docs = documented_columns()
    for name, df in generate(42).items():
        assert name in docs, f"{name} missing from data dictionary"
        assert docs[name] == list(df.columns), name


def test_lending_club_sample_columns_are_documented_in_order():
    assert documented_columns()["lending_club_sample"] == plc.OUTPUT_COLUMNS


def test_provenance_is_stated():
    text = DOC.read_text(encoding="utf-8")
    assert "SYNTHETIC" in text and "REAL" in text
