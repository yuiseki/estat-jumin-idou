#!/usr/bin/env python3
"""Download every 住民基本台帳人口移動報告 table (statsCode 00200523) from the e-Stat API.

Layout under data/raw/<version>/ (version = the date the collection started, UTC):

    MANIFEST.json                      what was fetched, hashes, the source's own counts
    getStatsList.json                  the table listing used to plan the run
    tables/<statsDataId>/getMetaInfo.json
    tables/<statsDataId>/header.csv    the CSV header sections of the first page
    tables/<statsDataId>/data.csv.gz   the VALUE section (column header + all rows)

Pages of a table are kept in data/work/<version>/<statsDataId>/ until the table is
complete, so an interrupted run resumes at the next page.

The application ID is read from the environment (E_STAT_APP_ID) and is never printed
or stored: manifests and logs carry the request URL without it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import http.client
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

import estat_api as ea

REPO = "estat-jumin-idou"
USER_AGENT = f"{REPO}/1 (+https://github.com/yuiseki/{REPO})"
ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
WORK = ROOT / "data" / "work"
# Stored bytes (gzip CSV) per value, measured on the first slice; used only for the estimate.
BYTES_PER_VALUE = 8.0
# Seconds per data request (100,000 values), measured on the first slice; estimate only.
SECONDS_PER_DATA_REQUEST = 12.0
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 8


def utcnow() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def log(msg: str) -> None:
    print(f"{utcnow()} {msg}", flush=True)


class TransientError(RuntimeError):
    pass


class Client:
    """Sequential HTTP client: one request at a time, a fixed gap, back-off on errors."""

    def __init__(self, app_id: str, delay: float):
        self.app_id = app_id
        self.delay = delay
        self.last = 0.0
        self.requests = 0

    def _wait(self) -> None:
        gap = self.delay - (time.monotonic() - self.last)
        if gap > 0:
            time.sleep(gap)

    def _once(self, url: str) -> tuple[bytes, dict]:
        req = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
        )
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                body = resp.read()
                headers = {k.lower(): v for k, v in resp.headers.items()}
        except urllib.error.HTTPError as e:
            # str(e) carries no URL, but never let the exception object travel with it.
            retry_after = e.headers.get("Retry-After") if e.headers else None
            code = e.code
            e.close()
            if code in RETRY_STATUS:
                raise TransientError(f"HTTP {code} (Retry-After {retry_after})") from None
            raise RuntimeError(f"HTTP {code}") from None
        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            http.client.HTTPException,
        ) as e:
            reason = getattr(e, "reason", None) or type(e).__name__
            raise TransientError(f"network error: {reason}") from None
        length = headers.get("content-length")
        if length is not None and int(length) != len(body):
            raise TransientError(f"short read: {len(body)} of {length} bytes")
        if headers.get("content-encoding") == "gzip":
            try:
                body = gzip.decompress(body)
            except (OSError, EOFError, zlib.error):
                raise TransientError("broken gzip transfer") from None
        return body, headers

    def get(self, endpoint: str, params: dict) -> tuple[bytes, dict]:
        url = ea.build_url(endpoint, params, self.app_id)
        shown = ea.public_url(endpoint, params)
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._wait()
            try:
                self.requests += 1
                return self._once(url)
            except TransientError as e:
                if attempt == MAX_ATTEMPTS:
                    raise RuntimeError(f"{shown}: {e}; gave up after {attempt} attempts") from None
                back = min(600, 30 * 2 ** (attempt - 1))
                log(f"  retry {attempt}/{MAX_ATTEMPTS - 1} in {back}s: {shown}: {e}")
                time.sleep(back)
            finally:
                self.last = time.monotonic()
        raise AssertionError("unreachable")


# --- manifest and files ----------------------------------------------------


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_bytes(data)
    os.replace(part, path)


class Manifest:
    def __init__(self, vdir: Path, version: str):
        self.path = vdir / "MANIFEST.json"
        self.vdir = vdir
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self.data = {
                "dataset": REPO,
                "title": "住民基本台帳人口移動報告 (Report on Internal Migration in Japan)",
                "stats_code": ea.STATS_CODE,
                "publisher": "総務省統計局 (Statistics Bureau of Japan)",
                "source": "e-Stat API 3.0 https://www.e-stat.go.jp/api/",
                "version": version,
                "created_at": utcnow(),
                "complete": False,
                "catalogue": None,
                "tables": {},
                "failures": {},
            }

    def save(self) -> None:
        self.data["updated_at"] = utcnow()
        text = json.dumps(self.data, ensure_ascii=False, indent=1, sort_keys=False)
        write_atomic(self.path, text.encode("utf-8"))

    def entry(self, rel: str, url: str, body_or_path, fetched_at: str) -> dict:
        p = self.vdir / rel
        return {
            "path": rel,
            "url": url,
            "bytes": p.stat().st_size,
            "sha256": sha256_file(p)
            if body_or_path is None
            else hashlib.sha256(body_or_path).hexdigest(),
            "last_modified": None,  # the API sends no Last-Modified header
            "fetched_at": fetched_at,
        }

    def file_ok(self, entry: dict | None) -> bool:
        if not entry:
            return False
        p = self.vdir / entry["path"]
        return (
            p.exists() and p.stat().st_size == entry["bytes"] and sha256_file(p) == entry["sha256"]
        )


# --- steps -----------------------------------------------------------------


def fetch_catalogue(client: Client, man: Manifest) -> list[dict]:
    rel = "getStatsList.json"
    entry = man.data.get("catalogue")
    if entry and man.file_ok(entry):
        obj = json.loads((man.vdir / rel).read_text(encoding="utf-8"))
        return ea.parse_stats_list(obj)
    params = {"statsCode": ea.STATS_CODE}
    log(f"getStatsList {ea.public_url('getStatsList', params)}")
    body, _ = client.get("getStatsList", params)
    obj = json.loads(body)
    tables = ea.parse_stats_list(obj)
    write_atomic(man.vdir / rel, body)
    e = man.entry(rel, ea.public_url("getStatsList", params), body, utcnow())
    e["tables_listed"] = len(tables)
    e["overall_total_number_sum"] = sum(t["overall_total_number"] for t in tables)
    man.data["catalogue"] = e
    for t in tables:
        man.data["tables"].setdefault(t["id"], {}).update({"listing": dict(t)})
    man.save()
    return tables


def count_tables(client: Client, man: Manifest, tables: list[dict]) -> None:
    todo = [t for t in tables if man.data["tables"][t["id"]].get("total_number") is None]
    if todo:
        log(f"counting {len(todo)} tables with cntGetFlg=Y")
    for i, t in enumerate(todo, 1):
        params = {"statsDataId": t["id"], "cntGetFlg": "Y", "metaGetFlg": "N"}
        body, _ = client.get("getStatsData", params)
        n = ea.parse_count(json.loads(body))
        rec = man.data["tables"][t["id"]]
        rec["total_number"] = n
        rec["counted_at"] = utcnow()
        if i % 25 == 0 or i == len(todo):
            man.save()
            log(f"  counted {i}/{len(todo)}")
    for t in tables:
        t["total_number"] = man.data["tables"][t["id"]].get("total_number")


def fetch_meta(client: Client, man: Manifest, tid: str) -> None:
    rec = man.data["tables"][tid]
    rel = f"tables/{tid}/getMetaInfo.json"
    if man.file_ok(rec.get("meta")):
        return
    params = {"statsDataId": tid}
    body, _ = client.get("getMetaInfo", params)
    ea.check_json_status(json.loads(body), "GET_META_INFO")
    write_atomic(man.vdir / rel, body)
    rec["meta"] = man.entry(rel, ea.public_url("getMetaInfo", params), body, utcnow())
    man.save()


def _page_files(wdir: Path, page_no: int) -> tuple[Path, Path]:
    return wdir / f"p{page_no:05d}.csv.gz", wdir / f"p{page_no:05d}.json"


def fetch_data(client: Client, man: Manifest, tid: str, version: str) -> None:
    rec = man.data["tables"][tid]
    if man.file_ok(rec.get("data")) and man.file_ok(rec.get("header")):
        return
    wdir = WORK / version / tid
    for attempt in (1, 2):
        try:
            _fetch_pages(client, man, tid, wdir)
            break
        except _TableChanged as e:
            log(f"  {tid}: {e}; restarting the table")
            shutil.rmtree(wdir, ignore_errors=True)
            if attempt == 2:
                raise RuntimeError(f"{tid} changed twice while being fetched") from None
    _assemble(man, tid, wdir)


class _TableChanged(RuntimeError):
    pass


def _fetch_pages(client: Client, man: Manifest, tid: str, wdir: Path) -> None:
    wdir.mkdir(parents=True, exist_ok=True)
    first_info = None
    page_no = 1
    while True:
        gz, info_path = _page_files(wdir, page_no)
        if gz.exists() and info_path.exists():
            info = json.loads(info_path.read_text(encoding="utf-8"))
        else:
            start = ea.page_start(page_no)
            params = {"statsDataId": tid, "startPosition": str(start), "limit": str(ea.PAGE_LIMIT)}
            fetched_at = utcnow()
            body, _ = client.get("getSimpleStatsData", params)
            page = ea.split_simple_csv(body.decode("utf-8"))
            ri = page.result_inf
            if page.table_id != tid:
                raise RuntimeError(f"{tid}: response is for table {page.table_id}")
            if ri["FROM_NUMBER"] != start or ri["TO_NUMBER"] - ri["FROM_NUMBER"] + 1 != page.rows:
                raise RuntimeError(
                    f"{tid} page {page_no}: RESULT_INF {ri} does not match {page.rows} rows"
                )
            text = page.value_csv if page_no == 1 else ea.strip_column_header(page.value_csv)
            write_atomic(gz, gzip.compress(text.encode("utf-8"), compresslevel=6))
            if page_no == 1:
                write_atomic(wdir / "header.csv", page.header.encode("utf-8"))
            info = {
                "page": page_no,
                "url": ea.public_url("getSimpleStatsData", params),
                "fetched_at": fetched_at,
                "result_inf": ri,
                "updated_date": page.updated_date,
                "rows": page.rows,
            }
            write_atomic(info_path, json.dumps(info, ensure_ascii=False).encode("utf-8"))
        if first_info is None:
            first_info = info
        elif (info["updated_date"], info["result_inf"]["TOTAL_NUMBER"]) != (
            first_info["updated_date"],
            first_info["result_inf"]["TOTAL_NUMBER"],
        ):
            raise _TableChanged(
                f"page {page_no} has UPDATED_DATE {info['updated_date']} / TOTAL_NUMBER "
                f"{info['result_inf']['TOTAL_NUMBER']}, page 1 had {first_info['updated_date']} / "
                f"{first_info['result_inf']['TOTAL_NUMBER']}"
            )
        if info["result_inf"]["NEXT_KEY"] is None:
            return
        if info["result_inf"]["NEXT_KEY"] != ea.page_start(page_no + 1):
            raise RuntimeError(f"{tid}: unexpected NEXT_KEY {info['result_inf']['NEXT_KEY']}")
        page_no += 1


def _assemble(man: Manifest, tid: str, wdir: Path) -> None:
    rec = man.data["tables"][tid]
    infos = sorted(
        (json.loads(p.read_text(encoding="utf-8")) for p in wdir.glob("p*.json")),
        key=lambda i: i["page"],
    )
    total = infos[0]["result_inf"]["TOTAL_NUMBER"]
    rows = sum(i["rows"] for i in infos)
    if rows != total:
        raise RuntimeError(f"{tid}: {rows} rows fetched, TOTAL_NUMBER is {total}")
    tdir = man.vdir / "tables" / tid
    tdir.mkdir(parents=True, exist_ok=True)
    rel_data = f"tables/{tid}/data.csv.gz"
    part = man.vdir / (rel_data + ".part")
    with open(part, "wb") as out:
        for i in infos:
            with open(_page_files(wdir, i["page"])[0], "rb") as f:
                shutil.copyfileobj(f, out)
    with open(part, "rb") as f:
        _, counted = ea.count_gzip_csv_rows(f)
    if counted != total:
        raise RuntimeError(f"{tid}: assembled file has {counted} rows, expected {total}")
    os.replace(part, man.vdir / rel_data)
    rel_header = f"tables/{tid}/header.csv"
    shutil.copyfile(wdir / "header.csv", man.vdir / rel_header)
    url = ea.public_url("getSimpleStatsData", {"statsDataId": tid})
    d = man.entry(rel_data, url, None, infos[0]["fetched_at"])
    d.update(
        {
            "fetched_until": infos[-1]["fetched_at"],
            "rows": counted,
            "total_number": total,
            "updated_date": infos[0]["updated_date"],
            "requests": len(infos),
            "page_urls": [i["url"] for i in infos],
        }
    )
    rec["data"] = d
    rec["header"] = man.entry(rel_header, url, None, infos[0]["fetched_at"])
    listing_updated = rec.get("listing", {}).get("updated_date")
    if listing_updated and listing_updated != d["updated_date"]:
        log(f"  {tid}: UPDATED_DATE {d['updated_date']} differs from listing {listing_updated}")
    man.data["failures"].pop(tid, None)
    man.save()
    shutil.rmtree(wdir, ignore_errors=True)


# --- main ------------------------------------------------------------------


def print_estimate(label: str, tables: list[dict], done: set[str]) -> None:
    todo = [t for t in tables if t["id"] not in done]
    est = ea.estimate(todo, BYTES_PER_VALUE)
    hours = (
        est["data_requests"] * SECONDS_PER_DATA_REQUEST
        + (est["meta_requests"] + est["count_requests"]) * 2.0
    ) / 3600
    log(
        f"{label}: {len(tables)} tables selected, {len(done)} already done, {est['tables']} to fetch; "
        f"{est['values']:,} values; about {est['requests']:,} requests "
        f"({est['count_requests']} count, {est['meta_requests']} meta, {est['data_requests']} data); "
        f"about {est['bytes'] / 1e9:.2f} GB stored; about {hours:.1f} h"
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--only", nargs="+", metavar="ID", help="statsDataId(s) to fetch (space or comma separated)"
    )
    ap.add_argument("--category", help="only tables whose 提供分類 contains this text, e.g. 月報")
    ap.add_argument("--limit", type=int, help="only the first N selected tables (by id)")
    ap.add_argument(
        "--version", help="version directory (default: the newest unfinished one, else today UTC)"
    )
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests (minimum 1)")
    ap.add_argument(
        "--plan-only",
        action="store_true",
        help="count the selected tables, print the estimate, stop",
    )
    args = ap.parse_args(argv)

    app_id = os.environ.get("E_STAT_APP_ID")
    if not app_id:
        print("E_STAT_APP_ID is not set in the environment", file=sys.stderr)
        return 2
    only = [x for item in (args.only or []) for x in item.split(",") if x]
    restricted = bool(only or args.category or args.limit is not None)

    version = args.version or ea.choose_version(RAW)
    vdir = RAW / version
    vdir.mkdir(parents=True, exist_ok=True)
    log(f"version {version} -> {vdir}")
    client = Client(app_id, max(1.0, args.delay))
    man = Manifest(vdir, version)

    tables = fetch_catalogue(client, man)
    try:
        selected = ea.select_tables(tables, only=only, category=args.category, limit=args.limit)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    done = {
        t["id"]
        for t in selected
        if man.file_ok(man.data["tables"][t["id"]].get("data"))
        and man.file_ok(man.data["tables"][t["id"]].get("meta"))
    }
    for t in selected:
        t["total_number"] = man.data["tables"][t["id"]].get("total_number")
    print_estimate("plan (listing counts where not counted yet)", selected, done)
    count_tables(client, man, [t for t in selected if t["id"] not in done])
    print_estimate("plan (cntGetFlg=Y counts)", selected, done)
    if args.plan_only:
        return 0

    failed = []
    todo = [t for t in selected if t["id"] not in done]
    for i, t in enumerate(todo, 1):
        tid = t["id"]
        log(
            f"[{i}/{len(todo)}] {tid} {t['total_number']:,} values ({t['category']}) {t['title'][:60]}"
        )
        try:
            fetch_meta(client, man, tid)
            fetch_data(client, man, tid, version)
        except (RuntimeError, ea.ApiError, ValueError, OSError) as e:
            log(f"  FAILED {tid}: {e}")
            man.data["failures"][tid] = {"error": str(e), "at": utcnow()}
            man.save()
            failed.append(tid)

    all_done = all(
        man.file_ok(rec.get("data")) and man.file_ok(rec.get("meta"))
        for rec in man.data["tables"].values()
    )
    if not restricted and all_done and not failed:
        man.data["complete"] = True
        man.data["completed_at"] = utcnow()
        man.save()
    log(
        f"done: {client.requests} requests this run, {len(failed)} failed tables"
        + (f": {' '.join(failed)}" if failed else "")
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
