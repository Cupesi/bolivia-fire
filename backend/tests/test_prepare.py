from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from test_api import client_for


def test_prepared_series_preserves_nulls_and_rejects_stale_inputs(synthetic_root: Path) -> None:
    from backend.app.prepare import prepare

    result = prepare(synthetic_root)
    assert result["rows"] == 9
    with client_for(synthetic_root) as client:
        series = client.get("/api/v1/cells/r1341c2247/series").json()
        assert series["y"] == [1, None, 0]
        assert series["n_primary"] == [3, 2, 0]
        assert client.app.state.store.series_dataset is not None
    source = synthetic_root / "datasets/processed/panel/year=2024/part.parquet"
    rows = pq.ParquetFile(source).read().to_pylist()
    rows[0]["y_primary"] = 0
    pq.write_table(pa.Table.from_pylist(rows), source)
    with client_for(synthetic_root) as client:
        assert client.get("/api/v1/cells/r1341c2247/series").json()["y"] == [0, None, 0]
        assert client.app.state.store.series_dataset is None


def test_corrupted_prepared_file_falls_back_to_source(synthetic_root: Path) -> None:
    from backend.app.prepare import prepare

    prepare(synthetic_root)
    manifest_path = synthetic_root / "artifacts/serving/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    parquet = synthetic_root / "artifacts/serving" / manifest["file"]
    parquet.write_bytes(b"synthetic corrupt cache")
    with client_for(synthetic_root) as client:
        assert client.get("/api/v1/cells/r1341c2247/series").json()["y"] == [1, None, 0]


def test_environment_root_is_used(synthetic_root: Path) -> None:
    from fastapi.testclient import TestClient

    from backend.app.main import create_app

    with TestClient(create_app()) as client:
        assert client.get("/api/v1/meta").json()["issue_date_range"] == {
            "start": "2024-09-02",
            "end": "2024-09-16",
        }


@pytest.mark.parametrize("content", ["[]", "null", "invalid synthetic json"])
def test_malformed_serving_manifest_falls_back(synthetic_root: Path, content: str) -> None:
    from backend.app.prepare import prepare

    prepare(synthetic_root)
    (synthetic_root / "artifacts/serving/manifest.json").write_text(content)
    with client_for(synthetic_root) as client:
        assert client.get("/api/v1/cells/r1341c2247/series").json()["y"] == [1, None, 0]
