from __future__ import annotations

import argparse
import hashlib
import json
from contextlib import nullcontext
from types import SimpleNamespace

import numpy as np
import pytest
from shapely.geometry import box, mapping

from bolivia_fire.config import Paths
from bolivia_fire.landcover import acquire
from bolivia_fire.landcover.raster import BANDS, RasterGrid, normalize_pixels
from tests.landcover.test_raster import synthetic_cells
from tests.landcover.test_usage import synthetic_ledger


@pytest.mark.parametrize(
    ("cost_seconds", "agreement", "expected_years", "expected_status", "cap"),
    [
        (7200, True, [], "stopped", 30),
        (1, False, [], "stopped", 30),
        (1, True, list(range(2012, 2026)), "completed", 30),
        (7200, True, list(range(2012, 2026)), "completed", 60),
        (7200, True, list(range(2012, 2026)), "completed", 100),
    ],
)
def test_synthetic_acquisition_requires_both_cost_and_agreement_gates(
    tmp_path, monkeypatch, cost_seconds, agreement, expected_years, expected_status, cap
) -> None:
    paths = Paths(tmp_path)
    paths.manifests.mkdir(parents=True)
    directory = paths.datasets / "processed/landcover"
    directory.mkdir(parents=True)
    cells = synthetic_cells()
    pixels = np.zeros((1, 2), dtype=[(name, "f8") for name in BANDS])
    pixels["forest"] = 100
    normalize_pixels(pixels, cells, RasterGrid.from_cells(cells), 2012).to_parquet(
        directory / "pilot_1000_2012.parquet", index=False
    )
    ledger = synthetic_ledger(tmp_path / "usage.json", workstream_safety_cap_eecu_hours=cap)
    monkeypatch.setattr(acquire, "load_project", lambda root: "synthetic-project")
    monkeypatch.setattr(acquire, "load_cells", lambda paths: (cells, None))
    monkeypatch.setattr(
        acquire, "ResourceMonitor", lambda root: nullcontext(SimpleNamespace(check=lambda: None))
    )
    monkeypatch.setattr(
        acquire,
        "EarthEngineSession",
        lambda paths, ledger, resources: SimpleNamespace(
            project="synthetic-project", ledger=ledger
        ),
    )
    monkeypatch.setattr(acquire, "compare_polygon_pilot", lambda *args: {"passed": agreement})
    submitted = []

    def synthetic_year(session, cells, country, year, reservation):
        submitted.append(year)
        batch = f"synthetic-{year}"
        session.ledger.reserve(batch, reservation)
        session.ledger.reconcile(
            batch, reported_eecu_seconds=cost_seconds, upper_eecu_seconds=cost_seconds
        )
        return (
            normalize_pixels(pixels, cells, RasterGrid.from_cells(cells), year),
            pixels,
            {
                "year": year,
                "reported_eecu_seconds": cost_seconds,
                "upper_eecu_seconds": cost_seconds,
            },
        )

    monkeypatch.setattr(acquire, "raster_year", synthetic_year)
    report = tmp_path / "report.json"
    result = acquire.run_raster(argparse.Namespace(ledger=ledger.path, report=report), paths)
    saved = json.loads(report.read_text("utf-8"))
    assert submitted == ([2012] if expected_status == "stopped" else expected_years)
    assert saved["years_completed"] == expected_years
    assert saved["status"] == expected_status
    assert result == (0 if expected_status == "completed" else 2)
    state = json.loads(ledger.path.read_text("utf-8"))
    assert state["status"] == "paused"
    assert state["submissions_authorized"] is False
    if expected_status == "stopped":
        assert not (directory / "mapbiomas_2012.parquet").exists()
        assert not (directory / "raster_2012.npz").exists()
        assert saved["pilot_report"]["upper_eecu_seconds"] == cost_seconds


@pytest.mark.parametrize("publication_crash", [False, True])
def test_synthetic_entrypoint_resumes_completed_year_and_refuses_corruption(
    tmp_path,
    monkeypatch,
    publication_crash,
):
    from bolivia_fire.landcover.earthengine import ASSET

    paths = Paths(tmp_path)
    paths.manifests.mkdir(parents=True)
    directory = paths.datasets / "processed/landcover"
    directory.mkdir(parents=True)
    cells = synthetic_cells()
    country = box(0, 0, 0.1, 0.05)
    grid = RasterGrid.from_cells(cells)
    pixels = np.zeros((1, 2), dtype=[(name, "f8") for name in BANDS])
    pixels["forest"] = 1
    normalize_pixels(pixels, cells, grid, 2012).to_parquet(
        directory / "pilot_1000_2012.parquet", index=False
    )
    ledger = synthetic_ledger(tmp_path / "usage.json", workstream_safety_cap_eecu_hours=60)
    monkeypatch.setattr(acquire, "load_project", lambda root: "synthetic-project")
    monkeypatch.setattr(acquire, "load_cells", lambda paths: (cells, country))
    monkeypatch.setattr(
        acquire, "ResourceMonitor", lambda root: nullcontext(SimpleNamespace(check=lambda: None))
    )
    monkeypatch.setattr(
        acquire,
        "EarthEngineSession",
        lambda paths, ledger, resources: SimpleNamespace(
            project="synthetic-project", ledger=ledger
        ),
    )
    submitted = []
    interrupt = [True]
    if publication_crash:
        real_replace = acquire.os.replace
        crashed = [False]

        def synthetic_crash(source, destination):
            if str(destination).endswith("raster_2012.npz") and not crashed[0]:
                crashed[0] = True
                raise OSError("synthetic interrupted publication")
            return real_replace(source, destination)

        monkeypatch.setattr(acquire.os, "replace", synthetic_crash)

    def synthetic_year(session, cells, country, year, reservation):
        submitted.append(year)
        if year == 2013 and interrupt[0] and not publication_crash:
            raise RuntimeError("synthetic interrupted year")
        session.ledger.reserve(f"synthetic-{year}", reservation)
        session.ledger.reconcile(f"synthetic-{year}", reported_eecu_seconds=1, upper_eecu_seconds=1)
        return (
            normalize_pixels(pixels, cells, grid, year),
            pixels,
            {
                "asset": ASSET,
                "year": year,
                "classification_band": f"classification_{year}",
                "grid": grid.request_grid(),
                "reported_eecu_seconds": 1,
                "upper_eecu_seconds": 1,
                "national_geometry_sha256": hashlib.sha256(
                    json.dumps(mapping(country), sort_keys=True).encode()
                ).hexdigest(),
                "cells_geometry_sha256": hashlib.sha256(
                    cells.to_json(orient="records", double_precision=12).encode()
                ).hexdigest(),
            },
        )

    monkeypatch.setattr(acquire, "raster_year", synthetic_year)
    args = argparse.Namespace(ledger=ledger.path, report=tmp_path / "report.json")
    assert acquire.run_raster(args, paths) == 2
    assert submitted == ([2012] if publication_crash else [2012, 2013])
    initial_hash = hashlib.sha256((directory / "mapbiomas_2012.parquet").read_bytes()).hexdigest()
    state = ledger._load()
    state.update(status="active", submissions_authorized=True)
    ledger.path.write_text(json.dumps(state), encoding="utf-8")
    interrupt[0] = False
    submitted.clear()
    assert acquire.run_raster(args, paths) == 0
    assert submitted == list(range(2013, 2026))
    assert (
        hashlib.sha256((directory / "mapbiomas_2012.parquet").read_bytes()).hexdigest()
        == initial_hash
    )
    (directory / "mapbiomas_2012.parquet").write_bytes(b"synthetic corruption")
    state = ledger._load()
    state.update(status="active", submissions_authorized=True)
    ledger.path.write_text(json.dumps(state), encoding="utf-8")
    submitted.clear()
    assert acquire.run_raster(args, paths) == 2
    assert submitted == []
