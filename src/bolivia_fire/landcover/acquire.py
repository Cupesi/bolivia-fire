from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
from filelock import FileLock

from bolivia_fire.config import Paths
from bolivia_fire.landcover.core import GROUPS
from bolivia_fire.landcover.earthengine import (
    EarthEngineSession,
    ResourceMonitor,
    load_cells,
    load_project,
)
from bolivia_fire.landcover.raster import compare_polygon_pilot, projection_hours, raster_year
from bolivia_fire.landcover.usage import UsageLedger, UsagePaused, atomic_json, redact_project


def _update_ledger(ledger: UsageLedger, **fields: Any) -> dict[str, Any]:
    with FileLock(str(ledger.path) + ".lock", timeout=0):
        state = json.loads(ledger.path.read_text("utf-8"))
        state.update(fields)
        atomic_json(ledger.path, state)
    return state


def _write_output(
    frame: pd.DataFrame, pixels: np.ndarray, paths: Paths, year: int
) -> dict[str, Any]:
    directory = paths.datasets / "processed/landcover"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"mapbiomas_{year}.parquet"
    raster_path = directory / f"raster_{year}.npz"
    if destination.exists() or raster_path.exists():
        raise UsagePaused("Annual raster output exists; refusing replacement")
    frame.to_parquet(destination, index=False)
    np.savez_compressed(raster_path, pixels=pixels)
    return {
        "output": destination.relative_to(paths.root).as_posix(),
        "output_bytes": destination.stat().st_size,
        "output_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "raster_output": raster_path.relative_to(paths.root).as_posix(),
        "raster_output_bytes": raster_path.stat().st_size,
        "raster_output_sha256": hashlib.sha256(raster_path.read_bytes()).hexdigest(),
    }


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
    }
    project = ""
    report_path = args.report
    state = json.loads(ledger.path.read_text("utf-8"))
    before = state["workstream_eecu_hours"]
    try:
        project = load_project(paths.root)
        with ResourceMonitor(paths.root) as resources:
            resources.check()
            cells, country = load_cells(paths)
            for year in summary["years_requested"]:
                if any(
                    (paths.datasets / "processed/landcover" / filename).exists()
                    for filename in (f"mapbiomas_{year}.parquet", f"raster_{year}.npz")
                ):
                    raise UsagePaused(
                        "Annual raster outputs already exist; review before submission"
                    )
            session = EarthEngineSession(paths, ledger, resources)
            project = session.project
            reservation = 2.0
            for year in summary["years_requested"]:
                frame, pixels, report = raster_year(session, cells, country, year, reservation)
                report.update(_write_output(frame, pixels, paths, year))
                report.update(
                    classification=summary["classification"],
                    collection=summary["collection"],
                    release_date=summary["release_date"],
                    licence=summary["licence"],
                    legend_groups={name: list(codes) for name, codes in GROUPS.items()},
                )
                summary["years_completed"].append(year)
                summary["outputs"].append(report)
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
                    state = json.loads(ledger.path.read_text("utf-8"))
                    total = (
                        before
                        + state.get("unreconciled_overhead_reserve_eecu_hours", 0)
                        + projection
                    )
                    summary["projected_workstream_total_including_reserve_eecu_hours"] = total
                    _update_ledger(
                        ledger,
                        raster_full_14_year_projection_eecu_hours_1_5x=projection,
                        full_aggregation_estimate_eecu_hours=projection * 13 / 14,
                        raster_pilot_authorized=False,
                        raster_pilot_evidence="reviews/G1_raster_report.json",
                    )
                with (paths.manifests / "mapbiomas_raster.jsonl").open(
                    "a", encoding="utf-8"
                ) as stream:
                    stream.write(
                        redact_project(json.dumps(report, allow_nan=False), project) + "\n"
                    )
                atomic_json(report_path, summary)
                if year == 2012:
                    if total > 30:
                        raise UsagePaused(
                            "Raster pilot projects above the 30 EECU-hour cap with 1.5x safety"
                        )
                    if not validation["passed"]:
                        raise UsagePaused(
                            "Raster pilot fails the predeclared polygon agreement tolerance"
                        )
                remaining = 2025 - year
                upper_rate = max(record["upper_eecu_seconds"] for record in summary["outputs"])
                reservation = upper_rate * 1.5 / 3600
                _update_ledger(ledger, full_aggregation_estimate_eecu_hours=reservation * remaining)
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
    state = json.loads(ledger.path.read_text("utf-8"))
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
    public = {key: value for key, value in summary.items() if key != "outputs"}
    print(redact_project(json.dumps(public, allow_nan=False), project), flush=True)
    return 0 if summary["status"] == "completed" else 2
