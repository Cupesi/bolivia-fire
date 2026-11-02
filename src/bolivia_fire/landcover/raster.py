from __future__ import annotations

import hashlib
import io
import json
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from shapely.geometry import box, mapping

from bolivia_fire.landcover.core import FEATURES, GROUPS
from bolivia_fire.landcover.earthengine import ASSET, EarthEngineSession
from bolivia_fire.landcover.monitoring import wait_for_settlement
from bolivia_fire.landcover.usage import UsagePaused, parse_profile, redact_project

INVALID_BANDS = ("invalid_0", "invalid_27", "invalid_other")
BANDS = (*GROUPS, *INVALID_BANDS)
AGREEMENT_TOLERANCE = 0.01


@dataclass(frozen=True)
class RasterGrid:
    west: float
    north: float
    width: int
    height: int
    resolution: float = 0.05

    @classmethod
    def from_cells(cls, cells: pd.DataFrame) -> RasterGrid:
        if cells.empty or cells.cell_id.duplicated().any():
            raise ValueError("Empty or duplicate project cells")
        for field, origin in (("west", -180), ("east", -180), ("south", -90), ("north", -90)):
            scaled = (cells[field].to_numpy() - origin) / 0.05
            if not np.allclose(scaled, np.rint(scaled), rtol=0, atol=1e-7):
                raise ValueError("Cell edges do not align with the registered grid")
        if not (
            np.allclose(cells.east - cells.west, 0.05, rtol=0, atol=1e-9)
            and np.allclose(cells.north - cells.south, 0.05, rtol=0, atol=1e-9)
        ):
            raise ValueError("Cell size does not match the registered grid")
        return cls(
            round(float(cells.west.min()), 9),
            round(float(cells.north.max()), 9),
            round((cells.east.max() - cells.west.min()) / 0.05),
            round((cells.north.max() - cells.south.min()) / 0.05),
        )

    @property
    def transform(self) -> list[float]:
        return [self.resolution, 0, self.west, 0, -self.resolution, self.north]

    def request_grid(self) -> dict[str, Any]:
        return {
            "dimensions": {"width": self.width, "height": self.height},
            "affineTransform": {
                "scaleX": self.resolution,
                "shearX": 0,
                "translateX": self.west,
                "shearY": 0,
                "scaleY": -self.resolution,
                "translateY": self.north,
            },
            "crsCode": "EPSG:4326",
        }

    def indices(self, cells: pd.DataFrame) -> tuple[list[int], list[int]]:
        rows = np.rint((self.north - cells.north.to_numpy()) / self.resolution).astype(int)
        cols = np.rint((cells.west.to_numpy() - self.west) / self.resolution).astype(int)
        if np.any((rows < 0) | (rows >= self.height) | (cols < 0) | (cols >= self.width)):
            raise ValueError("Cell outside raster grid")
        return rows.tolist(), cols.tolist()

    def tiles(
        self, maximum_edge: int = 16, cells: pd.DataFrame | None = None
    ) -> list[tuple[int, int, RasterGrid]]:
        if maximum_edge < 1 or maximum_edge > 16:
            raise ValueError("Raster tile edge must be between 1 and 16")
        occupied = np.ones((self.height, self.width), dtype=bool)
        if cells is not None:
            occupied[:] = False
            rows, cols = self.indices(cells)
            occupied[rows, cols] = True
        return [
            (
                row,
                col,
                RasterGrid(
                    round(self.west + col * self.resolution, 9),
                    round(self.north - row * self.resolution, 9),
                    min(maximum_edge, self.width - col),
                    min(maximum_edge, self.height - row),
                ),
            )
            for row in range(0, self.height, maximum_edge)
            for col in range(0, self.width, maximum_edge)
            if occupied[row : row + maximum_edge, col : col + maximum_edge].any()
        ]


def tile_country_geometry(country: Any, tile: RasterGrid, native_width: float) -> Any:
    halo = abs(native_width)
    extent = box(
        tile.west - halo,
        tile.north - tile.height * tile.resolution - halo,
        tile.west + tile.width * tile.resolution + halo,
        tile.north + halo,
    )
    return country.intersection(extent)


def projection_hours(seconds: float, years: int = 14, safety_factor: float = 1.5) -> float:
    if not math.isfinite(seconds) or seconds <= 0 or years < 1 or safety_factor < 1.5:
        raise ValueError("Invalid raster cost projection")
    return seconds * years * safety_factor / 3600


def reduction_transforms(grid: RasterGrid) -> list[list[float]]:
    return [
        [resolution, 0, grid.west, 0, -resolution, grid.north]
        for resolution in (
            grid.resolution / 64,
            grid.resolution / 16,
            grid.resolution / 4,
            grid.resolution,
        )
    ]


def normalize_pixels(
    pixels: np.ndarray, cells: pd.DataFrame, grid: RasterGrid, year: int
) -> pd.DataFrame:
    if pixels.shape != (grid.height, grid.width) or set(pixels.dtype.names or ()) != set(BANDS):
        raise ValueError("Raster response shape or bands differ from the request")
    row_indices, col_indices = grid.indices(cells)
    output = []
    for cell, row, col in zip(cells.itertuples(index=False), row_indices, col_indices, strict=True):
        values = np.array([pixels[name][row, col] for name in BANDS], dtype=float)
        present = bool(np.isfinite(values).all() and (values >= 0).all() and values.sum() > 0)
        national_area = float(cell.national_area_km2) * 1_000_000
        if not math.isfinite(national_area) or national_area <= 0:
            raise ValueError("National cell area must be positive and finite")
        total = math.fsum(values) if present else 0
        mapped = math.fsum(values[: len(GROUPS)]) if present else 0
        shares = values / total if present else np.full(len(BANDS), np.nan)
        coverage = mapped / total if present else None
        available = present and coverage >= 0.95
        fractions = {
            feature: float(values[index] / mapped) if available else None
            for index, feature in enumerate(FEATURES)
        }
        if available and abs(math.fsum(fractions.values()) - 1) > 1e-6:
            raise ValueError("Raster fractions do not sum to one")
        unmapped = {
            name.removeprefix("invalid_"): float(shares[index] * national_area)
            for index, name in enumerate(BANDS)
            if name in INVALID_BANDS and present and shares[index] > 0
        }
        output.append(
            {
                "cell_id": cell.cell_id,
                "year": year,
                "map_year": year,
                "response_feature_present": present,
                "available": available,
                "mapped_area_fraction": coverage,
                "mapped_area_m2": national_area * coverage if present else None,
                "national_support_area_m2": national_area,
                "unmapped_class_area_m2": json.dumps(unmapped, sort_keys=True),
                **fractions,
            }
        )
    return pd.DataFrame(output)


def compare_polygon_pilot(raster: pd.DataFrame, polygon: pd.DataFrame) -> dict[str, Any]:
    if polygon.cell_id.duplicated().any() or raster.cell_id.duplicated().any():
        raise ValueError("Duplicate validation cells")
    common = polygon.merge(raster, on=["cell_id", "year"], suffixes=("_polygon", "_raster"))
    if len(common) != len(polygon):
        raise ValueError("Raster validation omits polygon-pilot cells")
    available = common.available_polygon & common.available_raster
    errors = np.array(
        [
            abs(
                common.loc[available, feature + "_polygon"]
                - common.loc[available, feature + "_raster"]
            )
            for feature in FEATURES
        ]
    ).T
    coverage_errors = abs(common.mapped_area_fraction_polygon - common.mapped_area_fraction_raster)
    differences = common.available_polygon != common.available_raster
    failed_fraction = errors.max(axis=1) > AGREEMENT_TOLERANCE if len(errors) else np.array([])
    passed = (
        len(errors) > 0
        and not failed_fraction.any()
        and not differences.any()
        and coverage_errors.notna().all()
        and coverage_errors.max() <= AGREEMENT_TOLERANCE
    )
    return {
        "absolute_tolerance": AGREEMENT_TOLERANCE,
        "cells": len(common),
        "jointly_available": int(available.sum()),
        "availability_disagreement_cells": common.loc[differences, "cell_id"].tolist(),
        "fraction_failure_cells": common.loc[available, "cell_id"][failed_fraction].tolist(),
        "fraction_max_absolute_error": float(errors.max()) if len(errors) else None,
        "fraction_mean_absolute_error": float(errors.mean()) if len(errors) else None,
        "fraction_p99_absolute_error": float(np.quantile(errors, 0.99)) if len(errors) else None,
        "coverage_max_absolute_error": float(coverage_errors.max()),
        "coverage_failure_cells": common.loc[
            coverage_errors.isna() | (coverage_errors > AGREEMENT_TOLERANCE), "cell_id"
        ].tolist(),
        "passed": bool(passed),
    }


def _checkpoint_record(checkpoint: Path, root: Path, response: np.ndarray) -> dict[str, Any]:
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(checkpoint, pixels=response)
    return {
        "output": checkpoint.relative_to(root).as_posix(),
        "output_bytes": checkpoint.stat().st_size,
        "output_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        "response_array_bytes": response.nbytes,
    }


def _restore_tile(
    checkpoint: Path,
    record: dict[str, Any] | None,
    tile: RasterGrid,
    year: int,
    geometry_sha256: str,
    *,
    reconciled_batches: set[str] | None = None,
) -> np.ndarray:
    reconciled = bool(reconciled_batches) and (
        record is not None
        and (
            record.get("batch") in reconciled_batches
            or record.get("monitoring_batch") in reconciled_batches
        )
    )
    if (
        record is None
        or (record.get("status") != "downloaded" and not reconciled)
        or record.get("year") != year
        or record.get("grid") != tile.request_grid()
        or record.get("national_geometry_sha256") != geometry_sha256
        or record.get("output_bytes") != checkpoint.stat().st_size
        or record.get("output_sha256") != hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    ):
        raise UsagePaused("Raster tile checkpoint provenance or checksum differs")
    if record.get("reported_eecu_seconds") is not None or not reconciled:
        cost = parse_profile(record.get("profile", ""))
        if cost.reported_eecu_seconds != record.get(
            "reported_eecu_seconds"
        ) or cost.upper_eecu_seconds != record.get("upper_eecu_seconds"):
            raise UsagePaused("Raster tile checkpoint profile differs")
    with np.load(checkpoint, allow_pickle=False) as archive:
        response = archive["pixels"]
    if response.shape != (tile.height, tile.width) or set(response.dtype.names or ()) != set(BANDS):
        raise UsagePaused("Raster tile checkpoint shape or bands differ")
    return response


def raster_year(
    session: EarthEngineSession,
    cells: pd.DataFrame,
    country: Any,
    year: int,
    reservation_eecu_hours: float,
) -> tuple[pd.DataFrame, np.ndarray, dict[str, Any]]:
    session.resources.check()
    if year > 2012 and session.ledger.monitoring_batch_limit() in (180, 360):
        from bolivia_fire.landcover.parallel import parallel_raster_year

        return parallel_raster_year(session, cells, country, year)
    batch = f"raster-national-{year}-binary-cropped-v8"
    ee = session.ee
    grid = RasterGrid.from_cells(cells)
    geometry = mapping(country)
    classification = ee.Image(ASSET).select(f"classification_{year}").unmask(0, False)
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
    # Zero-fill outside national support so all class bands have the same weights.
    binary = ee.Image.cat(masks).toFloat()
    started = time.monotonic()
    pixels = np.full((grid.height, grid.width), np.nan, dtype=[(name, "f8") for name in BANDS])
    tiles = grid.tiles(cells=cells)
    requests = []
    manifest = session.paths.manifests / "mapbiomas_raster_requests.jsonl"
    checkpoints = {}
    recorded_requests = set()
    if manifest.exists():
        for line in manifest.read_text("utf-8").splitlines():
            record = json.loads(line)
            recorded_requests.add(record["batch"])
            if "output" in record:
                checkpoints[record["output"]] = record
    existing_batches = {item["batch"] for item in session.ledger._load().get("batches", [])}
    existing_batches.update(recorded_requests)
    state = session.ledger._load()
    monitoring = state.get("accounting_primary") == "cloud_monitoring"
    reconciled_batches = (
        {
            item["batch"]
            for item in state.get("batches", [])
            if item["status"] in ("aggregate_reconciled", "monitoring_reconciled", "measured")
        }
        if monitoring
        else None
    )
    year_before = state["project_month_eecu_hours"] * 3600
    remaining_tiles = sum(
        not (
            session.paths.datasets
            / "processed/landcover"
            / f"raster_tiles_{year}"
            / "cropped"
            / f"row_{row}_col_{col}.npz"
        ).exists()
        for row, col, _ in tiles
    )
    monitoring_batch = None
    maximum_batch_tiles = session.ledger.monitoring_batch_limit() if monitoring else 1
    group_left = 0
    group_baseline = 0.0
    group_attempted = 0
    group_completed = 0

    def settle_group() -> None:
        snapshot = wait_for_settlement(
            session.monitoring.read,
            baseline_seconds=group_baseline,
            completed_at=datetime.now(UTC),
        )
        session.ledger.reconcile_monitoring(
            monitoring_batch,
            snapshot.total_seconds,
            snapshot.observed_at.isoformat(),
            submitted_tile_count=group_attempted,
            completed_tile_count=group_completed,
        )
        entry = next(
            item for item in session.ledger._load()["batches"] if item["batch"] == monitoring_batch
        )
        entry.update(
            year=year,
            latest_point_end_utc=snapshot.latest_point_end.isoformat(),
            monitoring_points=snapshot.points,
            monitoring_series=snapshot.series,
            metric="earthengine.googleapis.com/project/cpu/in_progress_usage_time",
        )
        with (session.paths.manifests / "mapbiomas_monitoring_batches.jsonl").open(
            "a", encoding="utf-8"
        ) as stream:
            stream.write(json.dumps(entry, allow_nan=False) + "\n")
        print(
            json.dumps(
                {
                    "year": year,
                    "batch": monitoring_batch,
                    "monitoring_total_eecu_hours": snapshot.total_seconds / 3600,
                    "batch_delta_eecu_seconds": entry["monitoring_delta_eecu_seconds"],
                }
            ),
            flush=True,
        )

    reused = 0
    for index, (row, col, tile) in enumerate(tiles):
        session.resources.check()
        tile_batch = f"{batch}-tile-{index}"
        tile_geometry = mapping(tile_country_geometry(country, tile, session.transform[0]))
        geometry_sha256 = hashlib.sha256(
            json.dumps(tile_geometry, sort_keys=True).encode()
        ).hexdigest()
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
                checkpoint,
                record,
                tile,
                year,
                geometry_sha256,
                reconciled_batches=reconciled_batches,
            )
            pixels[row : row + tile.height, col : col + tile.width] = response
            requests.append(record)
            reused += 1
            continue
        if relative in checkpoints:
            raise UsagePaused("Raster tile checkpoint recorded but unavailable")
        attempt = 1
        original_batch = tile_batch
        while tile_batch in existing_batches:
            attempt += 1
            tile_batch = f"{original_batch}-attempt-{attempt}"
        image = (
            binary.clip(ee.Geometry(tile_geometry, proj="EPSG:4326", geodesic=False))
            .unmask(0, False)
            .reduceResolution(reducer=ee.Reducer.mean(), bestEffort=False, maxPixels=65536)
            .reproject(crs="EPSG:4326", crsTransform=grid.transform)
            .toFloat()
        )
        if monitoring:
            if monitoring_batch is None:
                group_left = min(maximum_batch_tiles, remaining_tiles)
                group_attempted = group_completed = 0
                monitoring_batch = f"{batch}-monitoring-{index}"
                base_monitoring_batch = monitoring_batch
                monitoring_attempt = 1
                while monitoring_batch in existing_batches:
                    monitoring_attempt += 1
                    monitoring_batch = base_monitoring_batch + f"-attempt-{monitoring_attempt}"
                session.ledger.reserve_monitoring(
                    monitoring_batch, group_left, remaining_tiles + (2025 - year) * len(tiles)
                )
                existing_batches.add(monitoring_batch)
                group_baseline = session.ledger._load()["project_month_eecu_hours"] * 3600
        else:
            session.ledger.reserve(tile_batch, max(reservation_eecu_hours / len(tiles), 0.1))
        profile = io.StringIO()
        error = None
        response = None
        tile_started = time.monotonic()
        try:
            with ee.profilePrinting(destination=profile):
                group_attempted += 1
                response = ee.data.computePixels(
                    {
                        "expression": image,
                        "fileFormat": "NUMPY_NDARRAY",
                        "grid": tile.request_grid(),
                        "bandIds": list(BANDS),
                        "workloadTag": f"bolivia-fire-g1-raster-{year}",
                    }
                )
        except Exception as exc:
            error = exc
        profile_text = redact_project(profile.getvalue(), session.project)
        checkpoint_fields = {}
        if response is not None:
            if response.shape != (tile.height, tile.width) or set(
                response.dtype.names or ()
            ) != set(BANDS):
                error = ValueError("Unexpected tile response shape or bands")
            else:
                checkpoint_fields = _checkpoint_record(checkpoint, session.paths.root, response)
                group_completed += 1
        try:
            cost = parse_profile(profile_text)
        except UsagePaused:
            cost = None if monitoring else False
        if cost is False:
            record = {
                "batch": tile_batch,
                "year": year,
                "grid": tile.request_grid(),
                "status": "usage_unknown",
                "profile": profile_text,
                "error": redact_project(str(error), session.project) if error else None,
                "reported_eecu_seconds": None,
                "upper_eecu_seconds": None,
                "runtime_seconds": time.monotonic() - tile_started,
                "national_geometry_sha256": geometry_sha256,
                **checkpoint_fields,
            }
            with (session.paths.manifests / "mapbiomas_raster_requests.jsonl").open(
                "a", encoding="utf-8"
            ) as stream:
                stream.write(json.dumps(record, allow_nan=False) + "\n")
            session.ledger.reconcile(
                tile_batch, reported_eecu_seconds=None, upper_eecu_seconds=None
            )
            raise UsagePaused("Request EECU profile unavailable")
        profile_error = redact_project(str(error), session.project) if error else None
        if monitoring and checkpoint_fields:
            # profilePrinting may fail after computePixels already returned a valid array.
            error = None
        tile_report = {
            "batch": tile_batch,
            "year": year,
            "grid": tile.request_grid(),
            "national_geometry_sha256": geometry_sha256,
            "national_geometry_scope": "tile ADM0 intersection plus one native-pixel halo",
            "profile": profile_text,
            "reported_eecu_seconds": cost.reported_eecu_seconds if cost else None,
            "upper_eecu_seconds": cost.upper_eecu_seconds if cost else None,
            "runtime_seconds": time.monotonic() - tile_started,
            "status": "failed" if error else "downloaded",
            "error": redact_project(str(error), session.project) if error else None,
            **checkpoint_fields,
        }
        if monitoring:
            tile_report.update(
                monitoring_batch=monitoring_batch,
                profile_error=profile_error,
                status="awaiting_monitoring" if checkpoint_fields else "failed",
                accounting_primary="cloud_monitoring",
            )
        with (session.paths.manifests / "mapbiomas_raster_requests.jsonl").open(
            "a", encoding="utf-8"
        ) as stream:
            stream.write(json.dumps(tile_report, allow_nan=False) + "\n")
        requests.append(tile_report)
        if monitoring:
            group_left -= 1
            remaining_tiles -= 1
            if group_left == 0 or error:
                settle_group()
                monitoring_batch = None
        else:
            session.ledger.reconcile(
                tile_batch,
                reported_eecu_seconds=cost.reported_eecu_seconds,
                upper_eecu_seconds=cost.upper_eecu_seconds,
            )
        if error:
            raise RuntimeError(redact_project(str(error), session.project)) from None
        if response.shape != (tile.height, tile.width) or set(response.dtype.names or ()) != set(
            BANDS
        ):
            raise ValueError("Unexpected tile response shape or bands")
        pixels[row : row + tile.height, col : col + tile.width] = response
    session.resources.check()
    frame = normalize_pixels(pixels, cells, grid, year)
    report = {
        "batch": batch,
        "asset": ASSET,
        "year": year,
        "classification_band": f"classification_{year}",
        "crs": "EPSG:4326",
        "crs_transform": grid.transform,
        "grid": grid.request_grid(),
        "native_crs_transform": session.transform,
        "national_geometry_sha256": hashlib.sha256(
            json.dumps(geometry, sort_keys=True).encode()
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
        "completed_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "reported_eecu_seconds": sum(item["reported_eecu_seconds"] or 0 for item in requests),
        "upper_eecu_seconds": sum(item["upper_eecu_seconds"] or 0 for item in requests),
        "requests": requests,
        "compute_pixels_requests": len(requests) - reused,
        "reused_tiles": reused,
        "completed_tiles": len(requests),
        "skipped_no_project_cell_tiles": len(grid.tiles()) - len(tiles),
        "response_array_bytes": pixels.nbytes,
        "runtime_seconds": time.monotonic() - started,
        "cells": len(cells),
        "available_cells": int(frame.available.sum()),
        "missing_response_cells": int((~frame.response_feature_present).sum()),
        "fraction_sum_max_absolute_error": float(
            abs(frame.loc[frame.available, list(FEATURES)].sum(axis=1) - 1).max()
        ),
        "peak_process_tree_rss_bytes": session.resources.peak_rss_bytes,
        "minimum_free_disk_bytes": session.resources.minimum_free_bytes,
    }
    if monitoring:
        current = session.ledger._load()["project_month_eecu_hours"] * 3600
        measured = current if year == 2012 else current - year_before
        report.update(
            accounting_primary="cloud_monitoring",
            reported_eecu_seconds=measured,
            upper_eecu_seconds=measured,
            project_total_eecu_hours=current / 3600,
            year_cost_scope=(
                "All project usage through national pilot, including earlier pilots and failures"
                if year == 2012
                else "Project Monitoring year delta"
            ),
            secondary_profile_tiles=sum(
                item["reported_eecu_seconds"] is not None for item in requests
            ),
        )
    return frame, pixels, report
