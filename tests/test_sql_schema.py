"""Static checks on the SQL files (no database needed)."""
import re
from pathlib import Path

import pytest

import prepare_lending_club as plc
from generate_ledgers import generate

SQL = Path(__file__).resolve().parents[1] / "sql"


def table_columns(sql_text, table):
    m = re.search(rf"CREATE TABLE {re.escape(table)} \((.*?)\n\);", sql_text, re.S)
    assert m, f"{table} not found in schema"
    cols = []
    for line in m.group(1).splitlines():
        line = line.split("--")[0].strip()
        if not line or re.match(r"(PRIMARY|FOREIGN|CONSTRAINT|CHECK|UNIQUE)\b", line, re.I):
            continue
        cols.append(line.split()[0].strip('",'))
    return cols


@pytest.fixture(scope="module")
def schema():
    return (SQL / "00_create_schema.sql").read_text(encoding="utf-8")


def test_ledger_tables_match_generated_columns(schema):
    for name, df in generate(42).items():
        assert table_columns(schema, f"fin.{name}") == list(df.columns), name


def test_lending_club_table_matches_prepared_columns(schema):
    assert table_columns(schema, "risk.lending_club_sample") == plc.OUTPUT_COLUMNS


def test_integer_columns_are_integer_in_the_schema(schema):
    body = re.search(r"CREATE TABLE risk.lending_club_sample \((.*?)\n\);", schema, re.S).group(1)
    for col in plc.INTEGER_COLUMNS:
        line = next(l for l in body.splitlines() if re.match(rf"\s*{col}\s", l))
        assert re.search(r"SMALLINT", line), col


def test_sql_files_are_plain_sql_without_psql_commands():
    for f in SQL.glob("*.sql"):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            assert not line.lstrip().startswith("\\"), f"{f.name}:{n} uses a psql meta-command"
