from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "local_data: requires prepared local artifacts")


@pytest.fixture
def synthetic_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "synthetic"
    (root / "configs").mkdir(parents=True)
    shutil.copyfile(
        Path(__file__).resolve().parents[2] / "configs/protocol.yaml",
        root / "configs/protocol.yaml",
    )
    processed = root / "datasets/processed"
    processed.mkdir(parents=True)
    cells = pa.table(
        {
            "cell_id": ["r1341c2247", "r1341c2248", "r1342c2247"],
            "row": [1341, 1341, 1342],
            "col": [2247, 2248, 2247],
            "department": ["Santa Cruz", "Santa Cruz", "Beni"],
            "national_area_km2": [20.0, 25.0, 24.0],
            "santa_cruz": [True, True, False],
            "geo_holdout": [False, False, True],
        }
    )
    metadata = {
        b"protocol_sha256": hashlib.sha256((root / "configs/protocol.yaml").read_bytes())
        .hexdigest()
        .encode(),
        b"firms_manifest_sha256": b"synthetic-manifest",
    }
    pq.write_table(cells.replace_schema_metadata(metadata), processed / "cells.parquet")
    panel_dir = processed / "panel/year=2024"
    panel_dir.mkdir(parents=True)
    records = []
    for day, labels, counts, complete in [
        (2, [1, 0, 0], [3, 0, 0], True),
        (9, [None, None, None], [2, 0, 0], False),
        (16, [0, 1, 0], [0, 1, 0], True),
    ]:
        for cell, label, count in zip(cells["cell_id"].to_pylist(), labels, counts, strict=True):
            records.append(
                {
                    "cell_id": cell,
                    "issue_date": datetime(2024, 9, day),
                    "y_primary": label,
                    "n_primary": count,
                    "target_complete": complete,
                    "target_low_coverage": day == 9,
                }
            )
    pq.write_table(
        pa.Table.from_pylist(records).replace_schema_metadata(metadata), panel_dir / "part.parquet"
    )
    boundary_dir = root / "datasets/raw/boundaries"
    boundary_dir.mkdir(parents=True)
    pins = {}
    for level in (0, 1):
        path = boundary_dir / f"synthetic_ADM{level}.geojson"
        content = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"shapeName": "Santa Cruz"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[-68, -23], [-67, -23], [-67, -22], [-68, -23]]],
                    },
                }
            ],
        }
        path.write_text(json.dumps(content), encoding="utf-8")
        pins[f"ADM{level}"] = {
            "path": path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    (root / "datasets/sources.yaml").write_text(yaml.safe_dump({"boundaries": pins}))
    outages = root / "datasets/manifests/snpp_outages.csv"
    outages.parent.mkdir(parents=True)
    outages.write_text(
        "start_utc,end_utc,comment\n2024-09-09 00:00:00+00:00,2024-09-10 00:00:00+00:00,synthetic\n"
    )
    monkeypatch.setenv("BOLIVIA_FIRE_DATA_ROOT", str(root))
    monkeypatch.delenv("BOLIVIA_FIRE_CORS_ORIGINS", raising=False)
    return root


@pytest.fixture
def synthetic_predictions(synthetic_root: Path) -> Path:
    folder = synthetic_root / "artifacts/predictions/strict"
    folder.mkdir(parents=True)
    pq.write_table(
        pa.table(
            {
                "cell_id": ["r1341c2247", "r1341c2248", "r1341c2247"],
                "issue_date": [datetime(2024, 9, 2), datetime(2024, 9, 2), datetime(2024, 9, 9)],
                "model": ["strict/lightgbm"] * 3,
                "p": [0.8, 0.1, 0.4],
                "y": [1, 0, None],
            }
        ),
        folder / "test.parquet",
    )
    return synthetic_root
