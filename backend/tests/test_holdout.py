from __future__ import annotations

import csv
import io
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backend.tests.test_api import client_for


@pytest.fixture
def synthetic_geographic_predictions(synthetic_predictions: Path) -> Path:
    path = synthetic_predictions / "artifacts/predictions/strict/test.parquet"
    temporal = pq.ParquetFile(path).read()
    geographic = pa.table(
        {
            "cell_id": ["r1342c2247", "r1341c2247"],
            "issue_date": [datetime(2024, 9, 2)] * 2,
            "model": ["strict_geo/lightgbm"] * 2,
            "p": [0.65, 0.99],
            "y": [0, 1],
        }
    )
    pq.write_table(pa.concat_tables([temporal, geographic]), path)
    return synthetic_predictions


def test_temporal_layer_uses_geographic_model_only_for_holdout_cells(
    synthetic_geographic_predictions: Path,
) -> None:
    with client_for(synthetic_geographic_predictions) as client:
        layer = client.get(
            "/api/v1/layer?issue_date=2024-09-02&kind=estimate&model=strict/lightgbm"
        ).json()
        assert layer["cell_index"] == [0, 1, 2]
        assert layer["value"] == [0.8, 0.1, 0.65]
        assert layer["value_model"] == ["strict/lightgbm", "strict/lightgbm", "strict_geo/lightgbm"]
        assert layer["geographic_model"] == "strict_geo/lightgbm"
        series = client.get("/api/v1/cells/r1342c2247/series?model=strict/lightgbm").json()
        assert series["model"] == "strict_geo/lightgbm"
        assert series["p"] == [0.65, None, None]
        assert series["y"] == [0, None, 0]
        response = client.get(
            "/api/v1/export?issue_date=2024-09-02&kind=estimate&model=strict/lightgbm"
        )
        rows = list(
            csv.DictReader(
                io.StringIO(
                    "\n".join(
                        line for line in response.text.splitlines() if not line.startswith("#")
                    )
                )
            )
        )
        assert rows[2]["model"] == "strict_geo/lightgbm"
        assert rows[2]["value"] == "0.65"


def test_holdout_without_geographic_week_remains_null(synthetic_predictions: Path) -> None:
    with client_for(synthetic_predictions) as client:
        layer = client.get(
            "/api/v1/layer?issue_date=2024-09-02&kind=estimate&model=strict/lightgbm"
        ).json()
        assert layer["cell_index"] == [0, 1, 2]
        assert layer["value"] == [0.8, 0.1, None]
        assert layer["value_model"] == ["strict/lightgbm", "strict/lightgbm", None]
        assert layer["geographic_model"] is None
        series = client.get("/api/v1/cells/r1342c2247/series?model=strict/lightgbm").json()
        assert series["model"] is None
        assert series["p"] == [None, None, None]


def test_geographic_selection_keeps_its_own_population(
    synthetic_geographic_predictions: Path,
) -> None:
    with client_for(synthetic_geographic_predictions) as client:
        layer = client.get(
            "/api/v1/layer?issue_date=2024-09-02&kind=estimate&model=strict_geo/lightgbm"
        ).json()
        assert layer["cell_index"] == [0, 2]
        assert layer["value"] == [0.99, 0.65]
        assert layer["value_model"] == ["strict_geo/lightgbm"] * 2


def test_geographic_week_is_visible_without_temporal_predictions(
    synthetic_geographic_predictions: Path,
) -> None:
    path = synthetic_geographic_predictions / "artifacts/predictions/strict/test.parquet"
    table = pq.ParquetFile(path).read()
    pq.write_table(
        table.filter(
            pa.array(
                [
                    row["model"].startswith("strict_geo/") or row["issue_date"].day == 9
                    for row in table.to_pylist()
                ]
            )
        ),
        path,
    )
    with client_for(synthetic_geographic_predictions) as client:
        response = client.get(
            "/api/v1/layer?issue_date=2024-09-02&kind=estimate&model=strict/lightgbm"
        )
        assert response.status_code == 200
        assert response.json()["cell_index"] == [2]
        assert response.json()["value"] == [0.65]
