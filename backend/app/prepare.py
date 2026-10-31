from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from time import perf_counter
from typing import Any

import duckdb
import pyarrow.dataset as ds


def file_hash(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def input_hashes(root: Path, files: list[Path]) -> dict[str, str]:
    paths = [root / "configs/protocol.yaml", root / "datasets/processed/cells.parquet", *files]
    return {path.relative_to(root).as_posix(): file_hash(path) for path in paths}


def prepared_dataset(root: Path, files: list[Path]) -> ds.Dataset | None:
    if os.environ.get("BOLIVIA_FIRE_DISABLE_SERVING") == "1":
        return None
    directory = root / "artifacts/serving"
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_bytes())
        if not isinstance(manifest, dict):
            return None
        if manifest.get("version") != 1 or manifest.get("inputs") != input_hashes(root, files):
            return None
        if manifest.get("file") != "series.parquet":
            return None
        path = directory / "series.parquet"
        if file_hash(path) != manifest.get("sha256"):
            return None
        return ds.dataset(str(path), format="parquet")
    except (OSError, ValueError, KeyError, TypeError):
        return None


def prepare(root: Path) -> dict[str, Any]:
    root = root.resolve()
    files = sorted((root / "datasets/processed/panel").glob("year=*/part.parquet"))
    if not files:
        raise ValueError("Prepared panel data is required")
    if shutil.disk_usage(root).free < 60 * 1024**3:
        raise ValueError("Preparation requires 60 GiB free to preserve the 55 GiB reserve")
    tick = perf_counter()
    signature = input_hashes(root, files)
    directory = root / "artifacts/serving"
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="prepare-", dir=directory) as temporary:
        pending = Path(temporary) / "series.parquet"
        with duckdb.connect(
            config={
                "memory_limit": "512MB",
                "threads": "2",
                "temp_directory": temporary,
                "max_temp_directory_size": "4GB",
                "preserve_insertion_order": "false",
            }
        ) as connection:
            rows = connection.execute(
                """
                COPY (SELECT cell_id, issue_date, y_primary, n_primary
                      FROM read_parquet(?) ORDER BY cell_id, issue_date)
                TO ? (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 8192)
            """,
                [[str(path) for path in files], str(pending)],
            ).fetchone()[0]
        if input_hashes(root, files) != signature:
            raise RuntimeError(
                "Panel inputs changed during preparation; retry after the build finishes"
            )
        manifest = {
            "version": 1,
            "file": "series.parquet",
            "sha256": file_hash(pending),
            "inputs": signature,
            "rows": rows,
            "bytes": pending.stat().st_size,
        }
        pending_manifest = Path(temporary) / "manifest.json"
        pending_manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        os.replace(pending, directory / "series.parquet")
        os.replace(pending_manifest, directory / "manifest.json")
    return {"rows": rows, "bytes": manifest["bytes"], "elapsed_seconds": perf_counter() - tick}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(os.environ.get("BOLIVIA_FIRE_DATA_ROOT", Path(__file__).resolve().parents[2])),
    )
    arguments = parser.parse_args()
    print(json.dumps(prepare(arguments.root), allow_nan=False))


if __name__ == "__main__":
    main()
