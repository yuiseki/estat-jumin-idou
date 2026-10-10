import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("publish", HERE.parent / "src" / "publish.py")
p = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(p)


def _tree(tmp_path, card=None, failures=None, stray=False):
    d = tmp_path / "data"
    run = d / "raw" / "2026-01-01"
    t = run / "tables" / "T1"
    t.mkdir(parents=True)
    entries = {}
    for name, body in (("getMetaInfo.json", b"{}"), ("header.csv", b"x"), ("data.csv.gz", b"y")):
        (t / name).write_bytes(body)
        entries[name] = {"path": f"tables/T1/{name}", "sha256": hashlib.sha256(body).hexdigest()}
    (run / "getStatsList.json").write_bytes(b"[]")
    cat = {"path": "getStatsList.json", "sha256": hashlib.sha256(b"[]").hexdigest()}
    manifest = {"complete": True, "catalogue": cat, "failures": failures or {},
                "tables": {"T1": {"meta": entries["getMetaInfo.json"],
                                  "header": entries["header.csv"],
                                  "data": entries["data.csv.gz"]}}}  # fmt: skip
    (run / "MANIFEST.json").write_text(json.dumps(manifest))
    obs = d / "parquet" / "observations"
    obs.mkdir(parents=True)
    (obs / "T1.parquet").write_bytes(b"p")
    if stray:
        (obs / "T2.parquet").write_bytes(b"p")
    (d / "parquet" / "tables.parquet").write_bytes(b"p")
    for f in ("LICENSE", "provenance.yaml"):
        (d / f).write_text("x")
    card = card or ("---\nlicense: other\nconfigs:\n" + "".join(p.CARD_CONFIGS)
                    + "---\nraw/2026-01-01/\n")
    (d / "README.md").write_text(card)
    return d


def test_collect_lists_card_parquet_and_raw(tmp_path):
    files = p.collect(_tree(tmp_path))
    assert "parquet/observations/T1.parquet" in files
    assert "raw/2026-01-01/tables/T1/data.csv.gz" in files
    assert files[-1] == "raw/2026-01-01/MANIFEST.json"


def test_a_card_without_licence_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="licence"):
        p.collect(_tree(tmp_path, card="no front matter\nraw/2026-01-01/"))


def test_failures_in_the_manifest_stop_the_push(tmp_path):
    with pytest.raises(SystemExit, match="failed"):
        p.collect(_tree(tmp_path, failures={"T9": "boom"}))


def test_a_changed_raw_file_stops_the_push(tmp_path):
    d = _tree(tmp_path)
    (d / "raw/2026-01-01/tables/T1/data.csv.gz").write_bytes(b"changed")
    with pytest.raises(SystemExit, match="not the file in its manifest"):
        p.collect(d)


def test_a_parquet_file_of_no_table_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="no table"):
        p.collect(_tree(tmp_path, stray=True))
