from __future__ import annotations

import contextlib
import hashlib
import io
import json
import logging
import multiprocessing
import time
from collections import deque
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Executor, ProcessPoolExecutor, wait
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from filelock import FileLock
from shapely.geometry import mapping

from bolivia_fire.landcover.core import FEATURES, GROUPS
from bolivia_fire.landcover.earthengine import ASSET, EarthEngineSession, load_project
from bolivia_fire.landcover.monitoring import METRIC, wait_for_settlement
from bolivia_fire.landcover.raster import (
    BANDS,
    INVALID_BANDS,
    RasterGrid,
    _checkpoint_record,
    _restore_tile,
    normalize_pixels,
    tile_country_geometry,
)
from bolivia_fire.landcover.usage import UsagePaused, atomic_json, parse_profile, redact_project

_worker_ee: Any = None
_worker_project = ""
_worker_error: dict[str, Any] | None = None


def quota_error(message: str) -> bool:
    return any(
        word in message.lower()
        for word in (
            "429",
            "quota",
            "rate limit",
            "too many requests",
            "too many concurrent",
        )
    )


def initialize_worker(root: Path) -> None:
    global _worker_ee, _worker_project, _worker_error
    import ee
    import httplib2

    logging.disable(logging.CRITICAL)
    try:
        _worker_project = load_project(root)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            ee.Initialize(
                ee.data.get_persistent_credentials(),
                project=_worker_project,
                http_transport=httplib2.Http(timeout=300),
            )
            ee.data.setDeadline(300000)
            ee.data.setMaxRetries(0)
        if ee.data.getProjectConfig().get("registrationState") != "REGISTERED_NOT_COMMERCIALLY":
            raise UsagePaused("Worker registration is not noncommercial")
        _worker_ee = ee
    except Exception as exc:
        _worker_error = {
            "pixels": None,
            "profile": "",
            "error": "Earth Engine worker initialization failed",
            "quota_error": quota_error(str(exc)),
            "profile_error": None,
        }


def compute_tile(job: dict[str, Any]) -> dict[str, Any]:
    if _worker_error is not None:
        return dict(_worker_error)
    ee = _worker_ee
    profile = io.StringIO()
    response = None
    error = None
    profile_error = None
    started = time.monotonic()
    try:
        classification = ee.Image(ASSET).select(f"classification_{job['year']}").unmask(0, False)
        masks = [
            classification.remap(list(codes), [1] * len(codes), 0).rename(name)
            for name, codes in GROUPS.items()
        ]
        known = [code for codes in GROUPS.values() for code in codes]
        masks.extend(
            [
                classification.eq(0).rename("invalid_0"),
                classification.eq(27).rename("invalid_27"),
                classification.remap([*known, 0, 27], [1] * (len(known) + 2), 0)
                .eq(0)
                .rename("invalid_other"),
            ]
        )
        image = (
            ee.Image.cat(masks)
            .toFloat()
            .clip(ee.Geometry(job["geometry"], proj="EPSG:4326", geodesic=False))
            .unmask(0, False)
            .reduceResolution(reducer=ee.Reducer.mean(), bestEffort=False, maxPixels=65536)
            .reproject(crs="EPSG:4326", crsTransform=job["transform"])
            .toFloat()
        )
        with ee.profilePrinting(destination=profile):
            try:
                response = ee.data.computePixels(
                    {
                        "expression": image,
                        "fileFormat": "NUMPY_NDARRAY",
                        "grid": job["grid"],
                        "bandIds": list(BANDS),
                        "workloadTag": f"bolivia-fire-g1-raster-{job['year']}",
                    }
                )
            except Exception as exc:
                error = redact_project(str(exc), _worker_project)
    except Exception as exc:
        message = redact_project(str(exc), _worker_project)
        if error is None and response is None:
            error = message
        else:
            profile_error = message
    valid = (
        response is not None
        and response.shape
        == (job["grid"]["dimensions"]["height"], job["grid"]["dimensions"]["width"])
        and set(response.dtype.names or ()) == set(BANDS)
    )
    return {
        "pixels": response if valid else None,
        "profile": redact_project(profile.getvalue(), _worker_project),
        "error": None if valid else (error or "Unexpected raster response shape or bands"),
        "profile_error": profile_error,
        "quota_error": quota_error(error or "") or quota_error(profile_error or ""),
        "runtime_seconds": time.monotonic() - started,
    }


def run_jobs(
    jobs: Sequence[dict[str, Any]],
    consume: Callable[[dict[str, Any], dict[str, Any]], bool],
    *,
    root: Path,
    maximum_workers: int = 4,
    backoff_steps: Sequence[int] = (1,),
    compute: Callable[[dict[str, Any]], dict[str, Any]] = compute_tile,
    pool_factory: Callable[[int], Executor] | None = None,
    on_rate_limit: Callable[[], None] = lambda: None,
    pause: Callable[[float], None] = time.sleep,
    check: Callable[[], None] = lambda: None,
) -> dict[str, Any]:
    if (
        maximum_workers not in (1, 2, 4, 6)
        or not backoff_steps
        or any(value not in (1, 2, 4) for value in backoff_steps)
    ):
        raise UsagePaused("Concurrent Earth Engine request limit is invalid")
    queue = deque((dict(job), 1) for job in jobs)
    pending = {}
    desired = maximum_workers
    submitted = 0
    errors = 0
    fatal = None
    executor = (
        pool_factory(maximum_workers)
        if pool_factory
        else ProcessPoolExecutor(
            max_workers=maximum_workers,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=initialize_worker,
            initargs=(root,),
        )
    )
    with executor as pool:
        while queue or pending:
            check()
            while queue and fatal is None and len(pending) < desired:
                job, attempt = queue.popleft()
                pending[pool.submit(compute, job)] = (job, attempt)
                submitted += 1
            if not pending:
                break
            done, _ = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
            for future in done:
                job, attempt = pending.pop(future)
                try:
                    result = future.result()
                except Exception:
                    result = {
                        "pixels": None,
                        "profile": "",
                        "quota_error": False,
                        "error": "Earth Engine worker failed; details suppressed",
                    }
                if result.get("quota_error"):
                    desired = next(
                        (value for value in backoff_steps if value < desired),
                        min(desired, backoff_steps[-1]),
                    )
                    errors += 1
                    on_rate_limit()
                succeeded = consume({**job, "attempt": attempt}, result)
                if not succeeded:
                    if result.get("quota_error") and attempt < 2 and fatal is None:
                        pause(30)
                        queue.appendleft((job, attempt + 1))
                    else:
                        fatal = result.get("error") or "Raster tile did not return valid data"
                        queue.clear()
    return {
        "submitted": submitted,
        "quota_errors": errors,
        "final_parallelism": desired,
        "failed_error": fatal,
    }


def _single_raster_year(
    session: EarthEngineSession,
    cells: pd.DataFrame,
    country: Any,
    year: int,
) -> tuple[pd.DataFrame, np.ndarray, dict[str, Any]]:
    session.resources.check()
    started = time.monotonic()
    grid = RasterGrid.from_cells(cells)
    tiles = grid.tiles(cells=cells)
    base = f"raster-national-{year}-binary-cropped-v8"
    manifest = session.paths.manifests / "mapbiomas_raster_requests.jsonl"
    checkpoints = {}
    if manifest.exists():
        for line in manifest.read_text("utf-8").splitlines():
            record = json.loads(line)
            if "output" in record:
                checkpoints[record["output"]] = record
    state = session.ledger._load()
    group = getattr(session, "_active_monitoring_group", None)
    trusted = {
        item["batch"]
        for item in state["batches"]
        if item["status"] in ("measured", "aggregate_reconciled", "monitoring_reconciled")
    }
    pixels = np.full((grid.height, grid.width), np.nan, dtype=[(name, "f8") for name in BANDS])
    requests = []
    jobs = []
    for index, (row, col, tile) in enumerate(tiles):
        geometry = mapping(tile_country_geometry(country, tile, session.transform[0]))
        geometry_sha = hashlib.sha256(json.dumps(geometry, sort_keys=True).encode()).hexdigest()
        checkpoint = (
            session.paths.datasets
            / "processed/landcover"
            / f"raster_tiles_{year}"
            / "cropped"
            / f"row_{row}_col_{col}.npz"
        )
        relative = checkpoint.relative_to(session.paths.root).as_posix()
        if checkpoint.exists():
            record = checkpoints.get(relative)
            response = _restore_tile(
                checkpoint, record, tile, year, geometry_sha, reconciled_batches=trusted
            )
            pixels[row : row + tile.height, col : col + tile.width] = response
            requests.append(record)
        elif relative in checkpoints:
            raise UsagePaused("Recorded raster checkpoint is unavailable")
        else:
            jobs.append(
                {
                    "id": index,
                    "row": row,
                    "col": col,
                    "year": year,
                    "grid": tile.request_grid(),
                    "geometry": geometry,
                    "geometry_sha256": geometry_sha,
                    "transform": grid.transform,
                    "checkpoint": str(checkpoint),
                }
            )
    reused = len(requests)
    stats = {"submitted": 0, "quota_errors": 0, "final_parallelism": 1, "failed_error": None}
    batch = base + "-year-monitoring"
    existing = {item["batch"] for item in state["batches"]}
    attempt = 1
    while batch in existing:
        attempt += 1
        batch = base + f"-year-monitoring-attempt-{attempt}"
    if group is not None:
        batch = group["batch"]
    seen = set()

    def on_rate_limit() -> None:
        with FileLock(str(session.ledger.path) + ".lock", timeout=0):
            current = session.ledger._load()
            steps = current.get("monitoring_quota_backoff_steps", [1])
            previous = current.get("monitoring_max_parallel_requests", 4)
            current["monitoring_max_parallel_requests"] = next(
                (value for value in steps if value < previous), min(previous, steps[-1])
            )
            current["quota_backoff_events"] = current.get("quota_backoff_events", 0) + 1
            atomic_json(session.ledger.path, current)

    def consume(job: dict[str, Any], result: dict[str, Any]) -> bool:
        seen.add(job["id"])
        response = result.get("pixels")
        checkpoint = Path(job["checkpoint"])
        fields = {}
        if response is not None:
            if checkpoint.exists():
                raise UsagePaused("Concurrent response would replace a saved tile")
            fields = _checkpoint_record(checkpoint, session.paths.root, response)
            height, width = response.shape
            row, col = job["row"], job["col"]
            pixels[row : row + height, col : col + width] = response
        try:
            cost = parse_profile(result.get("profile", ""))
        except UsagePaused:
            cost = None
        suffix = f"-attempt-{job['attempt']}" if job["attempt"] > 1 else ""
        report = {
            "batch": base + f"-tile-{job['id']}" + suffix,
            "monitoring_batch": batch,
            "year": year,
            "grid": job["grid"],
            "national_geometry_sha256": job["geometry_sha256"],
            "national_geometry_scope": "tile ADM0 intersection plus one native-pixel halo",
            "accounting_primary": "cloud_monitoring",
            "status": "awaiting_monitoring" if fields else "failed",
            "profile": result.get("profile", ""),
            "profile_error": result.get("profile_error"),
            "error": result.get("error"),
            "quota_error": result.get("quota_error", False),
            "reported_eecu_seconds": cost.reported_eecu_seconds if cost else None,
            "upper_eecu_seconds": cost.upper_eecu_seconds if cost else None,
            "runtime_seconds": result.get("runtime_seconds"),
            **fields,
        }
        with manifest.open("a", encoding="utf-8") as stream:
            stream.write(
                redact_project(json.dumps(report, allow_nan=False), session.project) + "\n"
            )
        requests.append(report)
        return bool(fields)

    if jobs:
        if group is None:
            session.ledger.reserve_monitoring(
                batch, len(jobs), len(jobs) + (2025 - year) * len(tiles)
            )
        baseline = session.ledger._load()["project_month_eecu_hours"] * 3600
        failure = None
        try:
            stats = run_jobs(
                jobs,
                consume,
                root=session.paths.root,
                maximum_workers=state.get("monitoring_max_parallel_requests", 4),
                backoff_steps=state.get("monitoring_quota_backoff_steps", [1]),
                on_rate_limit=on_rate_limit,
                check=session.resources.check,
            )
            failure = stats["failed_error"]
        except Exception as exc:
            failure = redact_project(str(exc), session.project)
        if group is not None:
            group["submitted"] += max(stats["submitted"], len(requests) - reused)
            group["completed"] += sum("output" in item for item in requests[reused:])
            group["quota_errors"] += stats["quota_errors"]
            group["final_parallelism"] = stats["final_parallelism"]
        else:
            _settle_single_year(session, batch, baseline, stats, seen, requests[reused:], year)
        if failure:
            raise UsagePaused(failure)
    session.resources.check()
    frame = normalize_pixels(pixels, cells, grid, year)
    state = session.ledger._load()
    cost, shared = _year_cost(state, year)
    report = {
        "batch": base,
        "asset": ASSET,
        "year": year,
        "classification_band": f"classification_{year}",
        "crs": "EPSG:4326",
        "crs_transform": grid.transform,
        "grid": grid.request_grid(),
        "native_crs_transform": session.transform,
        "national_geometry_sha256": hashlib.sha256(
            json.dumps(mapping(country), sort_keys=True).encode()
        ).hexdigest(),
        "cells_geometry_sha256": hashlib.sha256(
            cells.to_json(orient="records", double_precision=12).encode()
        ).hexdigest(),
        "method": (
            "native national mask; binary class means in EPSG:4326; "
            "local normalization and pinned geodesic national areas"
        ),
        "max_pixels": 65536,
        "reduction_transforms": [grid.transform],
        "best_effort": False,
        "invalid_bands": list(INVALID_BANDS),
        "direct_download": "computePixels NUMPY_NDARRAY; no exports",
        "accounting_primary": "cloud_monitoring",
        "reported_eecu_seconds": cost,
        "upper_eecu_seconds": cost,
        "project_total_eecu_hours": state["workstream_eecu_hours"],
        "year_cost_scope": _cost_scope(shared),
        "requests": requests,
        "compute_pixels_requests": stats["submitted"],
        "reused_tiles": reused,
        "completed_tiles": sum("output" in item for item in requests),
        "secondary_profile_tiles": sum(
            item.get("reported_eecu_seconds") is not None for item in requests
        ),
        "skipped_no_project_cell_tiles": len(grid.tiles()) - len(tiles),
        "response_array_bytes": pixels.nbytes,
        "runtime_seconds": time.monotonic() - started,
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "cells": len(cells),
        "available_cells": int(frame.available.sum()),
        "missing_response_cells": int((~frame.response_feature_present).sum()),
        "fraction_sum_max_absolute_error": float(
            abs(frame.loc[frame.available, list(FEATURES)].sum(axis=1) - 1).max()
        ),
        "peak_process_tree_rss_bytes": session.resources.peak_rss_bytes,
        "minimum_free_disk_bytes": session.resources.minimum_free_bytes,
        "maximum_parallel_requests": state.get("monitoring_max_parallel_requests", 4),
    }
    return frame, pixels, report


def _settle_single_year(
    session: EarthEngineSession,
    batch: str,
    baseline: float,
    stats: dict[str, Any],
    seen: set[int],
    requests: Sequence[dict[str, Any]],
    year: int,
) -> None:
    snapshot = wait_for_settlement(
        session.monitoring.read,
        baseline_seconds=baseline,
        completed_at=datetime.now(UTC),
    )
    session.ledger.reconcile_monitoring(
        batch,
        snapshot.total_seconds,
        snapshot.observed_at.isoformat(),
        submitted_tile_count=max(stats["submitted"], len(seen)),
        completed_tile_count=sum("output" in item for item in requests),
    )
    entry = next(item for item in session.ledger._load()["batches"] if item["batch"] == batch)
    entry.update(
        year=year,
        latest_point_end_utc=snapshot.latest_point_end.isoformat(),
        monitoring_points=snapshot.points,
        monitoring_series=snapshot.series,
        submitted_tile_attempts=stats["submitted"],
        quota_errors=stats["quota_errors"],
        final_parallelism=stats["final_parallelism"],
        batch_scope="one_map_year",
    )
    with (session.paths.manifests / "mapbiomas_monitoring_batches.jsonl").open(
        "a",
        encoding="utf-8",
    ) as stream:
        stream.write(json.dumps(entry, allow_nan=False) + "\n")
    print(
        json.dumps(
            {
                "year": year,
                "batch": batch,
                "monitoring_total_eecu_hours": snapshot.total_seconds / 3600,
                "batch_delta_eecu_seconds": entry["monitoring_delta_eecu_seconds"],
            }
        ),
        flush=True,
    )


def _year_cost(state: dict[str, Any], year: int) -> tuple[float, bool]:
    total = 0.0
    shared = False
    for entry in state["batches"]:
        if entry["status"] != "monitoring_reconciled":
            continue
        years = entry.get("years", [])
        if year in years:
            total += entry["monitoring_delta_eecu_seconds"] / len(years)
            shared |= len(years) > 1
        elif not years and entry["batch"].startswith(f"raster-national-{year}-"):
            total += entry["monitoring_delta_eecu_seconds"]
    return total, shared


def _cost_scope(shared: bool) -> str:
    if shared:
        return (
            "Equal allocation of shared two-year Monitoring deltas; "
            "per-year cost is not measured"
        )
    return "Project Monitoring year deltas including initialization and failed requests"


def parallel_raster_year(
    session: EarthEngineSession,
    cells: pd.DataFrame,
    country: Any,
    year: int,
) -> tuple[pd.DataFrame, np.ndarray, dict[str, Any]]:
    cache = getattr(session, "_raster_year_cache", {})
    session._raster_year_cache = cache
    if year in cache:
        return cache.pop(year)
    if session.ledger.monitoring_batch_limit() != 360 or year >= 2025:
        return _single_raster_year(session, cells, country, year)
    years = (year, year + 1)
    grid = RasterGrid.from_cells(cells)
    tiles = grid.tiles(cells=cells)
    manifest = session.paths.manifests / "mapbiomas_raster_requests.jsonl"
    checkpoints = {}
    if manifest.exists():
        for line in manifest.read_text("utf-8").splitlines():
            record = json.loads(line)
            if "output" in record:
                checkpoints[record["output"]] = record
    state = session.ledger._load()
    trusted = {
        entry["batch"]
        for entry in state["batches"]
        if entry["status"]
        in (
            "measured",
            "aggregate_reconciled",
            "monitoring_reconciled",
        )
    }
    missing = 0
    for selected in years:
        for row, col, tile in tiles:
            path = (
                session.paths.datasets
                / "processed/landcover"
                / f"raster_tiles_{selected}"
                / "cropped"
                / f"row_{row}_col_{col}.npz"
            )
            relative = path.relative_to(session.paths.root).as_posix()
            if path.exists():
                geometry = mapping(tile_country_geometry(country, tile, session.transform[0]))
                geometry_sha = hashlib.sha256(
                    json.dumps(geometry, sort_keys=True).encode()
                ).hexdigest()
                _restore_tile(
                    path,
                    checkpoints.get(relative),
                    tile,
                    selected,
                    geometry_sha,
                    reconciled_batches=trusted,
                )
            elif relative in checkpoints:
                raise UsagePaused("Recorded raster checkpoint is unavailable")
            else:
                missing += 1
    if not missing:
        first = _single_raster_year(session, cells, country, year)
        cache[year + 1] = _single_raster_year(session, cells, country, year + 1)
        return first
    base = f"raster-national-{year}-{year + 1}-binary-cropped-v8-pair-monitoring"
    batch = base
    existing = {entry["batch"] for entry in state["batches"]}
    attempt = 1
    while batch in existing:
        attempt += 1
        batch = base + f"-attempt-{attempt}"
    session.ledger.reserve_monitoring(batch, missing, missing + (2025 - years[-1]) * len(tiles))
    baseline = session.ledger._load()["project_month_eecu_hours"] * 3600
    group = {
        "batch": batch,
        "submitted": 0,
        "completed": 0,
        "quota_errors": 0,
        "final_parallelism": state.get("monitoring_max_parallel_requests", 6),
    }
    results = {}
    failure = None
    started = time.monotonic()
    session._active_monitoring_group = group
    try:
        for selected in years:
            results[selected] = _single_raster_year(session, cells, country, selected)
    except Exception as exc:
        failure = redact_project(str(exc), session.project)
    finally:
        del session._active_monitoring_group
    completed_at = datetime.now(UTC)
    with FileLock(str(session.ledger.path) + ".lock", timeout=0):
        current = session.ledger._load()
        entry = next(item for item in current["batches"] if item["batch"] == batch)
        entry.update(
            year=year,
            metric=METRIC,
            years=list(years),
            completed_at_utc=completed_at.isoformat(),
            observed_submitted_tiles=group["submitted"],
            observed_completed_tiles=group["completed"],
        )
        atomic_json(session.ledger.path, current)
    snapshot = wait_for_settlement(
        session.monitoring.read, baseline_seconds=baseline, completed_at=completed_at
    )
    session.ledger.reconcile_monitoring(
        batch,
        snapshot.total_seconds,
        snapshot.observed_at.isoformat(),
        submitted_tile_count=group["submitted"],
        completed_tile_count=group["completed"],
    )
    with FileLock(str(session.ledger.path) + ".lock", timeout=0):
        current = session.ledger._load()
        entry = next(item for item in current["batches"] if item["batch"] == batch)
        entry.update(
            year=year,
            years=list(years),
            batch_scope="two_map_years",
            metric=METRIC,
            latest_point_end_utc=snapshot.latest_point_end.isoformat(),
            monitoring_points=snapshot.points,
            monitoring_series=snapshot.series,
            submitted_tile_attempts=group["submitted"],
            quota_errors=group["quota_errors"],
            final_parallelism=group["final_parallelism"],
        )
        atomic_json(session.ledger.path, current)
    with (session.paths.manifests / "mapbiomas_monitoring_batches.jsonl").open(
        "a", encoding="utf-8"
    ) as stream:
        stream.write(json.dumps(entry, allow_nan=False) + "\n")
    print(
        json.dumps(
            {
                "years": list(years),
                "batch": batch,
                "monitoring_total_eecu_hours": snapshot.total_seconds / 3600,
                "batch_delta_eecu_seconds": entry["monitoring_delta_eecu_seconds"],
            }
        ),
        flush=True,
    )
    if failure:
        raise UsagePaused(failure)
    for selected, (_, _, report) in results.items():
        cost, shared = _year_cost(current, selected)
        report.update(
            reported_eecu_seconds=cost,
            upper_eecu_seconds=cost,
            project_total_eecu_hours=current["workstream_eecu_hours"],
            year_cost_scope=_cost_scope(shared),
            monitoring_group_years=list(years),
            monitoring_group_runtime_seconds=time.monotonic() - started,
            runtime_scope="Year processing; two-year Monitoring settlement is shared",
        )
    cache[year + 1] = results[year + 1]
    return results[year]
