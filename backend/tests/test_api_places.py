from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest
import yaml
from fastapi.testclient import TestClient


@pytest.mark.parametrize("openings", [[], [{"protocol_sha256": "outdated"}]])
def test_synthetic_unopened_evaluation_keeps_grid_available(
    synthetic_root: Path, openings: list[dict]
) -> None:
    from backend.app.main import create_app

    directory = synthetic_root / "artifacts/evaluation"
    directory.mkdir(parents=True)
    (directory / "final_test_log.json").write_text(json.dumps(openings), encoding="utf-8")
    with TestClient(create_app(synthetic_root)) as client:
        assert client.get("/api/v1/grid").status_code == 200
        meta = client.get("/api/v1/meta").json()
        assert "primary_opening_utc" not in meta


def test_synthetic_grid_places_and_administrative_boundaries(synthetic_root: Path) -> None:
    from backend.app.main import create_app

    cells = pd.read_parquet(synthetic_root / "datasets/processed/cells.parquet")
    places = pd.DataFrame(
        {
            "cell_id": cells.cell_id.iloc[::-1].tolist(),
            "department": ["Beni", "Santa Cruz", "Santa Cruz"],
            "province": ["B", "A", "A"],
            "province_national_share": [1.0] * 3,
            "municipality": ["Concepción", "Other", "San José de Chiquitos"],
            "municipality_national_share": [1.0, 0.6, 0.7],
            "nearest_place_name": ["Concepción", "Other", "San José de Chiquitos"],
            "nearest_place_geonames_id": [3, 2, 1],
            "nearest_place_feature_code": ["PPL"] * 3,
            "nearest_place_population": [30, 20, 10],
            "nearest_place_distance_km": [3.0, 2.0, 1.0],
            "populated_places": [
                [],
                [],
                [
                    {
                        "name": "San José de Chiquitos",
                        "geonames_id": 1,
                        "feature_code": "PPL",
                        "population": 10,
                    }
                ],
            ],
        }
    )
    places.to_parquet(synthetic_root / "datasets/processed/cell_places.parquet", index=False)
    sources_path = synthetic_root / "datasets/sources.yaml"
    sources = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
    for level in (2, 3):
        path = synthetic_root / f"datasets/raw/boundaries/synthetic_ADM{level}.geojson"
        data = json.loads(
            (synthetic_root / "datasets/raw/boundaries/synthetic_ADM1.geojson").read_text(
                encoding="utf-8"
            )
        )
        data["features"][0]["properties"]["shapeName"] = "Concepción"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        sources["boundaries"][f"ADM{level}"] = {
            "path": path.relative_to(synthetic_root).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    sources_path.write_text(yaml.safe_dump(sources), encoding="utf-8")
    with TestClient(create_app(synthetic_root)) as client:
        grid = client.get("/api/v1/grid").json()
        assert grid["nearest_place_geonames_id"] == [1, 2, 3]
        assert grid["municipality"][0] == "San José de Chiquitos"
        assert grid["populated_places"][0][0]["name"] == "San José de Chiquitos"
        for level in (2, 3):
            response = client.get(f"/api/v1/boundaries?level={level}")
            assert response.status_code == 200
            assert response.json()["features"][0]["properties"]["shapeName"] == "Concepción"
            assert response.json()["simplification"]["preserve_topology"] is True
