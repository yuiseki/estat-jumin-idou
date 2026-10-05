#!/usr/bin/env python3
"""Verify a downloaded version against its MANIFEST.json.

For every file in the manifest: size and sha256. JSON files must parse with
STATUS < 100. Every data.csv.gz is read to the end with the csv module and its
row count must equal the TOTAL_NUMBER the API reported for that table.
Exits non-zero on any failure. With --require-complete, tables in the listing
that have not been fetched also count as failures.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import estat_api as ea

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_file(vdir: Path, entry: dict, errors: list[str]) -> bool:
    p = vdir / entry["path"]
    if not p.exists():
        errors.append(f"missing {entry['path']}")
        return False
    if p.stat().st_size != entry["bytes"]:
        errors.append(f"size {entry['path']}: {p.stat().st_size} != {entry['bytes']}")
        return False
    if sha256_file(p) != entry["sha256"]:
        errors.append(f"sha256 {entry['path']}")
        return False
    return True


def check_json(vdir: Path, entry: dict, root: str, errors: list[str]) -> None:
    try:
        ea.check_json_status(json.loads((vdir / entry["path"]).read_text(encoding="utf-8")), root)
    except (ValueError, KeyError, ea.ApiError) as e:
        errors.append(f"json {entry['path']}: {e}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--version", help="version directory under data/raw (default: newest)")
    ap.add_argument(
        "--require-complete", action="store_true", help="fail if any listed table is missing"
    )
    args = ap.parse_args(argv)

    versions = sorted(p.name for p in RAW.glob("*/MANIFEST.json") for p in [p.parent])
    if not versions:
        print("no version with a MANIFEST.json under data/raw", file=sys.stderr)
        return 1
    version = args.version or versions[-1]
    vdir = RAW / version
    man = json.loads((vdir / "MANIFEST.json").read_text(encoding="utf-8"))
    errors: list[str] = []

    cat = man.get("catalogue")
    if not cat:
        errors.append("no catalogue entry")
    elif check_file(vdir, cat, errors):
        check_json(vdir, cat, "GET_STATS_LIST", errors)

    fetched = missing = rows_total = 0
    for tid, rec in sorted(man["tables"].items()):
        if "data" not in rec:
            missing += 1
            if args.require_complete:
                errors.append(f"{tid}: not fetched")
            continue
        fetched += 1
        if "meta" not in rec or "header" not in rec:
            errors.append(f"{tid}: data without meta or header")
            continue
        if check_file(vdir, rec["meta"], errors):
            check_json(vdir, rec["meta"], "GET_META_INFO", errors)
        check_file(vdir, rec["header"], errors)
        d = rec["data"]
        if not check_file(vdir, d, errors):
            continue
        try:
            with open(vdir / d["path"], "rb") as f:
                header, rows = ea.count_gzip_csv_rows(f)
        except (OSError, EOFError, UnicodeDecodeError) as e:
            errors.append(f"{tid}: gzip/csv read failed: {e}")
            continue
        if not header or not header[-1:] or "value" not in header:
            errors.append(f"{tid}: unexpected column header {header[:4]}")
        if rows != d["total_number"] or rows != d["rows"]:
            errors.append(
                f"{tid}: {rows} rows, manifest rows {d['rows']}, TOTAL_NUMBER {d['total_number']}"
            )
        if rec.get("total_number") is not None and rec["total_number"] != d["total_number"]:
            print(
                f"note {tid}: count before fetch {rec['total_number']} != TOTAL_NUMBER at fetch {d['total_number']}"
            )
        rows_total += rows

    print(
        f"version {version}: {fetched} tables verified ({rows_total:,} rows), "
        f"{missing} listed tables not fetched, complete={man.get('complete')}, "
        f"{len(man.get('failures', {}))} recorded failures, {len(errors)} errors"
    )
    for e in errors[:50]:
        print("ERROR", e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
