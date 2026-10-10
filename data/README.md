---
license: other
license_name: e-stat-terms-of-use-pdl
license_link: https://www.e-stat.go.jp/terms-of-use
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
that the e-Stat database serves through the e-Stat API: 471 tables and
214,274,401 values, as the API served them on 2026-10-05.

Source: 「住民基本台帳人口移動報告」(総務省統計局), e-Stat
(<https://www.e-stat.go.jp/stat-search/database?toukei=00200523>). The data are reused under
the e-Stat terms of use (https://www.e-stat.go.jp/terms-of-use), compatible with CC BY 4.0; this copy is edited (see
Layout), and the API credit required by its terms is at the end of this card.

## The data follows upstream corrections

The tables are overwritten in place at the source (tables from 2020 on grow with each month
and year under the same `statsDataId`; titles and `UPDATED_DATE` can change). This dataset
therefore holds one run, the newest, and is replaced as a whole when it is fetched again;
older runs are not kept here. The current run is `raw/2026-10-05/`.

## Subsets

| subset | rows | what it is |
|---|---:|---|
| `observations` | 214,274,401 | every value of every table, one row per value |
| `tables` | 471 | one row per table: title, category, period, update date, rows, and the name of each classification slot |

Tables by e-Stat category (提供分類):

| category | tables | values |
|---|---:|---:|
| 平成11年住民基本台帳人口移動報告 | 17 | 1,080,603 |
| 平成12年住民基本台帳人口移動報告 | 17 | 1,080,628 |
| 平成13年住民基本台帳人口移動報告 | 17 | 1,078,868 |
| 平成14年住民基本台帳人口移動報告 | 18 | 1,081,653 |
| 平成15年住民基本台帳人口移動報告 | 18 | 1,085,936 |
| 平成16年住民基本台帳人口移動報告 | 18 | 1,014,237 |
| 平成17年住民基本台帳人口移動報告 | 19 | 746,907 |
| 平成18年住民基本台帳人口移動報告 | 19 | 670,524 |
| 季報 | 4 | 1,277,376 |
| 平成19年住民基本台帳人口移動報告 | 15 | 685,029 |
| 平成20年住民基本台帳人口移動報告 | 15 | 680,624 |
| 平成21年住民基本台帳人口移動報告 | 15 | 687,239 |
| 平成22年住民基本台帳人口移動報告 | 18 | 762,523 |
| 平成23年住民基本台帳人口移動報告 | 18 | 759,014 |
| 平成24年住民基本台帳人口移動報告 | 18 | 770,367 |
| 平成25年住民基本台帳人口移動報告 | 18 | 770,502 |
| 平成26年住民基本台帳人口移動報告 | 17 | 1,207,020 |
| 平成27年住民基本台帳人口移動報告 | 17 | 1,207,170 |
| 参考表　2012年～2017年（年齢（10歳階級），男女，転入・転出市区町村別結果） | 12 | 60,324,507 |
| 平成28年住民基本台帳人口移動報告 | 17 | 1,207,320 |
| 平成29年住民基本台帳人口移動報告 | 17 | 1,207,395 |
| 平成30年住民基本台帳人口移動報告 | 17 | 1,840,486 |
| 参考表　2018年～　（年齢（10歳階級），男女，転入・転出市区町村別結果（移動者（外国人含む）） | 16 | 84,333,528 |
| 参考表　2018年～　（転入・転出市区町村別結果（移動者（外国人含む），日本人移動者，外国人移動者）） | 16 | 3,284,523 |
| 平成31年・令和元年住民基本台帳人口移動報告 | 20 | 3,102,262 |
| 年報（実数） | 15 | 25,506,246 |
| 月報 | 11 | 7,482,198 |
| 年報（人口推計に基づく移動率）　2020年～2024年 | 4 | 64,425 |
| 年報（年齢別）2010年～2019年 | 18 | 7,925,184 |
| 年報（総数）2019年以前 | 7 | 1,306,040 |
| 年報（住民基本台帳人口に基づく移動率）  2025年～ | 3 | 44,067 |

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
| `value` | VARCHAR | the value as received: a number, or a symbol such as `***` (not surveyed) or `-` (no value by definition) whose meaning is in the table's `NOTE` lines (`raw/2026-10-05/tables/<id>/header.csv`) |
| `value_num` | DOUBLE | `value` when it is a plain number (`-?[0-9]+(.[0-9]+)?`), otherwise NULL; 214,120,756 of the rows |
| `annotation` | VARCHAR | the API's annotation, NULL when empty |

```sql
-- inflow to Tokyo from each prefecture in one table: read the slot names first
select * from tables where stats_data_id = '0003420481';
select area_label, cat01_label, value_num
from observations where stats_data_id = '0003420481' limit 10;
```

## Layout

```
parquet/observations/<statsDataId>.parquet   471 files
parquet/tables.parquet
raw/2026-10-05/MANIFEST.json                 file entries with sha256, URLs (no appId), counts
raw/2026-10-05/getStatsList.json             the listing of the run, as received
raw/2026-10-05/tables/<id>/getMetaInfo.json  classification metadata, as received
raw/2026-10-05/tables/<id>/header.csv        RESULT, TABLE_INF, NOTE sections of the first page
raw/2026-10-05/tables/<id>/data.csv.gz       the VALUE section of the API's CSV, every row
```

The Parquet was written from `data.csv.gz` by `scripts/03_export_parquet.py` and compared
row by row with a second reader (`scripts/04_verify_parquet.py`): every code, label, unit,
value and annotation of all 214,274,401 rows equals the raw file. Not included: the files-only
tables on the e-Stat file pages (the long-term series from 1954, the 1999-2006 detailed
tables, 2000-2005 quarterly files), which have no API.

## Licence

e-Stat terms of use (https://www.e-stat.go.jp/terms-of-use), which follow the Government of Japan Standard Terms of Use
(version 2.0) and are compatible with CC BY 4.0: reuse, redistribution and commercial use
are allowed with attribution, and edited data must say so. This dataset is edited (reshaped
into Parquet with the classifications mapped to slots).

Credit required by article 7 of the e-Stat API terms of use (https://www.e-stat.go.jp/api/terms-of-use):

> このサービスは、政府統計総合窓口(e-Stat)のAPI機能を使用していますが、サービスの内容は国によって保証されたものではありません。

(This service uses the API of the Portal Site of Official Statistics of Japan (e-Stat); its
content is not guaranteed by the Government of Japan.)

Collection and conversion code: <https://github.com/yuiseki/estat-jumin-idou> (MIT).
Retrieved 2026-10-05 to 2026-10-06 (UTC).
