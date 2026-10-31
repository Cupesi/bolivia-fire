from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd
import shapely
import yaml
from filelock import FileLock
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from sklearn.neighbors import BallTree

from bolivia_fire.boundaries import acquire_boundaries
from bolivia_fire.config import Paths
from bolivia_fire.firms.storage import append_manifest, durable_write, sha256_file
from bolivia_fire.grid import coordinate_indices, geodesic_area_km2, load_boundary_geometries
from bolivia_fire.grid import write_parquet as write_grid_parquet
from bolivia_fire.redact import Redactor

GEONAMES_URL = "https://download.geonames.org/export/dump/BO.zip"
GEONAMES_README = "https://download.geonames.org/export/dump/readme.txt"
EARTH_RADIUS_KM = 6371.0088
PLACE_COLUMNS = [
    "cell_id",
    "department",
    "province",
    "province_national_share",
    "municipality",
    "municipality_national_share",
    "nearest_place_name",
    "nearest_place_geonames_id",
    "nearest_place_feature_code",
    "nearest_place_population",
    "nearest_place_distance_km",
    "populated_places",
]
AdminUnits = list[tuple[str, str, BaseGeometry]]


def acquire_geonames(
    paths: Paths, *, transport: httpx.BaseTransport | None = None
) -> dict[str, Any]:
    paths.temporary.mkdir(parents=True, exist_ok=True)
    sources_path = paths.datasets / "sources.yaml"
    with FileLock(str(paths.temporary / "geonames.lock"), timeout=0):
        sources = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
        pin = sources.get("geonames")
        if pin:
            path = (paths.root / pin["path"]).resolve()
            if not path.is_relative_to((paths.datasets / "raw/geonames").resolve()):
                raise ValueError("GeoNames pin outside raw directory")
            if sha256_file(path) != pin["sha256"]:
                raise ValueError("GeoNames pin hash mismatch")
            return pin
        with httpx.Client(transport=transport, timeout=120, follow_redirects=False) as client:
            readme = client.get(GEONAMES_README)
            readme.raise_for_status()
            if "Creative Commons Attribution 4.0 License" not in readme.text:
                raise ValueError("GeoNames license needs review")
            with client.stream("GET", GEONAMES_URL) as response:
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 64 * 1024 * 1024:
                        raise ValueError("GeoNames download exceeds size limit")
            modified = response.headers.get("last-modified")
        digest = hashlib.sha256(body).hexdigest()
        path = paths.datasets / f"raw/geonames/BO_{digest}.zip"
        durable_write(path, bytes(body))
        read_populated_places(path)
        readme_path = path.with_suffix(".readme.txt")
        durable_write(readme_path, readme.content)
        pin = {
            "provider": "GeoNames",
            "country": "BO",
            "url": GEONAMES_URL,
            "license": "CC BY 4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "attribution": "Place names: GeoNames, CC BY 4.0",
            "license_evidence_url": GEONAMES_README,
            "license_evidence_path": readme_path.relative_to(paths.root).as_posix(),
            "license_evidence_sha256": sha256_file(readme_path),
            "retrieval_utc": datetime.now(UTC).isoformat(),
            "last_modified": modified,
            "sha256": digest,
            "bytes": len(body),
            "path": path.relative_to(paths.root).as_posix(),
            "feature_class": "P",
        }
        sources["geonames"] = pin
        pending = paths.temporary / "places_sources.yaml.part"
        durable_write(
            pending, yaml.safe_dump(sources, sort_keys=False, allow_unicode=True).encode()
        )
        os.replace(pending, sources_path)
        append_manifest(paths.manifests / "geonames.jsonl", pin, Redactor())
        return pin


def read_populated_places(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo("BO.txt")
        if info.file_size > 128 * 1024 * 1024:
            raise ValueError("GeoNames extract exceeds size limit")
        with archive.open(info) as stream:
            frame = pd.read_csv(
                io.TextIOWrapper(stream, encoding="utf-8"),
                sep="\t",
                header=None,
                usecols=[0, 1, 4, 5, 6, 7, 8, 14],
                keep_default_na=False,
                names=[
                    "geonames_id",
                    "name",
                    "latitude",
                    "longitude",
                    "feature_class",
                    "feature_code",
                    "country",
                    "population",
                ],
            )
    frame = frame[frame.feature_class.eq("P") & frame.country.eq("BO")].copy()
    if frame.empty or frame.geonames_id.duplicated().any():
        raise ValueError("GeoNames must contain unique populated places")
    if not np.isfinite(frame[["latitude", "longitude"]].to_numpy(dtype=float)).all():
        raise ValueError("GeoNames coordinates must be finite")
    return frame.sort_values("geonames_id").reset_index(drop=True)


def load_admin_units(paths: Paths, level: str) -> AdminUnits:
    sources = yaml.safe_load((paths.datasets / "sources.yaml").read_text(encoding="utf-8"))
    pin = sources["boundaries"][level]
    path = (paths.root / pin["path"]).resolve()
    if not path.is_relative_to((paths.datasets / "raw/boundaries").resolve()):
        raise ValueError("Boundary pin outside raw directory")
    if sha256_file(path) != pin["sha256"]:
        raise ValueError("Boundary hash mismatch")
    return sorted(
        [
            (
                feature["properties"]["shapeID"],
                feature["properties"]["shapeName"],
                shapely.make_valid(shape(feature["geometry"])),
            )
            for feature in json.loads(path.read_text(encoding="utf-8"))["features"]
        ],
        key=lambda item: (item[1], item[0]),
    )


def largest_admin_share(parts: np.ndarray, units: AdminUnits) -> tuple[list[Any], np.ndarray]:
    national = np.array([geodesic_area_km2(part) for part in parts])
    if np.any(national <= 0):
        raise ValueError("Eligible cell must have positive national area")
    largest = np.zeros(len(parts))
    names: list[Any] = [None] * len(parts)
    for _, name, geometry in units:
        candidates = np.flatnonzero(shapely.intersects(parts, geometry))
        covered = shapely.covers(geometry, parts[candidates])
        areas = national[candidates].copy()
        areas[~covered] = [
            geodesic_area_km2(part)
            for part in shapely.intersection(parts[candidates[~covered]], geometry)
        ]
        better = areas > largest[candidates]
        for index in candidates[better]:
            names[index] = name
        largest[candidates[better]] = areas[better]
    return names, np.minimum(largest / national, 1.0)


def build_cell_places(
    cells: pd.DataFrame,
    country: BaseGeometry,
    provinces: AdminUnits,
    municipalities: AdminUnits,
    places: pd.DataFrame,
) -> pd.DataFrame:
    if places.empty:
        raise ValueError("At least one populated place is required")
    cells = cells.reset_index(drop=True)
    boxes = shapely.box(cells.west, cells.south, cells.east, cells.north)
    parts = np.asarray(boxes).copy()
    crossing = ~shapely.covers(country, boxes)
    parts[crossing] = shapely.intersection(boxes[crossing], country)
    result = cells[["cell_id", "department"]].copy()
    for label, units in (("province", provinces), ("municipality", municipalities)):
        names, shares = largest_admin_share(parts, units)
        result[label] = names
        result[f"{label}_national_share"] = shares
    places = places.sort_values("geonames_id").reset_index(drop=True)
    tree = BallTree(np.deg2rad(places[["latitude", "longitude"]].to_numpy()), metric="haversine")
    distance, indices = tree.query(
        np.deg2rad(cells[["latitude", "longitude"]].to_numpy()),
        k=1,
    )
    nearest = places.iloc[indices[:, 0]].reset_index(drop=True)
    for field in ("name", "geonames_id", "feature_code", "population"):
        result[f"nearest_place_{field}"] = nearest[field]
    result["nearest_place_distance_km"] = distance[:, 0] * EARTH_RADIUS_KM
    inside_country = shapely.covers(country, shapely.points(places.longitude, places.latitude))
    inside = places.loc[inside_country].copy()
    rows, cols = coordinate_indices(inside.longitude, inside.latitude)
    inside["cell_id"] = [f"r{r:04d}c{c:04d}" for r, c in zip(rows, cols, strict=True)]
    ranked = inside.sort_values(["population", "geonames_id"], ascending=[False, True])
    grouped = {
        key: group[["name", "geonames_id", "feature_code", "population"]]
        .head(3)
        .to_dict(orient="records")
        for key, group in ranked.groupby("cell_id", sort=False)
    }
    result["populated_places"] = [grouped.get(key, []) for key in cells.cell_id]
    return result[PLACE_COLUMNS]


def places_columnar(frame: pd.DataFrame, cell_ids: list[str]) -> dict[str, Any]:
    if frame.cell_id.duplicated().any() or set(frame.cell_id) != set(cell_ids):
        raise ValueError("Place table must contain exactly the grid cells once")
    aligned = frame.set_index("cell_id").loc[cell_ids].reset_index()
    # The JSON round trip converts Arrow's nested arrays and nullable scalars consistently.
    return {
        key: json.loads(aligned[key].to_json(orient="records", force_ascii=False))
        for key in PLACE_COLUMNS
    }


def write_cell_places(paths: Paths) -> dict[str, Any]:
    cells_path = paths.datasets / "processed/cells.parquet"
    cells = pd.read_parquet(cells_path).sort_values("cell_id").reset_index(drop=True)
    sources = yaml.safe_load((paths.datasets / "sources.yaml").read_text(encoding="utf-8"))
    pin = sources["geonames"]
    source = paths.root / pin["path"]
    if sha256_file(source) != pin["sha256"]:
        raise ValueError("GeoNames pin hash mismatch")
    places = read_populated_places(source)
    country, _ = load_boundary_geometries(paths)
    result = build_cell_places(
        cells,
        country,
        load_admin_units(paths, "ADM2"),
        load_admin_units(paths, "ADM3"),
        places,
    )
    destination = paths.datasets / "processed/cell_places.parquet"
    metadata = {
        "cells_sha256": sha256_file(cells_path),
        "sources_sha256": sha256_file(paths.datasets / "sources.yaml"),
        "geonames_sha256": pin["sha256"],
        **{
            f"{level.lower()}_sha256": sources["boundaries"][level]["sha256"]
            for level in ("ADM2", "ADM3")
        },
    }
    write_grid_parquet(result, destination, {k.encode(): v.encode() for k, v in metadata.items()})
    record = {
        "path": destination.relative_to(paths.root).as_posix(),
        "sha256": sha256_file(destination),
        "bytes": destination.stat().st_size,
        "retrieval_utc": datetime.now(UTC).isoformat(),
        "cells": len(result),
        "cells_with_municipality": int(result.municipality.notna().sum()),
        "median_nearest_place_distance_km": float(result.nearest_place_distance_km.median()),
        "populated_places": len(places),
        **metadata,
    }
    append_manifest(paths.manifests / "cell_places.jsonl", record, Redactor())
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--acquire", action="store_true")
    args = parser.parse_args()
    paths = Paths()
    if args.acquire:
        acquire_boundaries(paths, levels=("ADM2", "ADM3"))
        acquire_geonames(paths)
    print(json.dumps(write_cell_places(paths), ensure_ascii=False))


if __name__ == "__main__":
    main()
