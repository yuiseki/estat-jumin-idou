# estat-jumin-idou

A reproducible copy of every table of the **住民基本台帳人口移動報告** (Report on Internal
Migration in Japan Derived from the Basic Resident Registration, statistics code `00200523`,
Statistics Bureau of Japan) that the e-Stat database serves through the e-Stat API.

The report counts moves between municipalities, recorded by the move-in notifications
of the Basic Resident Registration. The database has monthly, quarterly and annual tables:
prefecture-to-prefecture origin-destination matrices since 1999, moves into each
municipality by prefecture of origin, municipality-level moves by age, sex and
nationality, and municipality-to-municipality origin-destination tables (the 参考表)
since 2012.

## Source

- e-Stat database: <https://www.e-stat.go.jp/stat-search/database?toukei=00200523>
- e-Stat API 3.0: <https://www.e-stat.go.jp/api/> (`getStatsList`, `getStatsData` with
  `cntGetFlg=Y`, `getMetaInfo`, `getSimpleStatsData`)
- Statistics Bureau: <https://www.stat.go.jp/data/idou/index.html>

What one run collects:

- the table listing (`getStatsList`, statsCode `00200523`), 471 tables on 2026-10-05;
- for each table, its metadata (`getMetaInfo`, JSON, as received) and all of its values as
  the API's CSV (`getSimpleStatsData`, paged 100,000 values per request with
  `startPosition`), stored as one gzip file per table.

### Version, and why it matters for this statistic

A version is the UTC date on which a collection started (`data/raw/<YYYY-MM-DD>/`).
The source changes in place: tables from 2020 on are extended with each new year or
month under the same `statsDataId` (they carry `SURVEY_DATE` `0`), titles and periods of
an existing id can change, and `UPDATED_DATE` is rewritten in batches. The manifest
therefore records, per table, the listing's `UPDATED_DATE`, `OPEN_DATE` and
`OVERALL_TOTAL_NUMBER`, the `TOTAL_NUMBER` counted before the fetch, and the
`UPDATED_DATE` and `TOTAL_NUMBER` seen in every page of the fetch. If a table changes
while its pages are being fetched (a different `UPDATED_DATE` or `TOTAL_NUMBER` on a
later page) the table is fetched again from its first page.

`TOTAL_NUMBER` (what the API pages over) is not the listing's `OVERALL_TOTAL_NUMBER`;
for `0003420481` they are 550,128 and 501,510. Row counts are checked against
`TOTAL_NUMBER`.

### Not included

- **Files-only tables** on the e-Stat file pages
  (<https://www.e-stat.go.jp/stat-search/files?toukei=00200523&tstat=000000070001>):
  the long-term series 長期時系列表 (1954 on, including the prefecture-to-prefecture
  matrix that is the only source before 1999), the 1999-2006 detailed tables 年報（詳細集計）
  that exist only as per-prefecture Excel files, quarterly files of 2000-2005 and notes.
  They are Excel files with no API, and are out of scope for this repository.
- The Excel and "EXCEL閲覧用" copies of tables that are in the database: the database
  values are the same tables, and the Statistics Bureau's errata note that the database
  copy was correct where an Excel copy was not (2025-11-21).
- Many values appear in more than one table (yearly bundles up to 2019 and the later
  multi-year summary tables overlap). Nothing is de-duplicated here.

## Licence

The data are covered by the e-Stat terms of use
(<https://www.e-stat.go.jp/terms-of-use>), which follow the Government of Japan Standard
Terms of Use (version 2.0) and are compatible with CC BY 4.0: reuse, redistribution and
commercial use are allowed with attribution, and edited data must say so.
Attribution: 「住民基本台帳人口移動報告」(総務省統計局), e-Stat
(<https://www.e-stat.go.jp/stat-search/database?toukei=00200523>), retrieved on the
version date.

Credit required by article 7 of the e-Stat API terms of use
(<https://www.e-stat.go.jp/api/terms-of-use>, <https://www.e-stat.go.jp/api/api-info/credit>):

> このサービスは、政府統計総合窓口(e-Stat)のAPI機能を使用していますが、サービスの内容は国によって保証されたものではありません。

(This service uses the API of the Portal Site of Official Statistics of Japan (e-Stat);
its content is not guaranteed by the Government of Japan.)

The code in `scripts/` and `tests/` is MIT licensed (`LICENSE`).

## How to run

An e-Stat application ID is required. It is read from the environment variable
`E_STAT_APP_ID` only, and is never printed or written: URLs in the manifest and the log
have no `appId`.

```sh
uv sync
export E_STAT_APP_ID=...            # or: set -a; . /path/to/.env; set +a

# plan only: list the tables, count every table with cntGetFlg=Y, print the estimate
uv run python scripts/01_download.py --plan-only

# a small slice
uv run python scripts/01_download.py --only 0000180002 0003423635
uv run python scripts/01_download.py --category 月報 --limit 3

# the full collection (all tables of the listing)
uv run python scripts/01_download.py 2>&1 | tee -a data/download.log

# verify the newest version (add --require-complete after a full run)
uv run python scripts/02_verify.py --require-complete
```

Options of `01_download.py`: `--only ID ...` (statsDataId, space or comma separated),
`--category TEXT` (提供分類 contains TEXT, for example `月報`, `年報（実数）`, `参考表`),
`--limit N`, `--version YYYY-MM-DD`, `--delay SECONDS` (default 1, minimum 1),
`--plan-only`.

Requests are sequential with at least one second between them. HTTP 429, 5xx, network
errors and short reads are retried with back-off (30 s doubling, at most 10 min, 8
attempts). A table that still fails is recorded under `failures` in the manifest and
the run goes on; the exit status is then 1.

**Resuming.** Run the same command again. Without `--version` the script reuses the
newest version whose manifest is not `complete`, so a collection started on one day
resumes into the same directory on the next. The listing and counts are not fetched
again; tables whose files match the manifest's sha256 are skipped; a table interrupted
part-way resumes at its next page (pages wait in `data/work/<version>/<id>/`). A run
with no `--only`, `--category` or `--limit` that ends with every listed table present
sets `complete: true`; the next run then starts a new version.

**Verifying.** `02_verify.py` re-hashes every file in the manifest, parses the JSON files
(`STATUS` below 100), reads every `data.csv.gz` to the end with the `csv` module and
checks its row count against `TOTAL_NUMBER`. It exits 1 on any error.

Tests: `uv run python -m pytest` and `uv run ruff check scripts tests`.

## Layout

```
data/raw/<version>/
  MANIFEST.json                      catalogue entry, then per table: listing fields,
                                     cntGetFlg count, and meta/header/data file entries
                                     (path, url without appId, bytes, sha256,
                                     last_modified (the API sends none: null),
                                     fetched_at UTC, rows, TOTAL_NUMBER, UPDATED_DATE,
                                     page URLs); failures
  getStatsList.json                  the listing used for the run, as received
  tables/<statsDataId>/
    getMetaInfo.json                 classification metadata, as received
    header.csv                       the CSV header sections of the first page
                                     (RESULT, RESULT_INF, TABLE_INF, NOTE)
    data.csv.gz                      the VALUE section: the column header line and every
                                     row, UTF-8, gzip (one member per page)
data/work/<version>/<statsDataId>/   pages of a table being fetched (removed when done)
scripts/estat_api.py                 parsing and planning (no network)
scripts/01_download.py               download
scripts/02_verify.py                 verify
tests/                               unit tests with trimmed real responses
```

The columns of `data.csv.gz` follow each table's classifications, as code and label
pairs (`tab_code`, `cat01_code`, ..., `area_code`, `time_code`) followed by `unit`,
`value` and `annotation`. `value` is text: besides numbers it holds symbols that the
`NOTE` lines of `header.csv` explain (for example `***` not surveyed, `-` no value by
definition).
