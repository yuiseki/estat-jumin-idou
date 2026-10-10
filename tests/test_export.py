import gzip
import importlib.util
import sys
from pathlib import Path

import duckdb
import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "scripts"))
_spec = importlib.util.spec_from_file_location(
    "export", HERE.parent / "scripts" / "03_export_parquet.py"
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

HEADER = (
    '"tab_code","表章項目","cat01_code","年齢","area_code","地域","time_code","時間軸",'
    '"unit","value","annotation"'
)
ROWS = [
    '"100","転入者数","001","総数","00000","全国","2020000000","2020年","人","1234",""',
    '"100","転入者数","002","0～4歳","00000","全国","2020000000","2020年","人","***","x"',
    '"100","転入者数","003","5～9歳","00000","全国","2020000000","2020年","人","-12.5",""',
]


def _gz(tmp_path, header=HEADER, rows=ROWS):
    p = tmp_path / "data.csv.gz"
    p.write_bytes(gzip.compress(("\n".join([header, *rows]) + "\n").encode()))
    return p


def test_slots_pairs_each_code_with_the_label_column_after_it():
    cols = m.parse_header(HEADER)
    assert [(s, c, lab, n) for s, c, lab, n in m.slots(cols)] == [
        ("tab", 0, 1, "表章項目"),
        ("cat01", 2, 3, "年齢"),
        ("area", 4, 5, "地域"),
        ("time", 6, 7, "時間軸"),
    ]


def test_a_header_that_does_not_end_with_unit_value_annotation_is_refused():
    with pytest.raises(SystemExit, match="unit,value,annotation"):
        m.slots(m.parse_header('"tab_code","x","value"'))


def test_a_label_column_missing_after_a_code_is_refused():
    with pytest.raises(SystemExit, match="label"):
        m.slots(m.parse_header('"tab_code","cat01_code","a","unit","value","annotation"'))


def test_export_table_writes_fixed_columns_and_numbers(tmp_path):
    out = tmp_path / "0001.parquet"
    n, names = m.export_table(duckdb.connect(), "0001", _gz(tmp_path), out)
    assert n == 3
    assert names["tab"] == "表章項目" and names["cat02"] is None
    cols = [r[0] for r in duckdb.sql(f"describe select * from '{out}'").fetchall()]
    assert cols == m.OUTPUT_COLUMNS
    rows = duckdb.sql(
        f"select cat01_code, cat01_label, cat02_code, value, value_num, annotation "
        f"from '{out}'"
    ).fetchall()
    assert rows == [
        ("001", "総数", None, "1234", 1234.0, None),
        ("002", "0～4歳", None, "***", None, "x"),
        ("003", "5～9歳", None, "-12.5", -12.5, None),
    ]


def test_value_that_looks_numeric_but_is_not_parsed_stays_text_only(tmp_path):
    rows = [ROWS[0].replace('"1234"', '"1e400"'), ROWS[1].replace('"***"', '"1.2.3"')]
    out = tmp_path / "x.parquet"
    m.export_table(duckdb.connect(), "0001", _gz(tmp_path, rows=rows), out)
    got = duckdb.sql(f"select value, value_num from '{out}'").fetchall()
    assert got == [("1e400", None), ("1.2.3", None)]


def test_row_count_must_match_the_manifest(tmp_path):
    out = tmp_path / "x.parquet"
    with pytest.raises(SystemExit, match="rows"):
        m.export_table(duckdb.connect(), "0001", _gz(tmp_path), out, expect_rows=4)
