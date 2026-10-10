#!/usr/bin/env python3
"""Push the card, the licence, the provenance, the Parquet and the current run to the Hub.

The source overwrites its tables in place, so the Hub holds one run only: the newest
complete run under data/raw/. Its raw files go up unchanged after their sha256 is checked
against its MANIFEST.json, and the files of older runs are deleted on the Hub in the same
commit (the data follows upstream corrections). The card must declare the Parquet
(observations and tables) and name the run (raw/<YYYY-MM-DD>/).

    uv run python src/publish.py             # dry run: list the files and their total size
    uv run python src/publish.py --push
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
REPO = "yuiseki/estat-jumin-idou"
DELETE_PATTERNS = ["raw/**", "parquet/**"]
CARD_CONFIGS = (
    "- config_name: observations\n  data_files: parquet/observations/*.parquet\n  default: true\n",
    "- config_name: tables\n  data_files: parquet/tables.parquet\n",
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def current_run(data: Path) -> Path:
    runs = [p.parent for p in sorted((data / "raw").glob("*/MANIFEST.json"))
            if json.loads(p.read_text()).get("complete")]  # fmt: skip
    if not runs:
        raise SystemExit("no complete run under data/raw/; run scripts/01_download.py")
    return runs[-1]


def collect(data: Path) -> list[str]:
    """The files to upload, relative to data/; stops on anything the card or a manifest refutes."""
    card = (data / "README.md").read_text(encoding="utf-8")
    if "license: other" not in card:
        raise SystemExit("the card does not declare its licence")
    for c in CARD_CONFIGS:
        if c not in card:
            raise SystemExit(f"the card does not declare {c.splitlines()[0]}")
    d = current_run(data)
    if f"raw/{d.name}/" not in card:
        raise SystemExit(f"the card does not name the current run raw/{d.name}/")
    manifest = json.loads((d / "MANIFEST.json").read_text())
    if manifest["failures"]:
        raise SystemExit(f"the manifest lists {len(manifest['failures'])} failed tables")
    files = ["README.md", "LICENSE", "provenance.yaml", "parquet/tables.parquet"]
    for tid in manifest["tables"]:
        p = data / "parquet" / "observations" / f"{tid}.parquet"
        if not p.exists():
            raise SystemExit(f"no Parquet for table {tid}; run scripts/03_export_parquet.py")
        files.append(p.relative_to(data).as_posix())
    have = {p.name for p in (data / "parquet" / "observations").iterdir()}
    stray = have - {f"{t}.parquet" for t in manifest["tables"]}
    if stray:
        raise SystemExit(f"Parquet files of no table in the manifest: {sorted(stray)[:5]}")
    entries = [manifest["catalogue"]]
    for t in manifest["tables"].values():
        entries += [t["meta"], t["header"], t["data"]]
    for e in entries:
        if sha256(d / e["path"]) != e["sha256"]:
            raise SystemExit(f"raw/{d.name}/{e['path']} is not the file in its manifest")
        files.append(f"raw/{d.name}/{e['path']}")
    files.append(f"raw/{d.name}/MANIFEST.json")
    return files


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()

    files = collect(DATA)
    total = sum((DATA / f).stat().st_size for f in files)
    shown = [f for f in files if "/" not in f or f.endswith(("tables.parquet", "MANIFEST.json"))]
    for f in shown:
        print(f"  {(DATA / f).stat().st_size:>12,}  {f}")
    print(f"  ({len(files) - len(shown)} more files not listed)")
    print(f"{a.repo}\n  {len(files)} files, {total:,} bytes ({total / 1e6:.1f} MB)")
    print(f"  remote files matching {DELETE_PATTERNS} and not uploaded are deleted")
    if not a.push:
        print("\ndry run. pass --push to upload")
        return 0

    from huggingface_hub import HfApi

    api = HfApi()
    api.create_repo(a.repo, repo_type="dataset", exist_ok=True, private=False)
    api.upload_folder(
        folder_path=str(DATA), repo_id=a.repo, repo_type="dataset", allow_patterns=files,
        delete_patterns=DELETE_PATTERNS,
    )  # fmt: skip
    print(f"\npushed to https://huggingface.co/datasets/{a.repo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
