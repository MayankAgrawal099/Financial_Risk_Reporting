"""Create the PostgreSQL schema, load the data and build the views.

Works on any machine with Python and PostgreSQL. No psql needed.

    pip install "psycopg[binary]"
    python src/load_to_postgres.py --dsn postgresql://postgres:YOUR_PASSWORD@localhost:5432/fin_risk
    # or set the environment variable FIN_PG_DSN once and omit --dsn

Steps (each can be skipped):
  1. sql/00_create_schema.sql        drops and recreates schemas fin and risk
  2. data/synthetic/*.csv            loaded into fin.*       (synthetic ledgers)
  3. data/processed/lending_club_sample.csv  loaded into risk.lending_club_sample  (real data; only with --lending-club)
  4. sql/02_*.sql and later          views, in file-name order

The database must already exist, e.g.  CREATE DATABASE fin_risk;
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = ROOT / "sql"
SYNTHETIC_DIR = ROOT / "data" / "synthetic"
LC_SAMPLE = ROOT / "data" / "processed" / "lending_club_sample.csv"

# load order respects foreign keys
LEDGER_TABLES = [
    ("fin.dim_account", "dim_account.csv"),
    ("fin.dim_cost_center", "dim_cost_center.csv"),
    ("fin.dim_vendor", "dim_vendor.csv"),
    ("fin.dim_period", "dim_period.csv"),
    ("fin.fact_gl_journal", "fact_gl_journal.csv"),
    ("fin.fact_budget", "fact_budget.csv"),
    ("fin.fact_drivers", "fact_drivers.csv"),
]


def run_sql_file(cur: psycopg.Cursor, path: Path) -> None:
    print(f"  running {path.name}")
    cur.execute(path.read_text(encoding="utf-8"))


def copy_csv(cur: psycopg.Cursor, table: str, path: Path) -> int:
    with path.open("r", encoding="utf-8", newline="") as f:
        header = next(csv.reader(f))
    cols = ", ".join('"' + c + '"' for c in header)
    sql = f"COPY {table} ({cols}) FROM STDIN WITH (FORMAT csv, HEADER true)"
    with path.open("rb") as f, cur.copy(sql) as copy:
        while chunk := f.read(1 << 20):
            copy.write(chunk)
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    return cur.fetchone()[0]


def main() -> None:
    ap = argparse.ArgumentParser(description="Create schema, load data and build views in PostgreSQL")
    ap.add_argument("--dsn", default=os.environ.get("FIN_PG_DSN"),
                    help="connection string, e.g. postgresql://user:password@localhost:5432/fin_risk")
    ap.add_argument("--lending-club", nargs="?", const=str(LC_SAMPLE), default=None, metavar="CSV",
                    help="also load the Lending Club sample (default file: data/processed/lending_club_sample.csv)")
    ap.add_argument("--skip-schema", action="store_true", help="do not drop and recreate the schemas")
    ap.add_argument("--skip-ledgers", action="store_true", help="do not load the synthetic ledger CSVs")
    ap.add_argument("--skip-views", action="store_true", help="do not run sql/02_*.sql and later")
    a = ap.parse_args()
    if not a.dsn:
        sys.exit("No database connection. Pass --dsn or set the FIN_PG_DSN environment variable.")

    with psycopg.connect(a.dsn) as conn, conn.cursor() as cur:
        if not a.skip_schema:
            run_sql_file(cur, SQL_DIR / "00_create_schema.sql")
        if not a.skip_ledgers:
            print("Loading synthetic ledgers (fin schema)")
            for table, name in LEDGER_TABLES:
                n = copy_csv(cur, table, SYNTHETIC_DIR / name)
                print(f"  {table:24s} {n:>8,} rows")
        if a.lending_club:
            path = Path(a.lending_club)
            if not path.exists():
                sys.exit(f"Lending Club sample not found: {path}\nRun src/prepare_lending_club.py first.")
            print("Loading Lending Club sample (risk schema)")
            n = copy_csv(cur, "risk.lending_club_sample", path)
            print(f"  risk.lending_club_sample {n:>8,} rows")
        if not a.skip_views:
            print("Building views")
            for f in sorted(SQL_DIR.glob("[0-9][0-9]_*.sql")):
                if f.name >= "02":
                    run_sql_file(cur, f)
        conn.commit()
    print("Done.")


if __name__ == "__main__":
    main()
