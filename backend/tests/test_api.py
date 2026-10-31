from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def client_for(root: Path) -> TestClient:
    from backend.app.main import create_app

    return TestClient(create_app(root), raise_server_exceptions=False)


def test_observed_nulls_grid_indices_and_week_completeness(synthetic_root: Path) -> None:
    with client_for(synthetic_root) as client:
        assert client.get("/api/v1/health").json()["data_ready"] == {
            "cells": True,
            "panel": True,
            "predictions": False,
            "evaluation": False,
        }
        grid = client.get("/api/v1/grid")
        assert grid.json()["cell_id"] == ["r1341c2247", "r1341c2248", "r1342c2247"]
        assert grid.json()["row_range"] == [1341, 1342]
        assert (
            client.get("/api/v1/grid", headers={"If-None-Match": grid.headers["etag"]}).status_code
            == 304
        )
        layer = client.get("/api/v1/layer?issue_date=2024-09-09&kind=observed").json()
        assert layer["cell_index"] == [0, 1, 2]
        assert layer["value"] == [None, None, None]
        assert layer["n_primary"] == [2, 0, 0]
        weeks = client.get("/api/v1/weeks?offset=1&limit=1").json()
        assert weeks["total"] == 3
        assert weeks["weeks"] == [
            {
                "issue_date": "2024-09-09",
                "label_complete_share": 0.0,
                "target_complete": False,
                "outage": True,
                "target_low_coverage": True,
            }
        ]


def test_short_outage_and_low_coverage_do_not_make_targets_incomplete(
    synthetic_root: Path,
) -> None:
    with client_for(synthetic_root) as client:
        weeks = client.get("/api/v1/weeks").json()["weeks"]
        assert weeks[0] == {
            "issue_date": "2024-09-02",
            "label_complete_share": 1.0,
            "target_complete": True,
            "outage": True,
            "target_low_coverage": False,
        }
        assert weeks[2] == {
            "issue_date": "2024-09-16",
            "label_complete_share": 1.0,
            "target_complete": True,
            "outage": False,
            "target_low_coverage": True,
        }


@pytest.mark.parametrize(
    "url",
    [
        "/api/v1/layer?issue_date=2024-09-02T00:00:00&kind=observed",
        "/api/v1/layer?issue_date=2024-02-30&kind=observed",
        "/api/v1/layer?issue_date=2024-08-26&kind=observed",
        "/api/v1/layer?issue_date=2024-09-03&kind=observed",
        "/api/v1/layer?issue_date=2024-09-02&kind=fire",
        "/api/v1/layer?issue_date=2024-09-02&kind=observed&variant=../../secret",
        "/api/v1/layer?issue_date=2024-09-02&kind=observed&unknown=x",
        "/api/v1/layer?issue_date=2024-09-02&issue_date=2024-09-09&kind=observed",
        "/api/v1/cells/invalid/series",
        "/api/v1/cells/r1341c2247/series?start=2024-09-16&end=2024-09-02",
        "/api/v1/weeks?limit=701",
        "/api/v1/weeks?offset=-1",
        "/api/v1/boundaries?level=2",
        "/api/v1/export?issue_date=2024-09-02&kind=observed&format=json",
        "/api/v1/export?issue_date=2024-09-02&kind=observed&start=2013-01-07",
        "/api/v1/metrics?variant=../../configs&split=test",
    ],
)
def test_rejects_malformed_or_unbounded_requests(synthetic_root: Path, url: str) -> None:
    with client_for(synthetic_root) as client:
        response = client.get(url)
        assert response.status_code == 422
        assert str(synthetic_root) not in response.text
        assert "Traceback" not in response.text


def test_missing_predictions_and_unknown_cells(synthetic_root: Path) -> None:
    with client_for(synthetic_root) as client:
        response = client.get("/api/v1/layer?issue_date=2024-09-02&kind=estimate")
        assert response.status_code == 404
        assert "prediction" in response.json()["error"]["message"].lower()
        assert client.get("/api/v1/cells/r9999c9999/series").status_code == 404
        series = client.get("/api/v1/cells/r1341c2247/series").json()
        assert series["issue_date"] == ["2024-09-02", "2024-09-09", "2024-09-16"]
        assert series["y"] == [1, None, 0]
        assert series["p"] == [None, None, None]


def test_predictions_join_by_cell_and_date(synthetic_predictions: Path) -> None:
    with client_for(synthetic_predictions) as client:
        layer = client.get(
            "/api/v1/layer?issue_date=2024-09-02&kind=estimate&model=strict/lightgbm"
        ).json()
        assert layer["cell_index"] == [0, 1]
        assert layer["value"] == [0.8, 0.1]
        series = client.get("/api/v1/cells/r1341c2247/series?model=strict/lightgbm").json()
        assert series["p"] == [0.8, 0.4, None]
        assert series["y"] == [1, None, 0]
        assert (
            client.get(
                "/api/v1/layer?issue_date=2024-09-16&kind=estimate&model=strict/lightgbm"
            ).status_code
            == 404
        )


def test_export_is_one_week_with_provenance_and_nulls(synthetic_root: Path) -> None:
    with client_for(synthetic_root) as client:
        response = client.get("/api/v1/export?issue_date=2024-09-09&kind=observed&format=csv")
        assert response.status_code == 200
        assert "protocol_sha256=" in response.text
        rows = list(
            csv.DictReader(
                io.StringIO(
                    "\n".join(
                        line for line in response.text.splitlines() if not line.startswith("#")
                    )
                )
            )
        )
        assert len(rows) == 3
        assert {row["issue_date"] for row in rows} == {"2024-09-09"}
        assert [row["value"] for row in rows] == ["", "", ""]
        assert [row["n_primary"] for row in rows] == ["2", "0", "0"]


def test_meta_boundaries_and_metrics_gate(synthetic_root: Path) -> None:
    evaluation = synthetic_root / "artifacts/evaluation"
    evaluation.mkdir(parents=True)
    (evaluation / "test_strict.json").write_text(json.dumps({"score": 0.5}))
    (evaluation / "val_select_strict.json").write_text(json.dumps({"score": 0.4}))
    with client_for(synthetic_root) as client:
        meta = client.get("/api/v1/meta").json()
        assert meta["protocol"]["version"] == "1.1"
        assert len(meta["protocol"]["sha256"]) == 64
        assert meta["issue_date_range"] == {"start": "2024-09-02", "end": "2024-09-16"}
        assert meta["evaluation_splits"] == {"strict": ["val_select"]}
        assert client.get("/api/v1/metrics?variant=strict&split=test").status_code == 404
        assert client.get("/api/v1/metrics?variant=strict&split=val_select").json() == {
            "score": 0.4
        }
        boundaries = client.get("/api/v1/boundaries?level=1")
        assert boundaries.json()["features"][0]["properties"]["shapeName"] == "Santa Cruz"
        assert (
            client.get(
                "/api/v1/boundaries?level=1", headers={"If-None-Match": boundaries.headers["etag"]}
            ).status_code
            == 304
        )
    (evaluation / "final_test_log.json").write_text(
        json.dumps(
            [
                {
                    "timestamp_utc": "2026-10-31T00:00:00+00:00",
                    "protocol_sha256": meta["protocol"]["sha256"],
                    "config": {"variant": "strict"},
                }
            ]
        )
    )
    with client_for(synthetic_root) as client:
        assert client.get("/api/v1/metrics?variant=strict&split=test").json() == {"score": 0.5}


def test_same_origin_default_and_explicit_cors(
    synthetic_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with client_for(synthetic_root) as client:
        response = client.get("/api/v1/health", headers={"Origin": "https://elsewhere.example"})
        assert "access-control-allow-origin" not in response.headers
    monkeypatch.setenv("BOLIVIA_FIRE_CORS_ORIGINS", "https://maps.example")
    with client_for(synthetic_root) as client:
        assert (
            client.get("/api/v1/health", headers={"Origin": "https://maps.example"}).headers[
                "access-control-allow-origin"
            ]
            == "https://maps.example"
        )
        assert (
            "access-control-allow-origin"
            not in client.get(
                "/api/v1/health", headers={"Origin": "https://elsewhere.example"}
            ).headers
        )
        assert (
            client.options(
                "/api/v1/layer",
                headers={"Origin": "https://maps.example", "Access-Control-Request-Method": "POST"},
            ).status_code
            == 400
        )


def test_spa_fallback_cannot_expose_files(synthetic_root: Path) -> None:
    dist = synthetic_root / "frontend/dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html>synthetic app</html>")
    (synthetic_root / "private.txt").write_text("synthetic private marker")
    with client_for(synthetic_root) as client:
        assert "synthetic app" in client.get("/map/cell").text
        assert client.get("/api/v1/unknown").status_code == 404
        for url in [
            "/%2e%2e/private.txt",
            "/..%5cprivate.txt",
            "/%2e%2e%2fprivate.txt",
            "/%252e%252e/private.txt",
            "/C:%5cprivate.txt",
            "/missing.js",
        ]:
            response = client.get(url)
            assert response.status_code == 404
            assert "synthetic private marker" not in response.text


def test_unexpected_errors_are_sanitized(
    synthetic_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with client_for(synthetic_root) as client:

        def broken(*args: object, **kwargs: object) -> object:
            raise RuntimeError(f"private data at {synthetic_root}/private.txt")

        monkeypatch.setattr(client.app.state.store, "layer", broken)
        response = client.get("/api/v1/layer?issue_date=2024-09-02&kind=observed")
        assert response.status_code == 500
        assert response.json() == {
            "error": {"code": "internal_error", "message": "Internal server error"}
        }
        assert "private" not in response.text


@pytest.mark.local_data
def test_real_local_artifacts() -> None:
    root = Path(__file__).resolve().parents[2]
    if not (root / "datasets/processed/cells.parquet").exists() or not list(
        (root / "datasets/processed/panel").glob("year=*/part.parquet")
    ):
        pytest.skip("Prepared local artifacts are absent")
    with client_for(root) as client:
        assert client.get("/api/v1/health").json()["data_ready"]["panel"] is True
        grid = client.get("/api/v1/grid").json()
        week = client.get("/api/v1/weeks?limit=1").json()["weeks"][0]["issue_date"]
        layer = client.get(f"/api/v1/layer?issue_date={week}&kind=observed")
        assert layer.status_code == 200
        assert len(layer.json()["cell_index"]) <= len(grid["cell_id"])
        series = client.get(f"/api/v1/cells/{grid['cell_id'][0]}/series")
        assert series.status_code == 200
        assert len(series.json()["issue_date"]) <= 700


@pytest.mark.local_data
@pytest.mark.parametrize(
    ("issue_date", "complete", "low_coverage"),
    [
        ("2022-08-01", False, True),
        ("2025-12-29", True, False),
    ],
)
def test_real_outage_and_complete_week(
    issue_date: str, complete: bool, low_coverage: bool
) -> None:
    root = Path(__file__).resolve().parents[2]
    if not (root / f"datasets/processed/panel/year={issue_date[:4]}/part.parquet").exists():
        pytest.skip("Prepared local artifacts are absent")
    with client_for(root) as client:
        response = client.get(f"/api/v1/weeks?start={issue_date}&end={issue_date}")
        assert response.status_code == 200
        assert response.json()["weeks"] == [
            {
                "issue_date": issue_date,
                "label_complete_share": float(complete),
                "target_complete": complete,
                "outage": True,
                "target_low_coverage": low_coverage,
            }
        ]
        layer = client.get(f"/api/v1/layer?issue_date={issue_date}&kind=observed").json()
        assert len(layer["value"]) == 37_146
        if complete:
            assert set(layer["value"]) == {0, 1}
        else:
            assert layer["value"] == [None] * 37_146
