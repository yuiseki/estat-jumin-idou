import gzip
import io
import json

import estat_api as ea
import pytest
from conftest import FIXTURES


def load_json(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# --- URLs and the secret -------------------------------------------------


def test_build_url_puts_app_id_first_and_mask_hides_it():
    url = ea.build_url("getSimpleStatsData", {"statsDataId": "0000180002"}, "SECRET123")
    assert url.startswith("https://api.e-stat.go.jp/rest/3.0/app/getSimpleStatsData?")
    assert "appId=SECRET123" in url
    masked = ea.mask_url(url)
    assert "SECRET123" not in masked
    assert "appId=***" in masked
    assert "statsDataId=0000180002" in masked


def test_json_endpoints_use_json_path():
    url = ea.build_url("getStatsList", {"statsCode": "00200523"}, "K")
    assert "/rest/3.0/app/json/getStatsList?" in url


def test_public_url_has_no_app_id():
    url = ea.public_url("getMetaInfo", {"statsDataId": "0000180002"})
    assert "appId" not in url
    assert url.endswith("getMetaInfo?statsDataId=0000180002")


# --- getStatsList ----------------------------------------------------------


def test_parse_stats_list():
    tables = ea.parse_stats_list(load_json("getStatsList_trimmed.json"))
    assert [t["id"] for t in tables] == ["0000180002", "0003419945", "0003423635"]
    t = {t["id"]: t for t in tables}
    small = t["0000180002"]
    assert small["overall_total_number"] == 182
    assert small["updated_date"] == "2023-10-31"
    assert small["survey_date"] == "199901-199912"
    assert small["category"] == "平成11年住民基本台帳人口移動報告"
    assert small["title"].startswith("年計・月")
    big = t["0003419945"]
    assert big["survey_date"] == "0"
    assert big["category"] == "年報（実数）"
    assert big["overall_total_number"] == 7513380


def test_parse_stats_list_single_table_dict():
    obj = load_json("getStatsList_trimmed.json")
    inf = obj["GET_STATS_LIST"]["DATALIST_INF"]
    inf["TABLE_INF"] = inf["TABLE_INF"][0]
    inf["NUMBER"] = 1
    assert len(ea.parse_stats_list(obj)) == 1


def test_parse_stats_list_rejects_partial_listing():
    obj = load_json("getStatsList_trimmed.json")
    obj["GET_STATS_LIST"]["DATALIST_INF"]["NUMBER"] = 999
    with pytest.raises(ea.ApiError):
        ea.parse_stats_list(obj)


def test_json_status_error_raises():
    obj = {"GET_STATS_DATA": {"RESULT": {"STATUS": 300, "ERROR_MSG": "x"}}}
    with pytest.raises(ea.ApiError):
        ea.check_json_status(obj, "GET_STATS_DATA")


# --- counts and planning ---------------------------------------------------


def test_parse_count():
    assert ea.parse_count(load_json("count_0000180002.json")) == 182


def test_parse_count_no_data_is_zero():
    obj = {"GET_STATS_DATA": {"RESULT": {"STATUS": 1, "ERROR_MSG": "no data"}}}
    assert ea.parse_count(obj) == 0


@pytest.mark.parametrize(
    "total,expected",
    [(0, 1), (1, 1), (100000, 1), (100001, 2), (7513380, 76)],
)
def test_pages_needed(total, expected):
    assert ea.pages_needed(total) == expected


def test_page_start():
    assert ea.page_start(1) == 1
    assert ea.page_start(3) == 200001


def test_select_tables():
    tables = ea.parse_stats_list(load_json("getStatsList_trimmed.json"))
    assert [t["id"] for t in ea.select_tables(tables, only=["0003423635"])] == ["0003423635"]
    assert [t["id"] for t in ea.select_tables(tables, category="年報")] == ["0003419945"]
    assert len(ea.select_tables(tables, limit=2)) == 2
    with pytest.raises(ValueError):
        ea.select_tables(tables, only=["0000000000"])


def test_estimate_counts_requests_and_bytes():
    tables = [
        {"id": "a", "total_number": 182},
        {"id": "b", "total_number": 250000},
        {"id": "c", "total_number": None, "overall_total_number": 10},
    ]
    est = ea.estimate(tables, bytes_per_value=10)
    # a: 1 page, b: 3 pages, c: 1 page; each table also 1 meta request; c needs a count
    assert est["data_requests"] == 5
    assert est["meta_requests"] == 3
    assert est["count_requests"] == 1
    assert est["requests"] == 9
    assert est["values"] == 182 + 250000 + 10
    assert est["bytes"] == (182 + 250000 + 10) * 10


# --- CSV pages -------------------------------------------------------------


def test_split_simple_csv_page_with_next_key():
    text = (FIXTURES / "simple_page_0003420481.csv").read_text(encoding="utf-8")
    page = ea.split_simple_csv(text)
    assert page.result_inf == {
        "TOTAL_NUMBER": 550128,
        "FROM_NUMBER": 1,
        "TO_NUMBER": 5,
        "NEXT_KEY": 6,
    }
    assert page.table_id == "0003420481"
    assert page.updated_date == "2026-02-03"
    assert page.header.rstrip("\n").endswith('"NOTE","-","定義上該当数値がないもの"')
    lines = page.value_csv.splitlines()
    assert lines[0].startswith('"tab_code","表章項目"')
    assert len(lines) == 6
    assert page.rows == 5


def test_split_simple_csv_last_page():
    text = (FIXTURES / "simple_complete_0000180002_trimmed.csv").read_text(encoding="utf-8")
    page = ea.split_simple_csv(text)
    assert page.result_inf["NEXT_KEY"] is None
    assert page.result_inf["TOTAL_NUMBER"] == 182
    assert page.rows == 4


def test_split_simple_csv_error():
    text = (FIXTURES / "simple_error.csv").read_text(encoding="utf-8")
    with pytest.raises(ea.ApiError) as e:
        ea.split_simple_csv(text)
    assert e.value.status == 300


def test_strip_column_header():
    text = (FIXTURES / "simple_page_0003420481.csv").read_text(encoding="utf-8")
    page = ea.split_simple_csv(text)
    body = ea.strip_column_header(page.value_csv)
    assert not body.startswith('"tab_code"')
    assert len(body.splitlines()) == 5


def test_count_csv_rows_in_gzip_members():
    # two gzip members concatenated, the second without the column header
    buf = io.BytesIO()
    buf.write(gzip.compress(b'"a","b"\n"1","x\ny"\n'))
    buf.write(gzip.compress(b'"2","z"\n'))
    buf.seek(0)
    header, rows = ea.count_gzip_csv_rows(buf)
    assert header == ["a", "b"]
    assert rows == 2


# --- versions --------------------------------------------------------------


def test_choose_version_reuses_incomplete(tmp_path):
    raw = tmp_path / "raw"
    (raw / "2026-10-01").mkdir(parents=True)
    (raw / "2026-10-01" / "MANIFEST.json").write_text(json.dumps({"complete": True}))
    (raw / "2026-10-05").mkdir()
    (raw / "2026-10-05" / "MANIFEST.json").write_text(json.dumps({"complete": False}))
    assert ea.choose_version(raw, today="2026-10-06") == "2026-10-05"


def test_choose_version_new_when_all_complete(tmp_path):
    raw = tmp_path / "raw"
    (raw / "2026-10-01").mkdir(parents=True)
    (raw / "2026-10-01" / "MANIFEST.json").write_text(json.dumps({"complete": True}))
    assert ea.choose_version(raw, today="2026-10-06") == "2026-10-06"
    assert ea.choose_version(tmp_path / "missing", today="2026-10-06") == "2026-10-06"
