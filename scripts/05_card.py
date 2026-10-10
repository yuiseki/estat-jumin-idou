#!/usr/bin/env python3
"""Write data/README.md (the Hub card), data/LICENSE and data/provenance.yaml.

Every number in them is computed from data/parquet and the manifest of the newest
complete run, so run it after 03_export_parquet.py and 04_verify_parquet.py.

    uv run python scripts/05_card.py
"""

import importlib.util
import json
import sys
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("export", HERE / "03_export_parquet.py")
ex = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ex)

DATA = ex.ROOT / "data"
TERMS = "https://www.e-stat.go.jp/terms-of-use"
API_TERMS = "https://www.e-stat.go.jp/api/terms-of-use"
CREDIT = "このサービスは、政府統計総合窓口(e-Stat)のAPI機能を使用していますが、サービスの内容は国によって保証されたものではありません。"


def stats() -> dict:
    run = ex.current_run()
    m = json.loads((run / "MANIFEST.json").read_text())
    t = ex.OUT / "tables.parquet"
    n, rows = duckdb.sql(f"select count(*), sum(rows) from '{t}'").fetchone()
    cats = duckdb.sql(
        f"select category, count(*), sum(rows) from '{t}' group by 1 order by min(stats_data_id)"
    ).fetchall()
    unit_rows = duckdb.sql(
        f"select sum(case when value_num is not null then 1 else 0 end) "
        f"from '{ex.OUT / 'observations' / '*.parquet'}'"
    ).fetchone()[0]
    return {"run": run.name, "m": m, "tables": n, "rows": rows, "cats": cats, "numeric": unit_rows}


def card(s: dict) -> str:
    m = s["m"]
    cats = "\n".join(f"| {c} | {n:,} | {r:,} |" for c, n, r in s["cats"])
    return f"""---
license: other
license_name: e-stat-terms-of-use-pdl
license_link: {TERMS}
language:
- ja
- en
task_categories:
- tabular-regression
- time-series-forecasting
tags:
- japan
- e-stat
- statistics-bureau-of-japan
- migration
- population
- basic-resident-register
size_categories:
- 100M<n<1B
configs:
- config_name: observations
  data_files: parquet/observations/*.parquet
  default: true
- config_name: tables
  data_files: parquet/tables.parquet
---

# estat-jumin-idou

Every table of the 住民基本台帳人口移動報告 (Report on Internal Migration in Japan Derived
from the Basic Resident Registration, statistics code `00200523`, Statistics Bureau of Japan)
that the e-Stat database serves through the e-Stat API: {s["tables"]:,} tables and
{s["rows"]:,} values, as the API served them on {s["run"]}.

Source: 「住民基本台帳人口移動報告」(総務省統計局), e-Stat
(<https://www.e-stat.go.jp/stat-search/database?toukei=00200523>). The data are reused under
the e-Stat terms of use ({TERMS}), compatible with CC BY 4.0; this copy is edited (see
Layout), and the API credit required by its terms is at the end of this card.

## The data follows upstream corrections

The tables are overwritten in place at the source (tables from 2020 on grow with each month
and year under the same `statsDataId`; titles and `UPDATED_DATE` can change). This dataset
therefore holds one run, the newest, and is replaced as a whole when it is fetched again;
older runs are not kept here. The current run is `raw/{s["run"]}/`.

## Subsets

| subset | rows | what it is |
|---|---:|---|
| `observations` | {s["rows"]:,} | every value of every table, one row per value |
| `tables` | {s["tables"]:,} | one row per table: title, category, period, update date, rows, and the name of each classification slot |

Tables by e-Stat category (提供分類):

| category | tables | values |
|---|---:|---:|
{cats}

Many values appear in more than one table (the yearly bundles up to 2019 and the later
multi-year summary tables overlap). Nothing is de-duplicated: filter by `stats_data_id`.

## Columns of `observations`

One file per table at `parquet/observations/<statsDataId>.parquet`, all with the same
columns, in the order the API sent the rows. A table has its own classifications; they are
mapped to the slots `tab` (表章項目), `cat01` to `cat05`, `area` and `time`, each as
`<slot>_code` and `<slot>_label`. A slot a table does not have is NULL, and the name of the
classification in that slot (for example 年齢, 性別, 国籍, 移動前の住所地) is in
`tables.<slot>_name`: read it before interpreting a `cat0N` column, because the same slot
means different things in different tables.

| column | type | |
|---|---|---|
| `stats_data_id` | VARCHAR | the e-Stat table id |
| `tab_code`, `tab_label`, `cat01_code` ... `cat05_label`, `area_code`, `area_label`, `time_code`, `time_label` | VARCHAR | codes and labels as in the API's CSV; empty is NULL |
| `unit` | VARCHAR | unit of the value (人, ％, ...) |
| `value` | VARCHAR | the value as received: a number, or a symbol such as `***` (not surveyed) or `-` (no value by definition) whose meaning is in the table's `NOTE` lines (`raw/{s["run"]}/tables/<id>/header.csv`) |
| `value_num` | DOUBLE | `value` when it is a plain number (`-?[0-9]+(.[0-9]+)?`), otherwise NULL; {s["numeric"]:,} of the rows |
| `annotation` | VARCHAR | the API's annotation, NULL when empty |

```sql
-- read the slot names of a table first, then its values
select * from tables where stats_data_id = '0003420481';
select area_label, cat01_label, value_num
from observations where stats_data_id = '0003420481' limit 10;
```

## Layout

```
parquet/observations/<statsDataId>.parquet   {s["tables"]:,} files
parquet/tables.parquet
raw/{s["run"]}/MANIFEST.json                 file entries with sha256, URLs (no appId), counts
raw/{s["run"]}/getStatsList.json             the listing of the run, as received
raw/{s["run"]}/tables/<id>/getMetaInfo.json  classification metadata, as received
raw/{s["run"]}/tables/<id>/header.csv        RESULT, TABLE_INF, NOTE sections of the first page
raw/{s["run"]}/tables/<id>/data.csv.gz       the VALUE section of the API's CSV, every row
```

The Parquet was written from `data.csv.gz` by `scripts/03_export_parquet.py` and compared
row by row with a second reader (`scripts/04_verify_parquet.py`): every code, label, unit,
value and annotation of all {s["rows"]:,} rows equals the raw file. Not included: the files-only
tables on the e-Stat file pages (the long-term series from 1954, the 1999-2006 detailed
tables, 2000-2005 quarterly files), which have no API.

## Licence

e-Stat terms of use ({TERMS}), which follow the Government of Japan Standard Terms of Use
(version 2.0) and are compatible with CC BY 4.0: reuse, redistribution and commercial use
are allowed with attribution, and edited data must say so. This dataset is edited (reshaped
into Parquet with the classifications mapped to slots).

Credit required by article 7 of the e-Stat API terms of use ({API_TERMS}):

> {CREDIT}

(This service uses the API of the Portal Site of Official Statistics of Japan (e-Stat); its
content is not guaranteed by the Government of Japan.)

Collection and conversion code: <https://github.com/yuiseki/estat-jumin-idou> (MIT).
Retrieved {m["created_at"][:10]} to {m["completed_at"][:10]} (UTC).
"""


def licence_file() -> str:
    return f"""The contents of data/ are 「住民基本台帳人口移動報告」(総務省統計局), obtained through the
e-Stat API and edited (reshaped into Parquet; classifications mapped to slots).

They are used under the e-Stat terms of use ({TERMS}), which follow the Government of Japan
Standard Terms of Use (version 2.0) and are compatible with CC BY 4.0: reuse,
redistribution and commercial use are allowed with attribution, and edited data must say
so. They were not created by the Statistics Bureau in the form published here.

Credit required by article 7 of the e-Stat API terms of use ({API_TERMS}):

  {CREDIT}

The code of the repository this dataset was built from is MIT licensed.
"""


def provenance(s: dict) -> str:
    m = s["m"]
    return f"""# Where this data comes from and what was done to it.

source:
  name: 住民基本台帳人口移動報告 (statistics code 00200523), e-Stat API 3.0
  publisher: 総務省統計局 (Statistics Bureau of Japan)
  api: https://api.e-stat.go.jp/rest/3.0/app (getStatsList, getMetaInfo, getSimpleStatsData)
  portal: https://www.e-stat.go.jp/stat-search/database?toukei=00200523
  versions: none. The tables are overwritten in place; this dataset holds the newest run and follows upstream corrections.
  licence:
    applied: e-Stat terms of use ({TERMS}) and API terms of use ({API_TERMS})
    card: "license: other, license_name: e-stat-terms-of-use-pdl"
    attribution_required: true
    commercial_use: allowed
    derivatives: allowed, with a note that the data are edited
    api_credit: "{CREDIT}"
  citation: >-
    「住民基本台帳人口移動報告」(総務省統計局), e-Stat, https://www.e-stat.go.jp/stat-search/database?toukei=00200523,
    retrieved {m["created_at"][:10]}.

run:
  date: {s["run"]}
  started_at: "{m["created_at"]}"
  completed_at: "{m["completed_at"]}"
  tables: {s["tables"]}
  rows: {s["rows"]}
  failures: {len(m["failures"])}

processing:
  - raw files kept as received (gzip of the VALUE section, header sections, metadata JSON)
  - scripts/03_export_parquet.py maps each table's classifications to fixed slots and writes Parquet
  - scripts/04_verify_parquet.py compares every row of every table with the raw file
"""


def main() -> int:
    s = stats()
    (DATA / "README.md").write_text(card(s), encoding="utf-8")
    (DATA / "LICENSE").write_text(licence_file(), encoding="utf-8")
    (DATA / "provenance.yaml").write_text(provenance(s), encoding="utf-8")
    print(f"{s['tables']} tables, {s['rows']:,} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
