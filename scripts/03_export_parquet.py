#!/usr/bin/env python3
"""Turn the newest complete run under data/raw/ into Parquet.

    data/parquet/observations/<statsDataId>.parquet   every value of one table
    data/parquet/tables.parquet                       one row per table: its listing fields and
                                                      the name of each classification slot

The source changes in place, so only the newest run whose MANIFEST.json says
"complete" is exported.

Each table's data.csv.gz has its own classifications (code and label columns, the label
column named after the classification). They are mapped to a fixed set of slots:
tab, cat01..cat05, area, time (the maximum in this statistic), each as <slot>_code and
<slot>_label; a slot the table does not have is NULL. The classification's own name
("年齢", "地域", ...) is kept in tables.parquet (<slot>_name). Then unit, value (text, as
received: numbers and symbols such as *** and -), value_num (DOUBLE when value is a plain
number, otherwise NULL) and annotation (NULL when empty). Rows keep the order the API sent
them. A table's row count is checked against the manifest.

    uv run python scripts/03_export_parquet.py
    uv run python scripts/03_export_parquet.py --only 0000180001
"""

import argparse
import csv
import gzip
import io
import json
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "parquet"

SLOTS = ["tab", "cat01", "cat02", "cat03", "cat04", "cat05", "area", "time"]
OUTPUT_COLUMNS = (
    ["stats_data_id"]
    + [f"{s}_{k}" for s in SLOTS for k in ("code", "label")]
    + ["unit", "value", "value_num", "annotation"]
)
NUMBER_RE = r"-?[0-9]+(\.[0-9]+)?"
COPY = "format parquet, compression zstd, row_group_size 100000, write_bloom_filter false"


def parse_header(line: str) -> list[str]:
    return next(csv.reader(io.StringIO(line)))


def slots(cols: list[str]) -> list[tuple[str, int, int, str]]:
    """(slot, code column, label column, classification name) for each classification."""
    if cols[-3:] != ["unit", "value", "annotation"]:
        raise SystemExit(f"header does not end with unit,value,annotation: {cols}")
    out, i, body = [], 0, cols[:-3]
    while i < len(body):
        name = body[i]
        if not name.endswith("_code") or name[:-5] not in SLOTS:
            raise SystemExit(f"unknown classification column {name!r} in {cols}")
        if i + 1 >= len(body) or body[i + 1].endswith("_code"):
            raise SystemExit(f"no label column after {name!r} in {cols}")
        out.append((name[:-5], i, i + 1, body[i + 1]))
        i += 2
    return out


def export_table(con, table_id: str, src: Path, out: Path, expect_rows: int | None = None):
    """Write one table; returns (rows, {slot: classification name or None})."""
    with gzip.open(src, "rt", encoding="utf-8", newline="") as f:
        cols = parse_header(f.readline())
    found = slots(cols)
    names = {s: None for s in SLOTS}
    select = [f"'{table_id}' as stats_data_id"]
    for s in SLOTS:
        hit = next((x for x in found if x[0] == s), None)
        for k, idx in (("code", hit[1] if hit else None), ("label", hit[2] if hit else None)):
            select.append(f"nullif(c{idx}, '') as {s}_{k}" if hit else f"null::varchar as {s}_{k}")
        if hit:
            names[s] = hit[3]
    n = len(cols)
    select += [
        f"nullif(c{n - 3}, '') as unit",
        f"c{n - 2} as value",
        f"case when regexp_full_match(c{n - 2}, '{NUMBER_RE}') "
        f"then try_cast(c{n - 2} as double) end as value_num",
        f"nullif(c{n - 1}, '') as annotation",
    ]
    colspec = ", ".join(f"'c{i}': 'VARCHAR'" for i in range(n))
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".parquet.part")
    con.execute(
        f"copy (select {', '.join(select)} from read_csv('{src}', header=false, skip=1, "
        f"columns={{{colspec}}}, quote='\"', escape='\"', compression='gzip')) "
        f"to '{tmp}' ({COPY})"
    )
    rows = con.execute(f"select count(*) from read_parquet('{tmp}')").fetchone()[0]
    if expect_rows is not None and rows != expect_rows:
        tmp.unlink()
        raise SystemExit(f"{table_id}: {rows} rows written, the manifest says {expect_rows} rows")
    tmp.replace(out)
    return rows, names


def current_run() -> Path:
    runs = [p.parent for p in sorted(RAW.glob("*/MANIFEST.json"))
            if json.loads(p.read_text()).get("complete")]  # fmt: skip
    if not runs:
        raise SystemExit("no complete run under data/raw/")
    return runs[-1]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=[])
    a = ap.parse_args(argv)
    run = current_run()
    manifest = json.loads((run / "MANIFEST.json").read_text())
    con = duckdb.connect()
    con.execute("set memory_limit='3GB'; set threads=4")
    rows_out = []
    for tid, t in sorted(manifest["tables"].items()):
        if a.only and tid not in a.only:
            continue
        d = t["data"]
        out = OUT / "observations" / f"{tid}.parquet"
        rows, names = export_table(con, tid, run / d["path"], out, d["rows"])
        L = t["listing"]
        rows_out.append(
            {
                "stats_data_id": tid,
                "statistics_name": L["statistics_name"],
                "category": L["category"],
                "title": L["title"],
                "title_no": L["title_no"],
                "cycle": L["cycle"],
                "survey_date": L["survey_date"],
                "open_date": L["open_date"],
                "updated_date": L["updated_date"],
                "rows": rows,
                **{f"{s}_name": names[s] for s in SLOTS},
            }
        )
        print(f"{tid} {rows:>12,}", flush=True)
    if not a.only:
        cols = list(rows_out[0])
        con.execute("create table t (" + ", ".join(
            f"{c} {'BIGINT' if c == 'rows' else 'VARCHAR'}" for c in cols) + ")")
        con.executemany(f"insert into t values ({', '.join('?' * len(cols))})",
                        [list(r.values()) for r in rows_out])
        con.execute(f"copy t to '{OUT / 'tables.parquet'}' ({COPY})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
