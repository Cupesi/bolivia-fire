from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import yaml

from bolivia_fire.extension.amendment import AMENDMENT_CONTRACT
from bolivia_fire.extension.registration import Registration, local_path, read_json

SOURCE = "datasets/processed/landcover/source.json"
RASTER_MANIFEST = "datasets/manifests/mapbiomas_raster.jsonl"
PROVENANCE = [
    "land_cover_nominal_map_year",
    "land_cover_map_year",
    "land_cover_reference_age_years",
    "land_cover_mapped_area_fraction",
]


def verify_source(
    registration: Registration,
    hashes: dict[str, str],
    audit: dict[str, Any],
    schema: pa.Schema,
) -> dict[str, Any]:
    if SOURCE not in hashes or audit["input_hashes"].get(SOURCE) != hashes[SOURCE]:
        raise ValueError("Land-cover source manifest requires a frozen checksum and audit binding")
    source = read_json(local_path(registration.paths, SOURCE))
    inputs = source.get("input_hashes", {})
    snapshot = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    metadata = schema.metadata or {}
    if (
        not inputs
        or any(hashes.get(name) != digest for name, digest in inputs.items())
        or inputs.get("datasets/processed/cells.parquet")
        != hashes["datasets/processed/cells.parquet"]
        or inputs.get("configs/extension_vegetation.yaml") != registration.record["contract_sha256"]
        or source.get("extension_contract_sha256") != registration.record["contract_sha256"]
        or source.get("collection") != 4
        or source.get("no_labels_accessed") is not True
        or source.get("no_training_selection_or_scoring") is not True
        or source.get("land_cover_snapshot_sha256") != snapshot
        or metadata.get(b"land_cover_snapshot_sha256") != snapshot.encode()
    ):
        raise ValueError("Land-cover source snapshot, contract, grid or collection mismatch")
    years = source.get("map_years")
    limits = registration.contract["land_cover"]
    if (
        not isinstance(years, list)
        or not years
        or any(type(year) is not int for year in years)
        or years != sorted(set(years))
        or min(years) < limits["optional_fallback_map_year_first"]
        or max(years) > limits["nominal_map_year_range"]["last"]
        or RASTER_MANIFEST not in inputs
    ):
        raise ValueError("Land-cover accessible annual map identities are missing or invalid")
    try:
        identities = [
            json.loads(line)
            for line in local_path(registration.paths, RASTER_MANIFEST)
            .read_text("utf-8")
            .splitlines()
            if line.strip()
        ]
    except (OSError, ValueError) as exc:
        raise ValueError("Land-cover annual identity manifest is malformed") from exc
    if (
        len(identities) != len(years)
        or sorted(item.get("year", -1) for item in identities) != years
    ):
        raise ValueError("Land-cover annual map identities differ from accessible years")
    asset = limits["access"]["gee_route"]["asset"]
    for item in identities:
        year = item["year"]
        annual = f"datasets/processed/landcover/mapbiomas_{year}.parquet"
        if (
            item.get("collection") != limits["product"]
            or item.get("asset") != asset
            or item.get("classification_band") != f"classification_{year}"
            or item.get("output") != annual
            or annual not in inputs
            or item.get("output_sha256") != inputs[annual]
        ):
            raise ValueError("Land-cover annual map collection, year, band or checksum mismatch")
    amendment = yaml.safe_load(
        local_path(registration.paths, AMENDMENT_CONTRACT).read_text("utf-8")
    )
    evidence = amendment["estimator"]["acceptance_evidence"]
    observed = source.get("polygon_pilot_validation", {})
    fields = (
        "cells",
        "fraction_max_absolute_error",
        "coverage_max_absolute_error",
        "absolute_tolerance",
        "passed",
    )
    if (
        any(observed.get(key) != evidence[key] for key in fields)
        or observed.get("passed") is not True
        or observed["absolute_tolerance"] != 0.01
        or observed["fraction_max_absolute_error"] > observed["absolute_tolerance"]
        or observed["coverage_max_absolute_error"] > observed["absolute_tolerance"]
    ):
        raise ValueError("Land-cover estimator acceptance evidence differs from amendment")
    if set(PROVENANCE + limits["features"]) - set(schema.names):
        raise ValueError("Land-cover feature snapshot lacks required provenance columns")
    return source


def admission_mask(
    table: pa.Table,
    features: list[str],
    source: dict[str, Any],
) -> np.ndarray:
    if set(PROVENANCE + features) - set(table.column_names):
        raise ValueError("Land-cover rows lack required provenance columns")
    fractions = np.column_stack([table[name].to_numpy().astype(float) for name in features])
    finite = np.isfinite(fractions).any(axis=1)
    issue_years = pd.DatetimeIndex(pd.to_datetime(table["issue_date"].to_numpy(), utc=True)).year
    nominal = issue_years.to_numpy(dtype=float) - 1
    years = np.array(source["map_years"], dtype=float)
    positions = np.searchsorted(years, nominal, side="right") - 1
    selected = np.where(positions >= 0, years[np.maximum(positions, 0)], np.nan)
    actual = table["land_cover_map_year"].to_numpy().astype(float)
    age = table["land_cover_reference_age_years"].to_numpy().astype(float)
    stated_nominal = table["land_cover_nominal_map_year"].to_numpy().astype(float)
    coverage = table["land_cover_mapped_area_fraction"].to_numpy().astype(float)
    temporal = (actual == selected) & (actual <= nominal)
    temporal &= (stated_nominal == nominal) & (age == issue_years - actual)
    temporal &= (age >= 1) & (age <= 2)
    if (finite & ~temporal).any():
        raise ValueError("Land-cover map year, nominal year or derived age violates admission")
    if (finite & ~(np.isfinite(coverage) & (coverage >= 0.95) & (coverage <= 1))).any():
        raise ValueError("Land-cover finite fractions require mapped coverage in [0.95, 1]")
    return (
        np.isfinite(fractions).all(axis=1)
        & ((fractions >= 0) & (fractions <= 1)).all(axis=1)
        & (np.abs(fractions.sum(axis=1) - 1) <= 1e-6)
        & temporal
        & (coverage >= 0.95)
        & (coverage <= 1)
    )
