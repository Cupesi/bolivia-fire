from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box

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
