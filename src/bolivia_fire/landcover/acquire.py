from __future__ import annotations

import argparse
import hashlib
import json
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
from filelock import FileLock
from shapely.geometry import mapping

from bolivia_fire.config import Paths
from bolivia_fire.landcover.core import GROUPS
from bolivia_fire.landcover.earthengine import (
    ASSET,
    EarthEngineSession,
    ResourceMonitor,
    load_cells,
    load_project,
)
from bolivia_fire.landcover.monitoring import MonitoringClient
from bolivia_fire.landcover.raster import (
    RasterGrid,
    compare_polygon_pilot,
    normalize_pixels,
    projection_hours,
    raster_year,
)
from bolivia_fire.landcover.usage import UsageLedger, UsagePaused, atomic_json, redact_project


def _update_ledger(ledger: UsageLedger, **fields: Any) -> dict[str, Any]:
    with FileLock(str(ledger.path) + ".lock", timeout=0):
        state = json.loads(ledger.path.read_text("utf-8"))
        state.update(fields)
        atomic_json(ledger.path, state)
    return state


def _write_output(
    frame: pd.DataFrame, pixels: np.ndarray, paths: Paths, year: int, report: dict[str, Any]
) -> dict[str, Any]:
    directory = paths.datasets / "processed/landcover"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"mapbiomas_{year}.parquet"
    raster_path = directory / f"raster_{year}.npz"
    staging = directory / "annual_staging" / uuid.uuid4().hex
    staging.mkdir(parents=True)
    staged_frame, staged_pixels = staging / "frame.parquet", staging / "pixels.npz"
    frame.to_parquet(staged_frame, index=False)
    np.savez_compressed(staged_pixels, pixels=pixels)
    fields = {
        "output": destination.relative_to(paths.root).as_posix(),
        "output_bytes": staged_frame.stat().st_size,
        "output_sha256": hashlib.sha256(staged_frame.read_bytes()).hexdigest(),
        "raster_output": raster_path.relative_to(paths.root).as_posix(),
        "raster_output_bytes": staged_pixels.stat().st_size,
        "raster_output_sha256": hashlib.sha256(staged_pixels.read_bytes()).hexdigest(),
    }
    with FileLock(str(directory / f"annual_{year}.lock"), timeout=0):
        if destination.exists() or raster_path.exists():
            raise UsagePaused("Annual raster output exists; refusing replacement")
        complete = {**report, **fields}
        atomic_json(
            directory / f"annual_pending_{year}.json",
            {
                "report": complete,
                "staged_output": staged_frame.relative_to(paths.root).as_posix(),
                "staged_raster_output": staged_pixels.relative_to(paths.root).as_posix(),
            },
        )
        with (paths.manifests / "mapbiomas_raster.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(complete, allow_nan=False) + "\n")
        os.replace(staged_frame, destination)
        os.replace(staged_pixels, raster_path)
    return fields


def _restore_outputs(
    paths: Paths,
    cells: pd.DataFrame,
    country: Any,
) -> dict[int, tuple[pd.DataFrame, np.ndarray, dict[str, Any]]]:
    directory = paths.datasets / "processed/landcover"
    manifest = paths.manifests / "mapbiomas_raster.jsonl"
    records = {}
    if manifest.exists():
        for line in manifest.read_text("utf-8").splitlines():
            record = json.loads(line)
            records[record["year"]] = record
    grid = RasterGrid.from_cells(cells)
    restored = {}
    for year in range(2012, 2026):
        destination, array_path = (
            directory / f"mapbiomas_{year}.parquet",
            directory / f"raster_{year}.npz",
        )
        pending = directory / f"annual_pending_{year}.json"
        journal = json.loads(pending.read_text("utf-8")) if pending.exists() else None
        record = records.get(year) or (journal["report"] if journal else None)
        if not destination.exists() and not array_path.exists() and record is None:
            continue
        if record is None:
            raise UsagePaused("Annual outputs lack a publication manifest")
        expected = {
            "asset": ASSET,
            "year": year,
            "classification_band": f"classification_{year}",
            "grid": grid.request_grid(),
            "legend_groups": {name: list(codes) for name, codes in GROUPS.items()},
            "national_geometry_sha256": hashlib.sha256(
                json.dumps(mapping(country), sort_keys=True).encode()
            ).hexdigest(),
            "cells_geometry_sha256": hashlib.sha256(
                cells.to_json(orient="records", double_precision=12).encode()
            ).hexdigest(),
        }
        if any(record.get(key) != value for key, value in expected.items()):
            raise UsagePaused("Annual output provenance differs from the current inputs")
        actual_paths = {}
        for key, destination_path in (("output", destination), ("raster_output", array_path)):
            if record.get(key) != destination_path.relative_to(paths.root).as_posix():
                raise UsagePaused("Annual output path differs from the publication manifest")
            path = destination_path
            if not path.exists() and journal:
                path = paths.root / journal[f"staged_{key}"]
            if not path.resolve().is_relative_to(directory.resolve()):
                raise UsagePaused("Annual staging path escapes the output directory")
            if (
                not path.exists()
                or path.stat().st_size != record[key + "_bytes"]
                or hashlib.sha256(path.read_bytes()).hexdigest() != record[key + "_sha256"]
            ):
                raise UsagePaused("Annual output checksum or size differs")
            actual_paths[key] = path
        frame = pd.read_parquet(actual_paths["output"])
        with np.load(actual_paths["raster_output"], allow_pickle=False) as archive:
            pixels = archive["pixels"]
        try:
            pd.testing.assert_frame_equal(
                frame,
                normalize_pixels(pixels, cells, grid, year),
                check_dtype=False,
                check_exact=True,
            )
        except AssertionError:
            raise UsagePaused("Annual feature table differs from its raster") from None
        if year == 2012 and (
            record.get("polygon_pilot_validation", {}).get("passed") is not True
            or record.get("pilot_gates_passed") is not True
        ):
            raise UsagePaused("Annual pilot publication lacks passed gate evidence")
        with FileLock(str(directory / f"annual_{year}.lock"), timeout=0):
            if year not in records:
                with manifest.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, allow_nan=False) + "\n")
            for key, destination_path in (("output", destination), ("raster_output", array_path)):
                if actual_paths[key] != destination_path:
                    if destination_path.exists() or not destination_path.resolve().is_relative_to(
                        directory.resolve()
                    ):
                        raise UsagePaused("Annual recovery would replace an existing output")
                    os.replace(actual_paths[key], destination_path)
        restored[year] = (frame, pixels, {**record, "reused_annual_output": True})
    if restored and sorted(restored) != list(range(2012, max(restored) + 1)):
        raise UsagePaused("Completed annual outputs are not a contiguous prefix")
    return restored


def run_raster(args: argparse.Namespace, paths: Paths) -> int:
    ledger = UsageLedger(args.ledger)
    summary: dict[str, Any] = {
        "classification": "retrospective",
        "collection": "MapBiomas Bolivia Collection 4",
        "release_date": "2026-10-01",
        "licence": "CC BY 4.0",
        "pilot_year": 2012,
        "safety_factor": 1.5,
        "years_requested": list(range(2012, 2026)),
        "years_completed": [],
        "outputs": [],
        "status": "running",
    }
    project = ""
    report_path = args.report
    state = ledger._load()
    before = state["workstream_eecu_hours"]
    try:
        project = load_project(paths.root)
        with ResourceMonitor(paths.root) as resources:
            resources.check()
            cells, country = load_cells(paths)
            restored = _restore_outputs(paths, cells, country)
            known = set(restored)
            costs = {
                year: values[2]["upper_eecu_seconds"] / 3600 for year, values in restored.items()
            }
            summary["previously_completed_years"] = sorted(known)
            session = EarthEngineSession(paths, ledger, resources)
            project = session.project
            if state.get("accounting_primary") == "cloud_monitoring":
                session.monitoring = MonitoringClient(project, state["project_month_start_utc"])
            reservation = max(costs.values(), default=2 / 1.5) * 1.5
            atomic_json(report_path, summary)
            for year in summary["years_requested"]:
                reused = year in restored
                if reused:
                    frame, pixels, report = restored[year]
                    report = dict(report)
                    report["original_compute_pixels_requests"] = report.get(
                        "compute_pixels_requests"
                    )
                    report["compute_pixels_requests"] = 0
                else:
                    frame, pixels, report = raster_year(session, cells, country, year, reservation)
                report.update(
                    classification=summary["classification"],
                    collection=summary["collection"],
                    release_date=summary["release_date"],
                    licence=summary["licence"],
                    legend_groups={name: list(codes) for name, codes in GROUPS.items()},
                )
                if year == 2012:
                    polygon = pd.read_parquet(
                        paths.datasets / "processed/landcover/pilot_1000_2012.parquet"
                    )
                    validation = compare_polygon_pilot(frame, polygon)
                    report["polygon_pilot_validation"] = validation
                    summary["polygon_pilot_validation"] = validation
                    projection = projection_hours(report["upper_eecu_seconds"])
                    summary["full_14_year_projection_eecu_hours_1_5x"] = projection
                    summary["full_14_year_projection_eecu_hours"] = (
                        report["reported_eecu_seconds"] * 14 / 3600
                    )
                    report["full_14_year_projection_eecu_hours_1_5x"] = projection
                    state = ledger._load()
                    remaining = 14 - len(known | {year})
                    annual_bound = max([report["upper_eecu_seconds"] / 3600, *costs.values()])
                    total = (
                        state["workstream_eecu_hours"]
                        + state.get("unreconciled_overhead_reserve_eecu_hours", 0)
                        + annual_bound * remaining * 1.5
                    )
                    summary["projected_workstream_total_including_reserve_eecu_hours"] = total
                    summary["pilot_tiles_complete"] = True
                    summary["pilot_report"] = dict(report)
                    cap = state["workstream_safety_cap_eecu_hours"]
                    if projection > cap or total > cap:
                        raise UsagePaused(
                            "Raster pilot projects above the authorized EECU-hour cap "
                            "with 1.5x safety"
                        )
                    if not validation["passed"]:
                        raise UsagePaused(
                            "Raster pilot fails the predeclared polygon agreement tolerance"
                        )
                    report["pilot_gates_passed"] = True
                    _update_ledger(
                        ledger,
                        raster_full_14_year_projection_eecu_hours_1_5x=projection,
                        full_aggregation_estimate_eecu_hours=annual_bound * remaining * 1.5,
                        raster_pilot_authorized=False,
                        raster_pilot_evidence=report_path.name,
                        raster_pilot_validation_passed=True,
                        monitoring_expanded_batches_authorized=(
                            state.get("monitoring_year_sized_batches_allowed") is True
                            or state.get("monitoring_twenty_tile_batches_allowed") is True
                        ),
                    )
                if not reused:
                    report.update(_write_output(frame, pixels, paths, year, report))
                    known.add(year)
                summary["years_completed"].append(year)
                summary["outputs"].append(report)
                costs[year] = report["upper_eecu_seconds"] / 3600
                reservation = max(costs.values()) * 1.5
                _update_ledger(
                    ledger, full_aggregation_estimate_eecu_hours=reservation * (14 - len(known))
                )
                atomic_json(report_path, summary)
            summary["status"] = "completed"
            _update_ledger(
                ledger,
                status="paused",
                submissions_authorized=False,
                pause_reason="All fourteen annual raster maps downloaded; submissions disabled",
            )
    except Exception as exc:
        summary["status"] = "stopped"
        summary["stop_reason"] = redact_project(str(exc), project)
        _update_ledger(
            ledger,
            status="paused",
            submissions_authorized=False,
            pause_reason=summary["stop_reason"],
        )
    state = ledger._load()
    summary.update(
        completed_at_utc=datetime.now(UTC).isoformat(timespec="seconds"),
        workstream_eecu_hours=state["workstream_eecu_hours"],
        raster_run_upper_eecu_hours=(
            state["workstream_eecu_hours"] - before
            if state["workstream_eecu_hours"] is not None
            else None
        ),
        unreconciled_overhead_reserve_eecu_hours=state.get(
            "unreconciled_overhead_reserve_eecu_hours"
        ),
    )
    atomic_json(report_path, summary)
    public = {
        key: value for key, value in summary.items() if key not in {"outputs", "pilot_report"}
    }
    print(redact_project(json.dumps(public, allow_nan=False), project), flush=True)
    return 0 if summary["status"] == "completed" else 2
