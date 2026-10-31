from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import shapely
import yaml
from numpy.typing import ArrayLike, NDArray
from pyproj import Geod
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import orient

from bolivia_fire.config import Paths
from bolivia_fire.firms.storage import sha256_file

GEOD = Geod(ellps="WGS84")


def coordinate_indices(lon: ArrayLike, lat: ArrayLike) -> tuple[NDArray, NDArray]:
    longitude, latitude = np.asarray(lon, dtype=float), np.asarray(lat, dtype=float)
    if not (np.isfinite(longitude).all() and np.isfinite(latitude).all()):
        raise ValueError("Grid coordinates must be finite")
    # Integer nanodegrees make decimal snapping and exact cell edges reproducible.
    cols = ((np.rint(longitude * 1e9).astype(np.int64) + 180_000_000_000) // 50_000_000).astype(
        np.int32
    )
    rows = ((np.rint(latitude * 1e9).astype(np.int64) + 90_000_000_000) // 50_000_000).astype(
        np.int32
    )
    return rows, cols


def cell_id(lon: float, lat: float) -> str:
    row, col = coordinate_indices(lon, lat)
    return f"r{int(row):04d}c{int(col):04d}"


def holdout_blocks(blocks: Iterable[str], seed: int, fraction: float) -> set[str]:
    unique = sorted(set(blocks))
    ranked = sorted(
        unique, key=lambda block: (hashlib.sha256(f"{seed}:{block}".encode()).digest(), block)
    )
    return set(ranked[: math.floor(len(ranked) * fraction)])


def geodesic_area_km2(geometry: BaseGeometry) -> float:
    oriented = orient(geometry)
    return abs(GEOD.geometry_area_perimeter(oriented)[0]) / 1_000_000


def provenance_metadata(paths: Paths, protocol: dict[str, Any]) -> dict[bytes, bytes]:
    config = paths.root / "configs" / "protocol.yaml"
    digest = (
        sha256_file(config)
        if config.exists()
        else hashlib.sha256(yaml.safe_dump(protocol, sort_keys=True).encode()).hexdigest()
    )
    metadata = {b"protocol_sha256": digest.encode()}
    for name, path in (("sources", paths.datasets / "sources.yaml"),):
        if path.exists():
            metadata[f"{name}_sha256".encode()] = sha256_file(path).encode()
    return metadata


def write_parquet(frame: pd.DataFrame, destination: Path, metadata: dict[bytes, bytes]) -> None:
    table = pa.Table.from_pandas(frame, preserve_index=False)
    table = table.replace_schema_metadata({**(table.schema.metadata or {}), **metadata})
    destination.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, destination, compression="zstd")


def load_boundary_geometries(paths: Paths) -> tuple[BaseGeometry, dict[str, BaseGeometry]]:
    sources = yaml.safe_load((paths.datasets / "sources.yaml").read_text(encoding="utf-8"))
    layers = {}
    for level in ("ADM0", "ADM1"):
        record = sources["boundaries"][level]
        path = (paths.root / record["path"]).resolve()
        if not path.is_relative_to((paths.datasets / "raw" / "boundaries").resolve()):
            raise ValueError("Boundary path is outside the raw boundary directory")
        if sha256_file(path) != record["sha256"]:
            raise ValueError(f"Boundary hash mismatch: {level}")
        features = json.loads(path.read_text(encoding="utf-8"))["features"]
        layers[level] = features
    country = shapely.union_all([shapely.make_valid(shape(f["geometry"])) for f in layers["ADM0"]])
    departments: dict[str, BaseGeometry] = {}
    for feature in layers["ADM1"]:
        name = feature["properties"]["shapeName"]
        geometry = shapely.make_valid(shape(feature["geometry"]))
        departments[name] = (
            shapely.union_all([departments[name], geometry]) if name in departments else geometry
        )
    return country, departments


def build_cells(
    country: BaseGeometry, departments: Mapping[str, BaseGeometry], protocol: dict[str, Any]
) -> pd.DataFrame:
    grid = protocol["grid"]
    if grid["resolution_deg"] != 0.05 or grid["origin"] != [-180.0, -90.0]:
        raise ValueError("Unsupported protocol grid")
    west, south, east, north = country.bounds
    minrow, mincol = coordinate_indices(west, south)
    maxrow, maxcol = coordinate_indices(east, north)
    rows, cols = np.meshgrid(
        np.arange(int(minrow), int(maxrow) + 1),
        np.arange(int(mincol), int(maxcol) + 1),
        indexing="ij",
    )
    rows, cols = rows.ravel(), cols.ravel()
    lon, lat = (cols - 3600) / 20, (rows - 1800) / 20
    boxes = shapely.box(lon, lat, (cols + 1 - 3600) / 20, (rows + 1 - 1800) / 20)
    shapely.prepare(country)
    intersects = shapely.intersects(country, boxes)
    rows, cols, boxes = rows[intersects], cols[intersects], boxes[intersects]
    parts = boxes.copy()
    crossing = ~shapely.covers(country, boxes)
    parts[crossing] = shapely.intersection(boxes[crossing], country)
    positive = shapely.area(parts) > 0
    rows, cols, boxes, parts = rows[positive], cols[positive], boxes[positive], parts[positive]
    national = np.array([geodesic_area_km2(part) for part in parts])
    full = np.array([geodesic_area_km2(cell) for cell in boxes])
    names = sorted(departments)
    if not names:
        raise ValueError("At least one department is required")
    largest = np.full(len(rows), -1.0)
    department = np.full(len(rows), "", dtype=object)
    for name in names:
        geometry = departments[name]
        shapely.prepare(geometry)
        candidates = np.flatnonzero(shapely.intersects(geometry, parts))
        shares = np.zeros(len(candidates))
        contained = shapely.covers(geometry, parts[candidates])
        shares[contained] = national[candidates[contained]]
        shares[~contained] = [
            geodesic_area_km2(part)
            for part in shapely.intersection(parts[candidates[~contained]], geometry)
        ]
        better = shares > largest[candidates]
        chosen = candidates[better]
        largest[chosen], department[chosen] = shares[better], name
    if (department == "").any():
        raise ValueError("An eligible cell has no ADM1 intersection")
    frame = pd.DataFrame(
        {
            "cell_id": [f"r{row:04d}c{col:04d}" for row, col in zip(rows, cols, strict=True)],
            "row": rows.astype(np.int32),
            "col": cols.astype(np.int32),
            "west": (cols - 3600) / 20,
            "east": (cols + 1 - 3600) / 20,
            "south": (rows - 1800) / 20,
            "north": (rows + 1 - 1800) / 20,
            "longitude": (cols - 3600 + 0.5) / 20,
            "latitude": (rows - 1800 + 0.5) / 20,
            "national_area_km2": national,
            "full_area_km2": full,
            "national_fraction": national / full,
            "department": department,
            "santa_cruz": department == "Santa Cruz",
        }
    )
    frame["block_id"] = [
        f"b{math.floor(lon)}_{math.floor(lat)}"
        for lon, lat in zip(frame.longitude, frame.latitude, strict=True)
    ]
    rule = protocol["partitions"]["geographic_holdout"]
    held = holdout_blocks(frame.block_id, rule["seed"], rule["fraction"])
    frame["geo_holdout"] = frame.block_id.isin(held)
    return frame


def write_cells(
    paths: Paths, protocol: dict[str, Any], output_directory: Path | None = None
) -> pd.DataFrame:
    cells = build_cells(*load_boundary_geometries(paths), protocol)
    output = (output_directory or paths.datasets / "processed") / "cells.parquet"
    output.parent.mkdir(parents=True, exist_ok=True)
    write_parquet(cells, output, provenance_metadata(paths, protocol))
    return cells
