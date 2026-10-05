"""Parsing and planning helpers for the e-Stat API (version 3.0).

Kept free of network code so that it can be tested with small fixtures.
The application ID is a secret: it is only ever put into a URL by
``build_url`` and every URL that is printed or saved goes through
``mask_url`` or is built with ``public_url`` (which has no appId at all).
"""

from __future__ import annotations

import csv
import datetime as dt
import gzip
import io
import json
import math
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

API_BASE = "https://api.e-stat.go.jp/rest/3.0/app"
JSON_ENDPOINTS = {"getStatsList", "getMetaInfo", "getStatsData"}
PAGE_LIMIT = 100_000  # values per getStatsData / getSimpleStatsData request (API maximum)
STATS_CODE = "00200523"  # 住民基本台帳人口移動報告


class ApiError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"e-Stat API STATUS {status}: {message}")
        self.status = status
        self.message = message


# --- URLs ------------------------------------------------------------------


def _endpoint_path(endpoint: str) -> str:
    if endpoint in JSON_ENDPOINTS:
        return f"{API_BASE}/json/{endpoint}"
    return f"{API_BASE}/{endpoint}"


def build_url(endpoint: str, params: dict, app_id: str) -> str:
    """URL with the secret. Never print or store the return value."""
    query = urllib.parse.urlencode({"appId": app_id, **params})
    return f"{_endpoint_path(endpoint)}?{query}"


def public_url(endpoint: str, params: dict) -> str:
    """The same request without the appId, for manifests and logs."""
    return f"{_endpoint_path(endpoint)}?{urllib.parse.urlencode(params)}"


def mask_url(url: str) -> str:
    return re.sub(r"(appId=)[^&]*", r"\1***", url)


# --- JSON responses --------------------------------------------------------


def check_json_status(obj: dict, root: str) -> int:
    """Return STATUS; raise ApiError for real errors.

    STATUS 0 is success, 1 is "no matching data", 2 is success with a
    warning. 100 and above are errors.
    """
    result = obj[root]["RESULT"]
    status = int(result["STATUS"])
    if status >= 100:
        raise ApiError(status, str(result.get("ERROR_MSG", "")))
    return status


def _text(value) -> str:
    if isinstance(value, dict):
        return str(value.get("$", ""))
    return "" if value is None else str(value)


def parse_stats_list(obj: dict) -> list[dict]:
    """Flatten a getStatsList response into one dict per table, sorted by id."""
    check_json_status(obj, "GET_STATS_LIST")
    inf = obj["GET_STATS_LIST"]["DATALIST_INF"]
    raw = inf.get("TABLE_INF", [])
    if isinstance(raw, dict):
        raw = [raw]
    number = int(inf.get("NUMBER", len(raw)))
    if number != len(raw):
        raise ApiError(-1, f"getStatsList returned {len(raw)} of {number} tables (paging needed)")
    tables = []
    for t in raw:
        spec = t.get("STATISTICS_NAME_SPEC") or {}
        tables.append(
            {
                "id": t["@id"],
                "statistics_name": _text(t.get("STATISTICS_NAME")),
                "category": _text(spec.get("TABULATION_SUB_CATEGORY1")),
                "title": _text(t.get("TITLE")),
                "title_no": t["TITLE"].get("@no") if isinstance(t.get("TITLE"), dict) else None,
                "cycle": _text(t.get("CYCLE")),
                "survey_date": str(t.get("SURVEY_DATE", "")),
                "open_date": _text(t.get("OPEN_DATE")),
                "updated_date": _text(t.get("UPDATED_DATE")),
                "overall_total_number": int(t.get("OVERALL_TOTAL_NUMBER", 0)),
            }
        )
    tables.sort(key=lambda x: x["id"])
    return tables


def parse_count(obj: dict) -> int:
    """TOTAL_NUMBER from getStatsData with cntGetFlg=Y (0 when no data)."""
    status = check_json_status(obj, "GET_STATS_DATA")
    if status == 1:
        return 0
    return int(obj["GET_STATS_DATA"]["STATISTICAL_DATA"]["RESULT_INF"]["TOTAL_NUMBER"])


# --- planning --------------------------------------------------------------


def pages_needed(total: int, limit: int = PAGE_LIMIT) -> int:
    """Data requests for a table; an empty table still takes one request."""
    return max(1, math.ceil(total / limit))


def page_start(page_no: int, limit: int = PAGE_LIMIT) -> int:
    """startPosition of the 1-based page number."""
    return 1 + (page_no - 1) * limit


def select_tables(
    tables: list[dict],
    only: list[str] | None = None,
    category: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    selected = tables
    if only:
        wanted = set(only)
        known = {t["id"] for t in tables}
        missing = sorted(wanted - known)
        if missing:
            raise ValueError(f"table ids not in getStatsList: {', '.join(missing)}")
        selected = [t for t in selected if t["id"] in wanted]
    if category:
        selected = [t for t in selected if category in t["category"]]
    if limit is not None:
        selected = selected[:limit]
    return selected


def estimate(tables: list[dict], bytes_per_value: float) -> dict:
    """Requests and stored bytes for the given tables.

    ``total_number`` is the cntGetFlg=Y count; when it is not known yet the
    listing's OVERALL_TOTAL_NUMBER stands in and a count request is added.
    """
    values = 0
    data_requests = 0
    count_requests = 0
    for t in tables:
        n = t.get("total_number")
        if n is None:
            count_requests += 1
            n = t.get("overall_total_number", 0)
        values += n
        data_requests += pages_needed(n)
    meta_requests = len(tables)
    return {
        "tables": len(tables),
        "values": values,
        "count_requests": count_requests,
        "meta_requests": meta_requests,
        "data_requests": data_requests,
        "requests": count_requests + meta_requests + data_requests,
        "bytes": int(values * bytes_per_value),
    }


# --- getSimpleStatsData CSV ------------------------------------------------


@dataclass
class CsvPage:
    header: str  # everything before the "VALUE" line (RESULT, RESULT_INF, TABLE_INF, NOTE ...)
    value_csv: str  # column header line plus data rows
    result_inf: dict
    table_id: str | None
    updated_date: str | None
    rows: int


def _first_records(text: str):
    yield from csv.reader(io.StringIO(text))


def split_simple_csv(text: str) -> CsvPage:
    """Split a getSimpleStatsData CSV page (sectionHeaderFlg=1) into its parts."""
    marker = '"VALUE"\n'
    pos = text.find(marker)
    header = text if pos < 0 else text[:pos]
    records = list(_first_records(header))
    fields: dict[str, list[str]] = {}
    for rec in records:
        if rec and rec[0] not in fields:
            fields[rec[0]] = rec[1:]
    status = int(fields.get("STATUS", ["-1"])[0])
    if status >= 100 or pos < 0:
        if status in (0, 1, 2) and pos < 0:
            raise ApiError(-1, "CSV response has no VALUE section")
        raise ApiError(status, (fields.get("ERROR_MSG") or [""])[0])

    def num(key):
        v = fields.get(key)
        return int(v[0]) if v and v[0] != "" else None

    result_inf = {
        "TOTAL_NUMBER": num("TOTAL_NUMBER"),
        "FROM_NUMBER": num("FROM_NUMBER"),
        "TO_NUMBER": num("TO_NUMBER"),
        "NEXT_KEY": num("NEXT_KEY"),
    }
    value_csv = text[pos + len(marker) :]
    rows = sum(1 for _ in csv.reader(io.StringIO(value_csv))) - 1
    return CsvPage(
        header=header,
        value_csv=value_csv,
        result_inf=result_inf,
        table_id=(fields.get("TABLE_INF") or [None])[0],
        updated_date=(fields.get("UPDATED_DATE") or [None])[0],
        rows=max(rows, 0),
    )


def strip_column_header(value_csv: str) -> str:
    """Drop the column header line (pages after the first)."""
    nl = value_csv.find("\n")
    return "" if nl < 0 else value_csv[nl + 1 :]


def count_gzip_csv_rows(fileobj) -> tuple[list[str], int]:
    """Read a (possibly multi-member) gzip CSV to the end; return header and row count."""
    with gzip.open(fileobj, "rt", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, [])
        rows = sum(1 for _ in reader)
    return header, rows


# --- versions --------------------------------------------------------------


def today_utc() -> str:
    return dt.datetime.now(dt.UTC).date().isoformat()


def choose_version(raw_dir: Path, today: str | None = None) -> str:
    """Reuse the newest unfinished version so a run resumes; else today's date."""
    today = today or today_utc()
    raw_dir = Path(raw_dir)
    if raw_dir.is_dir():
        versions = sorted(
            p.name
            for p in raw_dir.iterdir()
            if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name)
        )
        for name in reversed(versions):
            manifest = raw_dir / name / "MANIFEST.json"
            if manifest.exists():
                try:
                    if not json.loads(manifest.read_text(encoding="utf-8")).get("complete"):
                        return name
                except json.JSONDecodeError:
                    return name
    return today
