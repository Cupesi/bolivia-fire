from __future__ import annotations

import contextlib
import hashlib
import io
import json
import logging
import os
import shlex
import shutil
import threading
import time
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from shapely.geometry import box, mapping

from bolivia_fire.config import Paths, load_protocol
from bolivia_fire.grid import load_boundary_geometries
from bolivia_fire.landcover.core import summarize_areas, validate_groups
from bolivia_fire.landcover.usage import UsageLedger, UsagePaused, parse_profile, redact_project

ASSET = (
    "projects/mapbiomas-public/assets/bolivia/lulc/collection4/"
    "mapbiomas_bolivia_collection4_coverage_v1"
)


def normalize_features(
    features: Sequence[dict[str, Any]],
    requested_ids: Sequence[str],
    year: int,
) -> pd.DataFrame:
    requested = set(requested_ids)
    if len(requested) != len(requested_ids):
        raise ValueError("Duplicate requested cell")
    properties: dict[str, dict[str, Any]] = {}
    for feature in features:
        value = feature["properties"]
        cell = value["cell_id"]
        if cell not in requested:
            raise ValueError("Unrequested response cell")
        if cell in properties:
            raise ValueError("Duplicate response cell")
        properties[cell] = value
    rows = []
    for cell in requested_ids:
        areas: dict[int, float] = defaultdict(float)
        for group in properties.get(cell, {}).get("groups", []):
            areas[int(group["class_code"])] += float(group["sum"])
        summary = summarize_areas(areas)
        summary["unmapped_class_area_m2"] = json.dumps(
            summary["unmapped_class_area_m2"], sort_keys=True
        )
        rows.append(
            {
                "cell_id": cell,
                "year": year,
                "map_year": year,
                "response_feature_present": cell in properties,
                **summary,
            }
        )
    return pd.DataFrame(rows)


def load_project(root: Path) -> str:
    project = os.environ.get("EE_PROJECT_ID", "").strip()
    if not project:
        try:
            with (root / ".env").open(encoding="utf-8-sig") as stream:
                for line in stream:
                    key, separator, raw = line.strip().removeprefix("export ").partition("=")
                    if separator and key.strip() == "EE_PROJECT_ID":
                        tokens = shlex.split(raw, comments=True, posix=True)
                        project = tokens[0].strip() if len(tokens) == 1 else ""
                        break
        except FileNotFoundError:
            pass
    if not project or any(character.isspace() for character in project) or "/" in project:
        raise ValueError("Configured Earth Engine project is unavailable or malformed")
    return project


def load_cells(paths: Paths) -> tuple[pd.DataFrame, Any]:
    import yaml

    contract = yaml.safe_load((paths.root / "configs/extension_vegetation.yaml").read_text("utf-8"))
    validate_groups(contract["land_cover"]["classes"])
    protocol = load_protocol(paths.root)
    if protocol["grid"]["resolution_deg"] != 0.05:
        raise ValueError("Unsupported project grid")
    cells = pd.read_parquet(
        paths.datasets / "processed/cells.parquet",
        columns=[
            "cell_id",
            "west",
            "south",
            "east",
            "north",
            "national_area_km2",
        ],
    )
    country, _ = load_boundary_geometries(paths)
    return cells, country


class ResourceMonitor:
    def __init__(self, root: Path) -> None:
        import psutil

        self.root = root
        self.process = psutil.Process()
        self.peak_rss_bytes = 0
        self.minimum_free_bytes = shutil.disk_usage(root).free
        self.error: str | None = None
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._sample_loop, daemon=True)

    def _sample(self) -> None:
        try:
            processes = [self.process, *self.process.children(recursive=True)]
            rss = sum(process.memory_info().rss for process in processes)
            self.peak_rss_bytes = max(self.peak_rss_bytes, rss)
            self.minimum_free_bytes = min(
                self.minimum_free_bytes, shutil.disk_usage(self.root).free
            )
            if rss >= 12 * 1024**3:
                self.error = "Process-tree RAM reached the 12-GiB limit"
        except Exception:
            self.error = "Process-tree resource monitoring failed"

    def _sample_loop(self) -> None:
        while not self.stopping.wait(1):
            self._sample()

    def __enter__(self) -> ResourceMonitor:
        self._sample()
        self.thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.stopping.set()
        self.thread.join()
        self._sample()

    def check(self) -> None:
        self._sample()
        if self.error:
            raise UsagePaused(self.error)
        if shutil.disk_usage(self.root).free - 128 * 1024**2 < 55 * 1024**3:
            raise UsagePaused("Projected free disk is below the 55-GiB floor")
        directories = [
            self.root / "datasets" / category / name
            for category in ("raw", "processed")
            for name in ("vegetation", "landcover")
        ]
        stored = sum(
            path.stat().st_size
            for directory in directories
            if directory.exists()
            for path in directory.rglob("*")
            if path.is_file()
        )
        if stored + 128 * 1024**2 >= 30 * 1024**3:
            raise UsagePaused("Projected extension storage exceeds the 30-GiB budget")


class EarthEngineSession:
    def __init__(self, paths: Paths, ledger: UsageLedger, resources: ResourceMonitor) -> None:
        import ee
        import httplib2

        self.paths, self.ledger, self.resources = paths, ledger, resources
        self.project = load_project(paths.root)
        logging.getLogger("googleapiclient").setLevel(logging.CRITICAL)
        logging.getLogger("google.auth").setLevel(logging.CRITICAL)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            ee.Initialize(
                ee.data.get_persistent_credentials(),
                project=self.project,
                http_transport=httplib2.Http(timeout=300),
            )
            ee.data.setDeadline(300000)
        if ee.data.getProjectConfig().get("registrationState") != "REGISTERED_NOT_COMMERCIALLY":
            raise UsagePaused("Configured project is not registered noncommercially")
        self.ee = ee
        self.metadata = ee.data.getAsset(ASSET)
        bands = self.metadata["bands"]
        if not all(
            f"classification_{year}" in {band["id"] for band in bands} for year in range(2012, 2026)
        ):
            raise UsagePaused("Required Collection 4 years are unavailable")
        grid = bands[0]["grid"]
        affine = grid["affineTransform"]
        self.crs = grid["crsCode"]
        self.transform = [
            affine["scaleX"],
            affine.get("shearX", 0),
            affine["translateX"],
            affine.get("shearY", 0),
            affine["scaleY"],
            affine["translateY"],
        ]

    def batch(
        self,
        cells: pd.DataFrame,
        country: Any,
        year: int,
        batch: str,
        reservation_eecu_hours: float,
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        self.resources.check()
        self.ledger.reserve(batch, reservation_eecu_hours)
        ee = self.ee
        started = time.monotonic()
        profile = io.StringIO()
        features: list[dict[str, Any]] = []
        parameters: dict[str, Any] = {
            "asset": ASSET,
            "year": year,
            "crs": self.crs,
            "crs_transform": self.transform,
            "cell_ids": cells.cell_id.tolist(),
            "geometry": "cell intersection with pinned ADM0",
            "reducer": "pixelArea sum grouped by native classification code; weighted",
            "invalid_fill_code": 0,
            "direct_download": "computeFeatures",
            "batch": batch,
        }
        error: Exception | None = None
        calls = 0
        response_bytes = 0
        try:
            with ee.profilePrinting(destination=profile):
                collection = []
                geometries = []
                for row in cells.itertuples(index=False):
                    geometry = mapping(
                        box(row.west, row.south, row.east, row.north).intersection(country)
                    )
                    geometries.append(geometry)
                    collection.append(
                        ee.Feature(
                            ee.Geometry(geometry, proj="EPSG:4326", geodesic=False),
                            {"cell_id": row.cell_id},
                        )
                    )
                parameters["geometry_sha256"] = hashlib.sha256(
                    json.dumps(geometries, sort_keys=True).encode()
                ).hexdigest()
                classification = ee.Image(ASSET).select(f"classification_{year}").unmask(0, False)
                image = ee.Image.pixelArea().rename("area_m2").addBands(classification)
                result = image.reduceRegions(
                    collection=ee.FeatureCollection(collection),
                    reducer=ee.Reducer.sum().group(groupField=1, groupName="class_code"),
                    crs=self.crs,
                    crsTransform=self.transform,
                    maxPixelsPerRegion=100000,
                ).map(lambda feature: ee.Feature(None, feature.toDictionary()))
                request = {"expression": result, "pageSize": len(cells)}
                while True:
                    self.resources.check()
                    with ee.data.workloadTagContext("bolivia-fire-g1-" + str(year)):
                        response = ee.data.computeFeatures(request)
                    calls += 1
                    response_bytes += len(json.dumps(response).encode())
                    features.extend(response.get("features", []))
                    if not response.get("nextPageToken"):
                        break
                    request["pageToken"] = response["nextPageToken"]
        except Exception as exc:
            error = exc
        profile_text = redact_project(profile.getvalue(), self.project)
        try:
            cost = parse_profile(profile_text)
        except UsagePaused:
            self.ledger.reconcile(batch, reported_eecu_seconds=None, upper_eecu_seconds=None)
            raise
        self.ledger.reconcile(
            batch,
            reported_eecu_seconds=cost.reported_eecu_seconds,
            upper_eecu_seconds=cost.upper_eecu_seconds,
        )
        if error:
            raise RuntimeError(redact_project(str(error), self.project)) from None
        frame = normalize_features(features, cells.cell_id.tolist(), year)
        report = {
            **parameters,
            "completed_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "reported_eecu_seconds": cost.reported_eecu_seconds,
            "upper_eecu_seconds": cost.upper_eecu_seconds,
            "profile": profile_text,
            "compute_features_requests": calls,
            "response_json_bytes": response_bytes,
            "runtime_seconds": time.monotonic() - started,
            "cells": len(cells),
            "available_cells": int(frame.available.sum()),
            "missing_response_cells": int((~frame.response_feature_present).sum()),
            "peak_process_tree_rss_bytes": self.resources.peak_rss_bytes,
            "minimum_free_disk_bytes": self.resources.minimum_free_bytes,
        }
        return frame, report
