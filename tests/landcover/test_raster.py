from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box, mapping

from bolivia_fire.config import Paths
from bolivia_fire.landcover.core import FEATURES, GROUPS
from bolivia_fire.landcover.raster import (
    BANDS,
    RasterGrid,
    compare_polygon_pilot,
    normalize_pixels,
    projection_hours,
    raster_year,
    reduction_transforms,
    tile_country_geometry,
)
from bolivia_fire.landcover.usage import UsagePaused
from tests.landcover.test_usage import synthetic_ledger


def synthetic_cells() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "cell_id": ["synthetic-west", "synthetic-east"],
            "west": [0.0, 0.05],
            "south": [0.0, 0.0],
            "east": [0.05, 0.10],
            "north": [0.05, 0.05],
            "national_area_km2": [2.0, 1.0],
        }
    )


def test_synthetic_grid_uses_cell_edges_and_north_to_south_rows() -> None:
    grid = RasterGrid.from_cells(synthetic_cells())
    assert grid.transform == [0.05, 0, 0.0, 0, -0.05, 0.05]
    assert grid.width == 2
    assert grid.height == 1
    assert grid.indices(synthetic_cells()) == ([0, 0], [0, 1])


def test_synthetic_area_means_preserve_national_area_and_exclude_invalid() -> None:
    cells = synthetic_cells()
    grid = RasterGrid.from_cells(cells)
    pixels = np.zeros(
        (1, 2),
        dtype=[(name, "f8") for name in (*GROUPS, "invalid_0", "invalid_27", "invalid_other")],
    )
    pixels["forest"] = [[40, 0]]
    pixels["farming"] = [[55, 0]]
    pixels["invalid_27"] = [[5, 100]]
    frame = normalize_pixels(pixels, cells, grid, 2012)
    assert frame.iloc[0].mapped_area_m2 == pytest.approx(1_900_000)
    assert frame.iloc[0].national_support_area_m2 == 2_000_000
    assert frame.iloc[0].mapbiomas_forest_fraction == pytest.approx(40 / 95)
    assert frame.iloc[0].mapped_area_fraction == pytest.approx(0.95)
    assert frame.iloc[1][list(FEATURES)].isna().all()
    assert not frame.iloc[1].available


def test_synthetic_absent_or_nonfinite_raster_support_stays_missing() -> None:
    cells = synthetic_cells()
    grid = RasterGrid.from_cells(cells)
    pixels = np.zeros(
        (1, 2),
        dtype=[(name, "f8") for name in (*GROUPS, "invalid_0", "invalid_27", "invalid_other")],
    )
    pixels["forest"][0, 1] = np.nan
    frame = normalize_pixels(pixels, cells, grid, 2012)
    assert not frame.available.any()
    assert frame[list(FEATURES)].isna().all().all()
    assert frame.mapped_area_fraction.isna().all()


def test_synthetic_projection_applies_safety_to_all_fourteen_years() -> None:
    assert projection_hours(3600, years=14, safety_factor=1.5) == 21
    with pytest.raises(ValueError):
        projection_hours(float("nan"))


def test_synthetic_grid_rejects_unaligned_edges() -> None:
    cells = synthetic_cells()
    cells.loc[0, "west"] = 0.001
    with pytest.raises(ValueError, match="align"):
        RasterGrid.from_cells(cells)


def test_synthetic_tiles_cover_every_pixel_once_and_bound_native_requests() -> None:
    grid = RasterGrid(-69.65, -9.65, 244, 266)
    coverage = np.zeros((266, 244), dtype=int)
    for row, col, tile in grid.tiles():
        assert tile.width <= 16 and tile.height <= 16
        assert tile.width * tile.height * (0.05 / 0.00026949458523585647) ** 2 < 2**31
        assert tile.west == pytest.approx(grid.west + col * 0.05)
        assert tile.north == pytest.approx(grid.north - row * 0.05)
        coverage[row : row + tile.height, col : col + tile.width] += 1
    np.testing.assert_array_equal(coverage, np.ones_like(coverage))


def test_synthetic_staged_means_preserve_zero_filled_national_area_weights() -> None:
    # Synthetic 4x4 raster: masked national pixels are zero before every mean.
    forest_area = np.array([[4, 0, 0, 0], [0, 0, 0, 0], [0, 0, 4, 0], [0, 0, 0, 0]])
    farming_area = np.ones((4, 4))
    farming_area[0, 0] = farming_area[2, 2] = 0
    farming_area[3, 3] = 0
    forest_mean = forest_area.reshape(2, 2, 2, 2).mean(axis=(1, 3)).mean()
    farming_mean = farming_area.reshape(2, 2, 2, 2).mean(axis=(1, 3)).mean()
    assert forest_mean / (forest_mean + farming_mean) == pytest.approx(8 / 21)
    transforms = reduction_transforms(RasterGrid(-69.65, -9.65, 244, 266))
    assert [transform[0] for transform in transforms] == [0.00078125, 0.003125, 0.0125, 0.05]
    assert all(transform[2] == -69.65 and transform[5] == -9.65 for transform in transforms)


def test_synthetic_empty_tiles_skip_only_regions_without_project_cells() -> None:
    grid = RasterGrid(0, 0.05, 64, 64)
    assert len(grid.tiles()) == 16
    tiles = grid.tiles(cells=synthetic_cells())
    assert len(tiles) == 1
    assert tiles[0][:2] == (0, 0)


def test_synthetic_tile_geometry_keeps_native_pixels_overlapping_tile_edges() -> None:
    tile = RasterGrid(0, 0.05, 1, 1)
    country = box(-1, -1, 1, 1)
    cropped = tile_country_geometry(country, tile, 0.001)
    assert cropped.bounds == pytest.approx((-0.001, -0.001, 0.051, 0.051))
    assert cropped.contains(box(-0.0005, 0.02, 0.0005, 0.03))


def test_synthetic_unknown_request_usage_preserves_failure_and_pauses(tmp_path) -> None:
    paths = Paths(tmp_path)
    paths.manifests.mkdir(parents=True)
    ledger = synthetic_ledger(tmp_path / "usage.json")
    ee = MagicMock()

    @contextmanager
    def synthetic_profile(*, destination):
        destination.write("No usable profile")
        yield

    ee.profilePrinting.side_effect = synthetic_profile
    ee.data.computePixels.side_effect = RuntimeError("projects/synthetic-project unavailable")
    session = SimpleNamespace(
        ee=ee,
        ledger=ledger,
        paths=paths,
        project="synthetic-project",
        transform=[0.000269, 0, 0, 0, -0.000269, 0],
        resources=SimpleNamespace(check=lambda: None),
    )
    with pytest.raises(UsagePaused, match="could not be determined"):
        raster_year(session, synthetic_cells(), box(0, 0, 0.1, 0.05), 2012, 2)
    state = json.loads(ledger.path.read_text("utf-8"))
    assert state["status"] == "paused" and state["workstream_eecu_hours"] is None
    assert state["batches"][0]["status"] == "reserved"
    saved = (paths.manifests / "mapbiomas_raster_requests.jsonl").read_text("utf-8")
    assert "synthetic-project" not in saved
    record = json.loads(saved)
    assert record["status"] == "usage_unknown"
    assert record["profile"] == "No usable profile"
    assert record["upper_eecu_seconds"] is None
    ee.data.computePixels.assert_called_once()


def test_synthetic_polygon_validation_rejects_fraction_and_availability_disagreement() -> None:
    cells = synthetic_cells()
    grid = RasterGrid.from_cells(cells)
    pixels = np.zeros((1, 2), dtype=[(name, "f8") for name in BANDS])
    pixels["forest"] = 100
    raster = normalize_pixels(pixels, cells, grid, 2012)
    polygon = raster.copy(deep=True)
    assert compare_polygon_pilot(raster, polygon)["passed"]
    polygon.loc[0, "mapbiomas_forest_fraction"] = 0.98
    polygon.loc[0, "mapbiomas_farming_fraction"] = 0.02
    report = compare_polygon_pilot(raster, polygon)
    assert not report["passed"]
    assert report["fraction_failure_cells"] == ["synthetic-west"]
    polygon = raster.copy(deep=True)
    polygon.loc[1, "available"] = False
    report = compare_polygon_pilot(raster, polygon)
    assert not report["passed"]
    assert report["availability_disagreement_cells"] == ["synthetic-east"]


def test_synthetic_polygon_validation_rejects_missing_cells() -> None:
    cells = synthetic_cells()
    grid = RasterGrid.from_cells(cells)
    pixels = np.zeros((1, 2), dtype=[(name, "f8") for name in BANDS])
    pixels["forest"] = 100
    raster = normalize_pixels(pixels, cells, grid, 2012)
    with pytest.raises(ValueError, match="omits"):
        compare_polygon_pilot(raster.iloc[:1], raster)


@pytest.mark.parametrize("corrupt", [False, True])
def test_synthetic_resume_reuses_verified_tiles_and_refuses_corruption(tmp_path, corrupt) -> None:
    paths = Paths(tmp_path)
    paths.manifests.mkdir(parents=True)
    cells = synthetic_cells()
    grid = RasterGrid.from_cells(cells)
    country = box(0, 0, 0.1, 0.05)
    pixels = np.zeros((1, 2), dtype=[(name, "f8") for name in BANDS])
    pixels["forest"] = 1
    checkpoint = paths.datasets / "processed/landcover/raster_tiles_2012/cropped/row_0_col_0.npz"
    checkpoint.parent.mkdir(parents=True)
    np.savez_compressed(checkpoint, pixels=pixels)
    native_width = 0.000269
    record = {
        "batch": "raster-national-2012-binary-cropped-v8-tile-0",
        "year": 2012,
        "grid": grid.request_grid(),
        "national_geometry_sha256": hashlib.sha256(
            json.dumps(
                mapping(tile_country_geometry(country, grid, native_width)), sort_keys=True
            ).encode()
        ).hexdigest(),
        "status": "downloaded",
        "profile": "EECU s PeakMem Count Description\n 1.000 1k 1 synthetic\n",
        "reported_eecu_seconds": 1,
        "upper_eecu_seconds": 1.001,
        "runtime_seconds": 1,
        "output": checkpoint.relative_to(paths.root).as_posix(),
        "output_bytes": checkpoint.stat().st_size,
        "output_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
    }
    (paths.manifests / "mapbiomas_raster_requests.jsonl").write_text(
        json.dumps(record) + "\n", encoding="utf-8"
    )
    if corrupt:
        checkpoint.write_bytes(b"synthetic corruption")
    ee = MagicMock()
    ee.data.computePixels.side_effect = AssertionError("Finished tile was re-requested")
    ledger = synthetic_ledger(tmp_path / "usage.json")
    session = SimpleNamespace(
        ee=ee,
        ledger=ledger,
        paths=paths,
        project="synthetic-project",
        transform=[native_width, 0, 0, 0, -native_width, 0],
        resources=SimpleNamespace(check=lambda: None, peak_rss_bytes=1, minimum_free_bytes=10**12),
    )
    if corrupt:
        with pytest.raises(UsagePaused, match="checkpoint"):
            raster_year(session, cells, country, 2012, 2)
    else:
        frame, restored, report = raster_year(session, cells, country, 2012, 2)
        assert frame.mapbiomas_forest_fraction.tolist() == [1, 1]
        np.testing.assert_array_equal(restored, pixels)
        assert report["reused_tiles"] == 1
        assert report["compute_pixels_requests"] == 0
        assert report["upper_eecu_seconds"] == 1.001
    assert json.loads(ledger.path.read_text("utf-8"))["batches"] == []


def test_synthetic_returned_tile_survives_missing_profile_without_further_requests(
    tmp_path,
) -> None:
    paths = Paths(tmp_path)
    paths.manifests.mkdir(parents=True)
    ledger = synthetic_ledger(tmp_path / "usage.json")
    ee = MagicMock()
    pixels = np.zeros((1, 2), dtype=[(name, "f8") for name in BANDS])
    pixels["forest"] = 1
    ee.data.computePixels.return_value = pixels

    @contextmanager
    def synthetic_missing_profile(*, destination):
        destination.write("No usable profile")
        yield

    ee.profilePrinting.side_effect = synthetic_missing_profile
    session = SimpleNamespace(
        ee=ee,
        ledger=ledger,
        paths=paths,
        project="synthetic-project",
        transform=[0.000269, 0, 0, 0, -0.000269, 0],
        resources=SimpleNamespace(check=lambda: None),
    )
    with pytest.raises(UsagePaused, match="could not be determined"):
        raster_year(session, synthetic_cells(), box(0, 0, 0.1, 0.05), 2012, 2)
    saved = json.loads((paths.manifests / "mapbiomas_raster_requests.jsonl").read_text("utf-8"))
    checkpoint = paths.root / saved["output"]
    assert checkpoint.exists()
    with np.load(checkpoint, allow_pickle=False) as archive:
        np.testing.assert_array_equal(archive["pixels"], pixels)
    assert saved["status"] == "usage_unknown"
    assert json.loads(ledger.path.read_text("utf-8"))["status"] == "paused"


def test_synthetic_monitoring_reconciled_tile_reuses_without_profile(tmp_path) -> None:
    from bolivia_fire.landcover.raster import _restore_tile

    tile = RasterGrid(0, 0.05, 2, 1)
    pixels = np.zeros((1, 2), dtype=[(name, "f8") for name in BANDS])
    pixels["forest"] = 1
    checkpoint = tmp_path / "synthetic.npz"
    np.savez_compressed(checkpoint, pixels=pixels)
    record = {
        "batch": "synthetic-reconciled",
        "status": "usage_unknown",
        "year": 2012,
        "grid": tile.request_grid(),
        "national_geometry_sha256": "synthetic-geometry",
        "output_bytes": checkpoint.stat().st_size,
        "output_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        "profile": "",
        "reported_eecu_seconds": None,
        "upper_eecu_seconds": None,
    }
    with pytest.raises(UsagePaused):
        _restore_tile(checkpoint, record, tile, 2012, "synthetic-geometry")
    result = _restore_tile(
        checkpoint,
        record,
        tile,
        2012,
        "synthetic-geometry",
        reconciled_batches={"synthetic-reconciled"},
    )
    np.testing.assert_array_equal(result, pixels)


def test_synthetic_missing_profile_continues_only_after_monitoring_settles(
    tmp_path,
    monkeypatch,
) -> None:
    from bolivia_fire.landcover.monitoring import UsageSnapshot

    paths = Paths(tmp_path)
    paths.manifests.mkdir(parents=True)
    ledger = synthetic_ledger(
        tmp_path / "usage.json",
        accounting_primary="cloud_monitoring",
        project_month_eecu_hours=1,
        workstream_eecu_hours=1,
        monitoring_seed_per_tile_eecu_hours=0.01,
    )
    pixels = np.zeros((1, 2), dtype=[(name, "f8") for name in BANDS])
    pixels["forest"] = 1
    ee = MagicMock()
    ee.data.computePixels.return_value = pixels

    @contextmanager
    def synthetic_missing_profile(*, destination):
        yield
        raise RuntimeError("Profile not found")

    ee.profilePrinting.side_effect = synthetic_missing_profile
    observed = datetime.now(UTC)
    monkeypatch.setattr(
        "bolivia_fire.landcover.raster.wait_for_settlement",
        lambda *args, **kwargs: UsageSnapshot(3602, observed, observed, 1, 1),
    )
    session = SimpleNamespace(
        ee=ee,
        ledger=ledger,
        paths=paths,
        project="synthetic-project",
        monitoring=SimpleNamespace(read=lambda: None),
        transform=[0.000269, 0, 0, 0, -0.000269, 0],
        resources=SimpleNamespace(check=lambda: None, peak_rss_bytes=1, minimum_free_bytes=10**12),
    )
    frame, _, report = raster_year(
        session,
        synthetic_cells(),
        box(0, 0, 0.1, 0.05),
        2012,
        2,
    )
    assert frame.mapbiomas_forest_fraction.tolist() == [1, 1]
    assert report["reported_eecu_seconds"] == pytest.approx(3602)
    assert report["secondary_profile_tiles"] == 0
    assert ledger._load()["batches"][-1]["monitoring_delta_eecu_seconds"] == pytest.approx(2)
    assert ledger._load()["workstream_eecu_hours"] == pytest.approx(3602 / 3600)
    frame, _, report = raster_year(
        session,
        synthetic_cells(),
        box(0, 0, 0.1, 0.05),
        2012,
        2,
    )
    assert report["reused_tiles"] == 1
    ee.data.computePixels.assert_called_once()


def test_synthetic_fully_mapped_support_has_exact_unit_coverage() -> None:
    cells = synthetic_cells()
    grid = RasterGrid.from_cells(cells)
    pixels = np.zeros((1, 2), dtype=[(name, "f8") for name in BANDS])
    weights = [
        0.873553454875946,
        0.0052653043530881405,
        0.8212284445762634,
        0.7970694303512573,
        0.4679349660873413,
    ]
    for name, weight in zip(GROUPS, weights, strict=True):
        pixels[name] = weight / 4
    frame = normalize_pixels(pixels, cells, grid, 2012)
    assert frame.mapped_area_fraction.tolist() == [1.0, 1.0]
    assert frame[list(FEATURES)].ge(0).all().all()
    assert frame[list(FEATURES)].le(1).all().all()


@pytest.mark.parametrize("failed_attempt", [1, 4])
def test_synthetic_early_failure_records_actual_group_counts(tmp_path, monkeypatch, failed_attempt):
    from bolivia_fire.landcover.monitoring import UsageSnapshot

    paths = Paths(tmp_path)
    paths.manifests.mkdir(parents=True)
    seed = synthetic_cells().iloc[0].to_dict()
    cells = pd.DataFrame(
        [
            {
                **seed,
                "cell_id": f"synthetic-{index}",
                "west": index * 0.05,
                "east": (index + 1) * 0.05,
            }
            for index in range(160)
        ]
    )
    ledger = synthetic_ledger(
        tmp_path / "usage.json",
        accounting_primary="cloud_monitoring",
        project_month_eecu_hours=1,
        workstream_eecu_hours=1,
        workstream_safety_cap_eecu_hours=60,
        raster_pilot_authorized=True,
        monitoring_seed_per_tile_eecu_hours=0.01,
    )
    ee = MagicMock()
    attempted = [0]

    def compute(parameters):
        attempted[0] += 1
        if attempted[0] == failed_attempt:
            raise RuntimeError("synthetic computation failed")
        dimensions = parameters["grid"]["dimensions"]
        response = np.zeros(
            (dimensions["height"], dimensions["width"]), dtype=[(name, "f8") for name in BANDS]
        )
        response["forest"] = 1
        return response

    @contextmanager
    def profile(*, destination):
        destination.write("EECU s PeakMem Count Description\n 1.000 1k 1 synthetic\n")
        yield

    ee.profilePrinting.side_effect = profile
    ee.data.computePixels.side_effect = compute
    now = datetime.now(UTC)
    monkeypatch.setattr(
        "bolivia_fire.landcover.raster.wait_for_settlement",
        lambda *args, **kwargs: UsageSnapshot(4320, now, now),
    )
    session = SimpleNamespace(
        ee=ee,
        ledger=ledger,
        paths=paths,
        project="synthetic-project",
        transform=[0.000269, 0, 0, 0, -0.000269, 0],
        monitoring=SimpleNamespace(read=lambda: None),
        resources=SimpleNamespace(check=lambda: None),
    )
    with pytest.raises(RuntimeError, match="synthetic computation failed"):
        raster_year(session, cells, box(0, 0, 8, 0.05), 2012, 2)
    entry = ledger._load()["batches"][-1]
    assert entry["planned_tile_count"] == 10
    assert entry["attempted_tiles"] == entry["tile_count"] == failed_attempt
    assert entry["completed_tiles"] == failed_attempt - 1
    assert entry["rate_sample_complete"] is False
    assert entry["partial_batch_overhead_eecu_seconds"] == pytest.approx(720)
    assert attempted[0] == failed_attempt
