from __future__ import annotations

import argparse
import json
from contextlib import nullcontext
from types import SimpleNamespace

import numpy as np
import pytest

from bolivia_fire.config import Paths
from bolivia_fire.landcover import acquire
from bolivia_fire.landcover.raster import BANDS, RasterGrid, normalize_pixels
from tests.landcover.test_raster import synthetic_cells
from tests.landcover.test_usage import synthetic_ledger


@pytest.mark.parametrize(
    ("cost_seconds", "agreement", "expected_years", "expected_status"),
    [
        (7200, True, [2012], "stopped"),
        (1, False, [2012], "stopped"),
        (1, True, list(range(2012, 2026)), "completed"),
    ],
)
def test_synthetic_acquisition_requires_both_cost_and_agreement_gates(
    tmp_path, monkeypatch, cost_seconds, agreement, expected_years, expected_status
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
    ledger = synthetic_ledger(tmp_path / "usage.json")
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
    assert submitted == expected_years
    assert saved["years_completed"] == expected_years
    assert saved["status"] == expected_status
    assert result == (0 if expected_status == "completed" else 2)
    state = json.loads(ledger.path.read_text("utf-8"))
    assert state["status"] == "paused"
    assert state["submissions_authorized"] is False
