#!/usr/bin/env python3
"""Check data/parquet against data/raw with a second reader.

Every row of every table is read from the gzip with the csv module and compared, in
order, with the row of data/parquet/observations/<id>.parquet: each classification's
code and label, unit, value, value_num (a plain number, or NULL) and annotation. Also
checks tables.parquet against the manifest and that no unexpected files are there.

    uv run python scripts/04_verify_parquet.py [--only ID ...]
"""

import argparse
import csv
import gzip
import json
import re
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "export", Path(__file__).resolve().parent / "03_export_parquet.py"
)
ex = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ex)

NUMBER = re.compile(r"-?[0-9]+(\.[0-9]+)?")


def expected(cols: list[str], row: list[str]) -> tuple:
    by = {s: (row[c], row[lab]) for s, c, lab, _ in ex.slots(cols)}
    out = []
    for s in ex.SLOTS:
        code, label = by.get(s, (None, None))
        out += [code or None if s in by else None, label or None if s in by else None]
    unit, value, ann = row[-3], row[-2], row[-1]
    num = float(value) if NUMBER.fullmatch(value) else None
    return (*out, unit or None, value, num, ann or None)


def check_table(run: Path, tid: str, entry: dict) -> list[str]:
    errors = []
    pq = ex.OUT / "observations" / f"{tid}.parquet"
    if not pq.exists():
        return [f"{tid}: no Parquet"]
    con = duckdb.connect()
    cur = con.execute(f"select * from read_parquet('{pq}')")
    names = [d[0] for d in cur.description]
    if names != ex.OUTPUT_COLUMNS:
        return [f"{tid}: columns {names}"]
    n = 0
    with gzip.open(run / entry["data"]["path"], "rt", encoding="utf-8", newline="") as f:
        r = csv.reader(f)
        cols = next(r)
        batch = []
        for row in r:
            if not batch:
                batch = cur.fetchmany(50000)
                batch.reverse()
            if not batch:
                errors.append(f"{tid}: Parquet ends at row {n}")
                return errors
            got = batch.pop()
            n += 1
            if got[0] != tid or tuple(got[1:]) != expected(cols, row):
                errors.append(f"{tid}: row {n}: {got} != {expected(cols, row)}")
                if len(errors) > 3:
                    return errors
    if batch or cur.fetchmany(1):
        errors.append(f"{tid}: Parquet has more rows than the {n} of the raw file")
    if n != entry["data"]["rows"]:
        errors.append(f"{tid}: {n} rows read, manifest says {entry['data']['rows']}")
    return errors


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=[])
    a = ap.parse_args(argv)
    run = ex.current_run()
    manifest = json.loads((run / "MANIFEST.json").read_text())
    bad, rows = 0, 0
    for tid, t in sorted(manifest["tables"].items()):
        if a.only and tid not in a.only:
            continue
        errs = check_table(run, tid, t)
        rows += t["data"]["rows"]
        for e in errs:
            print("ERROR", e)
        bad += bool(errs)
    files = {p.stem for p in (ex.OUT / "observations").glob("*.parquet")}
    extra = files - set(manifest["tables"])
    if extra and not a.only:
        print("ERROR unexpected Parquet files:", sorted(extra)[:5])
        bad += 1
    if not a.only:
        tb = duckdb.sql(f"select count(*), sum(rows) from '{ex.OUT / 'tables.parquet'}'").fetchone()
        want = (len(manifest["tables"]), rows)
        if tb != want:
            print("ERROR tables.parquet", tb, "!=", want)
            bad += 1
    print(f"{rows:,} rows checked, {bad} tables with errors")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
