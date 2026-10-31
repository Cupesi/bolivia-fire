from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def test_synthetic_static_refresh_preserves_forecasts_and_has_full_provenance(tmp_path) -> None:
    import hashlib
    import json

    import yaml

    from bolivia_fire.config import Paths
    from bolivia_fire.live.static import refresh_static

    root, out = tmp_path / "source", tmp_path / "live"
    for folder in ("configs", "datasets/processed", "artifacts/evaluation"):
        (root / folder).mkdir(parents=True)
    for folder in ("static", "model", "forecasts"):
        (out / folder).mkdir(parents=True)
    protocol = {"protocol_version": "synthetic", "grid": {}}
    (root / "configs/protocol.yaml").write_text(yaml.safe_dump(protocol), encoding="utf-8")
    (root / "configs/experiments.yaml").write_text("synthetic: true\n", encoding="utf-8")
    (root / "datasets/sources.yaml").write_text("boundaries: {}\n", encoding="utf-8")
    cells = pd.DataFrame({"cell_id": ["a"], "department": ["Synthetic"]})
    cells.to_parquet(root / "datasets/processed/cells.parquet")
    places_frame = pd.DataFrame(
        {
            "cell_id": ["a"],
            "department": ["Synthetic"],
            "province": ["Province"],
            "province_national_share": [1.0],
            "municipality": ["Concepción"],
            "municipality_national_share": [0.9],
            "nearest_place_name": ["Concepción"],
            "nearest_place_geonames_id": [1],
            "nearest_place_feature_code": ["PPL"],
            "nearest_place_population": [100],
            "nearest_place_distance_km": [1.5],
            "populated_places": [[]],
        }
    )
    places_frame.to_parquet(root / "datasets/processed/cell_places.parquet", index=False)
    primary = {
        "protocol_sha256": hashlib.sha256(
            (root / "configs/protocol.yaml").read_bytes()
        ).hexdigest(),
        "timestamp_utc": "2026-01-01T00:00:00Z",
        "dataset_snapshot_hashes": {
            "datasets/processed/panel/year=2024/part.parquet": "a" * 64,
            "datasets/sources.yaml": "b" * 64,
        },
        "model_ids": {"strict/synthetic": "c" * 64},
        "calibrator_ids": {"strict/synthetic": "d" * 64},
    }
    (root / "artifacts/evaluation/final_test_log.json").write_text(
        json.dumps([primary]), encoding="utf-8"
    )
    card = {
        "model_name": "synthetic",
        "variant": "strict",
        "model_id": "c" * 64,
        "source_model_sha256": "c" * 64,
        "source_calibrator_sha256": "d" * 64,
        "primary_opening_utc": primary["timestamp_utc"],
    }
    (out / "model/model_card.json").write_text(json.dumps(card), encoding="utf-8")
    (out / "static/grid.json").write_text('{"cell_id":["a"]}', encoding="utf-8")
    (out / "static/meta.json").write_text('{"generated_utc":"synthetic"}', encoding="utf-8")
    forecast = out / "forecasts/2026-01-05.json"
    forecast.write_bytes(b'{ "synthetic": "immutable" }\n')
    before = forecast.read_bytes()
    refresh_static(Paths(root), out)
    assert forecast.read_bytes() == before
    places = json.loads((out / "static/cell_places.json").read_text(encoding="utf-8"))
    assert places["municipality"] == ["Concepción"]
    meta = json.loads((out / "static/meta.json").read_text(encoding="utf-8"))
    assert meta["protocol"]["version"] == "synthetic"
    assert meta["source_model_sha256"] == "c" * 64
    assert meta["source_calibrator_sha256"] == "d" * 64
    assert meta["dataset_snapshot_ids"]["panel"] == {"2024": "a" * 64}
    assert meta["dataset_snapshot_ids"]["evaluation_sources_sha256"] == "b" * 64
    assert len(meta["experiments_sha256"]) == 64
    assert len(meta["cells_sha256"]) == 64
    assert meta["generated_utc"] == "synthetic"
    assert "CHIRPS" not in [entry["name"] for entry in meta["source_attributions"]]
    card["source_model_sha256"] = "e" * 64
    (out / "model/model_card.json").write_text(json.dumps(card), encoding="utf-8")
    with pytest.raises(ValueError, match="primary opening"):
        refresh_static(Paths(root), out)


def test_synthetic_climatology_uses_same_week_and_department_for_unseen_cells() -> None:
    from bolivia_fire.live.static import climatology_for_issue

    climate = pd.DataFrame(
        {
            "week_index": [0, 0, 1, 1],
            "cell_id": ["a", "b", "a", "b"],
            "train_climatology_cell": [0.1, np.nan, 0.9, np.nan],
            "train_climatology_dept": [0.2, 0.2, 0.8, 0.8],
        }
    )
    cells = pd.DataFrame({"cell_id": ["b", "a"]})
    features, baseline = climatology_for_issue(cells, climate, pd.Timestamp("2026-01-05"))
    assert np.isnan(features.train_climatology_cell.iloc[0])
    assert features.train_climatology_cell.iloc[1] == 0.1
    assert baseline.tolist() == [0.2, 0.1]
