from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pyarrow.parquet as pq
import yaml
from fastapi import HTTPException
from shapely.geometry import mapping, shape

IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
SPLITS = ("train", "val_select", "val_calib", "test")
BOUNDARY_TOLERANCE_DEG = 0.005
GRID_COLUMNS = (
    "cell_id",
    "row",
    "col",
    "department",
    "national_area_km2",
    "santa_cruz",
    "geo_holdout",
)
LIMITATIONS = [
    "Satellite detections are not counts of fires, ignitions, or burned area.",
    "Zeros are not proof of absence: clouds, smoke, overpass timing, "
    "and small fires reduce detection.",
    "Retrospective estimates are not operational forecasts.",
    "Weeks with unobserved target days have missing labels, "
    "never zero-filled labels.",
    "Strict estimates assume standard-processing detections approximate near-real-time "
    "availability; "
    "this equivalence has not been validated.",
    "CHIRPS v3 historical vintages were unavailable during much of the study period.",
]
ATTRIBUTIONS = [
    {
        "name": "NASA FIRMS",
        "product": "VIIRS S-NPP standard processing",
        "url": "https://firms.modaps.eosdis.nasa.gov/",
    },
    {"name": "geoBoundaries", "license": "CC BY 4.0", "url": "https://www.geoboundaries.org/"},
    {
        "name": "CHIRPS",
        "provider": "Climate Hazards Center",
        "url": "https://www.chc.ucsb.edu/data/chirps",
    },
]


def missing(message: str) -> HTTPException:
    return HTTPException(404, message)


def invalid(message: str) -> HTTPException:
    return HTTPException(422, message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(data: Any) -> bytes:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def query_rows(files: list[Path], query: str) -> list[tuple[Any, ...]]:
    with duckdb.connect(config={"memory_limit": "256MB", "threads": "2"}) as connection:
        return connection.execute(query, [[str(path) for path in files]]).fetchall()


class Catalog:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        protocol_path = self.root / "configs/protocol.yaml"
        protocol_bytes = protocol_path.read_bytes()
        self.protocol = yaml.safe_load(protocol_bytes)
        self.protocol_hash = digest(protocol_bytes)
        sources_path = self.root / "datasets/sources.yaml"
        self.sources = yaml.safe_load(sources_path.read_bytes()) if sources_path.exists() else {}
        self.snapshots: dict[str, Any] = {"protocol_sha256": self.protocol_hash, "panel": {}}
        if sources_path.exists():
            self.snapshots["sources_sha256"] = digest(sources_path.read_bytes())
        self.cells_path = self.root / "datasets/processed/cells.parquet"
        self.cell_ids: list[str] = []
        self.cell_index: dict[str, int] = {}
        self.grid: dict[str, Any] | None = None
        if self.cells_path.exists():
            self._load_grid()
        self.panel_files = sorted(
            (self.root / "datasets/processed/panel").glob("year=*/part.parquet")
        )
        self.panel_by_year: dict[int, list[Path]] = {}
        for path in self.panel_files:
            year = int(path.parent.name.removeprefix("year="))
            self.panel_by_year.setdefault(year, []).append(path)
            self.snapshots["panel"][str(year)] = self._metadata(path)
        self.weeks = self._load_weeks()
        self.issue_dates = {row["issue_date"] for row in self.weeks}
        self.first = date.fromisoformat(self.weeks[0]["issue_date"]) if self.weeks else None
        self.last = date.fromisoformat(self.weeks[-1]["issue_date"]) if self.weeks else None
        self.predictions: dict[str, list[Path]] = {}
        self.models: dict[str, list[str]] = {}
        prediction_dir = self.root / "artifacts/predictions"
        for folder in sorted(prediction_dir.iterdir()) if prediction_dir.exists() else []:
            if not folder.is_dir() or not IDENTIFIER.fullmatch(folder.name):
                continue
            files = [
                folder / f"{split}.parquet"
                for split in SPLITS
                if (folder / f"{split}.parquet").is_file()
            ]
            if files:
                self.predictions[folder.name] = files
                self.models[folder.name] = [
                    row[0]
                    for row in query_rows(
                        files, "SELECT DISTINCT model FROM read_parquet(?) ORDER BY model"
                    )
                ]
        self.boundaries = self._load_boundaries()
        self.evaluation = self._load_evaluation()

    @staticmethod
    def _metadata(path: Path) -> dict[str, str]:
        return {
            key.decode(): value.decode()
            for key, value in (pq.ParquetFile(path).schema_arrow.metadata or {}).items()
            if key.endswith(b"sha256")
        }

    def _load_grid(self) -> None:
        table = (
            pq.ParquetFile(self.cells_path)
            .read(columns=list(GRID_COLUMNS))
            .sort_by([("cell_id", "ascending")])
        )
        columns = table.to_pydict()
        self.cell_ids = columns["cell_id"]
        self.cell_index = {cell: index for index, cell in enumerate(self.cell_ids)}
        if len(self.cell_index) != len(self.cell_ids):
            raise ValueError("Duplicate grid cells")
        self.grid = {
            "origin": self.protocol["grid"]["origin"],
            "resolution_deg": self.protocol["grid"]["resolution_deg"],
            "crs": self.protocol["grid"]["crs"],
            "row_range": [min(columns["row"]), max(columns["row"])],
            "col_range": [min(columns["col"]), max(columns["col"])],
            **columns,
        }
        self.snapshots["cells"] = self._metadata(self.cells_path)
        self.snapshots["cells_sha256"] = digest(self.cells_path.read_bytes())

    def _load_weeks(self) -> list[dict[str, Any]]:
        if not self.panel_files:
            return []
        rows = query_rows(
            self.panel_files,
            """
            SELECT CAST(issue_date AS DATE), AVG(CASE WHEN y_primary IS NULL THEN 0.0 ELSE 1.0 END),
                   BOOL_OR(target_low_coverage), BOOL_AND(target_complete)
            FROM read_parquet(?) GROUP BY issue_date ORDER BY issue_date
        """,
        )
        outages_path = self.root / "datasets/manifests/snpp_outages.csv"
        outages: list[tuple[datetime, datetime]] = []
        if outages_path.exists():
            with outages_path.open(encoding="utf-8", newline="") as stream:
                outages = [
                    (
                        datetime.fromisoformat(row["start_utc"]),
                        datetime.fromisoformat(row["end_utc"]),
                    )
                    for row in csv.DictReader(stream)
                ]
        result = []
        for issue, completeness, low_coverage, target_complete in rows:
            start = datetime.combine(issue, datetime.min.time(), tzinfo=UTC)
            end = start + timedelta(days=7)
            result.append(
                {
                    "issue_date": issue.isoformat(),
                    "label_complete_share": float(completeness),
                    "target_complete": bool(target_complete),
                    "outage": any(left < end and right > start for left, right in outages),
                    "target_low_coverage": bool(low_coverage),
                }
            )
        return result

    def _load_boundaries(self) -> dict[int, dict[str, Any]]:
        boundaries = {}
        raw = (self.root / "datasets/raw/boundaries").resolve()
        for level in (0, 1):
            pin = self.sources.get("boundaries", {}).get(f"ADM{level}")
            if not pin:
                continue
            path = (self.root / pin["path"]).resolve()
            if not path.is_relative_to(raw) or not path.is_file():
                continue
            content = path.read_bytes()
            if digest(content) != pin["sha256"]:
                raise ValueError("Boundary pin mismatch")
            data = json.loads(content)
            for feature in data["features"]:
                feature["geometry"] = mapping(
                    shape(feature["geometry"]).simplify(
                        BOUNDARY_TOLERANCE_DEG, preserve_topology=True
                    )
                )
            boundaries[level] = data
        return boundaries

    def _load_evaluation(self) -> dict[tuple[str, str], Any]:
        directory = self.root / "artifacts/evaluation"
        log_path = directory / "final_test_log.json"
        openings = json.loads(log_path.read_bytes()) if log_path.exists() else []
        opened = isinstance(openings, list) and any(
            isinstance(entry, dict)
            and entry.get("timestamp_utc")
            and entry.get("protocol_sha256") == self.protocol_hash
            for entry in openings
        )
        result = {}
        for path in sorted(directory.glob("*.json")):
            for split in SPLITS:
                prefix = f"{split}_"
                if path.stem.startswith(prefix) and (split != "test" or opened):
                    variant = path.stem.removeprefix(prefix)
                    if IDENTIFIER.fullmatch(variant):
                        result[(variant, split)] = json.loads(path.read_bytes())
                    break
        return result

    def validate_variant(self, variant: str) -> None:
        available = {"strict", "strict_geo", "retrospective", "retrospective_geo"}
        available.update(self.predictions)
        available.update(variant for variant, _ in self.evaluation)
        if variant not in available:
            raise invalid("Unknown variant; use a variant listed in metadata")

    def check_date(self, value: date, *, issue: bool = False) -> None:
        if self.first is None or self.last is None:
            raise HTTPException(503, "Prepared panel data is not available")
        if not self.first <= value <= self.last:
            raise invalid("Date is outside the prepared issue-date range")
        if issue and value.isoformat() not in self.issue_dates:
            raise invalid("Date is not an available weekly issue date")

    def date_range(self, start: date | None, end: date | None) -> tuple[date, date]:
        start, end = start or self.first, end or self.last
        if start is None or end is None:
            raise HTTPException(503, "Prepared panel data is not available")
        self.check_date(start)
        self.check_date(end)
        if start > end:
            raise invalid("Start must be on or before end")
        return start, end

    def select_model(self, variant: str, model: str | None, *, required: bool) -> str | None:
        self.validate_variant(variant)
        available = self.models.get(variant, [])
        if model is not None and model not in available:
            raise missing("Requested model predictions are not available")
        if not available:
            if required:
                raise missing("Prepared predictions are not available for this variant")
            return None
        return model or available[0]

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "data_ready": {
                "cells": self.grid is not None,
                "panel": bool(self.weeks),
                "predictions": bool(self.predictions),
                "evaluation": bool(self.evaluation),
            },
        }

    def meta(self) -> dict[str, Any]:
        variants = sorted(set(self.models) | {variant for variant, _ in self.evaluation})
        return {
            "project_name": "Bolivia Fire",
            "protocol": {
                "version": self.protocol["protocol_version"],
                "sha256": self.protocol_hash,
            },
            "dataset_snapshot_ids": self.snapshots,
            "issue_date_range": {
                "start": self.first.isoformat() if self.first else None,
                "end": self.last.isoformat() if self.last else None,
            },
            "available_layers": (["observed"] if self.weeks else [])
            + (["estimate"] if self.predictions else []),
            "models": self.models,
            "variants": variants,
            "evaluation_splits": {
                variant: sorted(split for run, split in self.evaluation if run == variant)
                for variant in variants
            },
            "source_attributions": ATTRIBUTIONS,
            "limitations": LIMITATIONS,
            "boundary_simplification_tolerance_deg": BOUNDARY_TOLERANCE_DEG,
        }
