from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import rasterio
from affine import Affine
from numpy.typing import NDArray
from pyproj import Transformer
from rasterio.warp import transform_bounds
from rasterio.windows import Window, from_bounds
from rasterio.windows import transform as window_transform

from bolivia_fire.covariates.raster import BBOX, counted_vsicurl, raster_metadata
from bolivia_fire.covariates.storage import Budget, append_jsonl
from bolivia_fire.grid import coordinate_indices

URL_TEMPLATE = (
    "https://storage.googleapis.com/mapbiomas-public/initiatives/bolivia/lulc/"
    "collection_04/coverage/coverage-bolivia_classification_{year}.tif"
)
GROUPS = {
    "forest": {1, 3, 4, 6},
    "nonforest": {10, 11, 12, 13, 29, 63, 66, 77, 81, 82},
    "farming": {14, 15, 18, 21, 39, 40, 72},
    "nonvegetated": {22, 23, 24, 25, 30, 61, 68, 92},
    "water": {26, 31, 33, 34},
}
FRACTION_COLUMNS = [f"mapbiomas_{name}_fraction" for name in (*GROUPS, "other")]


def aggregate_pixels(
    codes: NDArray, transform: Affine, *, nodata: int | None = 0,
    crs: Any = "EPSG:4326",
) -> pd.DataFrame:
    rows, cols = np.indices(codes.shape)
    x, y = cols.ravel() + 0.5, rows.ravel() + 0.5
    longitude = transform.a * x + transform.b * y + transform.c
    latitude = transform.d * x + transform.e * y + transform.f
    if rasterio.crs.CRS.from_user_input(crs) != rasterio.crs.CRS.from_epsg(4326):
        longitude, latitude = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform(
            longitude, latitude,
        )
    grid_rows, grid_cols = coordinate_indices(longitude, latitude)
    values = codes.ravel()
    valid = np.isfinite(values)
    if nodata is not None:
        valid &= values != nodata
    ids = grid_rows[valid] * 10000 + grid_cols[valid]
    unique, inverse, totals = np.unique(ids, return_inverse=True, return_counts=True)
    output = pd.DataFrame({
        "cell_id": [f"r{v // 10000:04d}c{v % 10000:04d}" for v in unique],
        "mapbiomas_valid_pixels": totals.astype(np.int64),
    })
    known = np.zeros(valid.sum(), dtype=bool)
    for group, members in GROUPS.items():
        selected = np.isin(values[valid], list(members))
        known |= selected
        counts = np.bincount(inverse, weights=selected, minlength=len(unique))
        output[f"mapbiomas_{group}_fraction"] = (counts / totals).astype(np.float32)
    counts = np.bincount(inverse, weights=~known, minlength=len(unique))
    output["mapbiomas_other_fraction"] = (counts / totals).astype(np.float32)
    return output


def previous_year_features(panel: pd.DataFrame, fractions: pd.DataFrame) -> pd.DataFrame:
    if fractions.duplicated(["cell_id", "year"]).any():
        raise ValueError("MapBiomas fractions contain duplicate cell-year keys")
    keys = panel[["cell_id", "issue_date"]].copy()
    keys["year"] = pd.to_datetime(keys.issue_date).dt.year - 1
    keys["_row"] = np.arange(len(keys))
    joined = keys.merge(fractions, on=["cell_id", "year"], how="left", validate="many_to_one")
    joined = joined.sort_values("_row").drop(columns=["year", "_row"])
    return joined.reset_index(drop=True)


def aggregate_raster(
    source: str, *, eligible_ids: set[str] | None = None, budget: Budget | None = None,
) -> pd.DataFrame:
    parts = []
    with rasterio.open(source) as src:
        bounds = transform_bounds("EPSG:4326", src.crs, *BBOX)
        region = from_bounds(*bounds, src.transform)
        first_row = max(0, int(np.floor(region.row_off)))
        first_col = max(0, int(np.floor(region.col_off)))
        last_row = min(src.height, int(np.ceil(region.row_off + region.height)))
        last_col = min(src.width, int(np.ceil(region.col_off + region.width)))
        for row in range(first_row, last_row, 512):
            if budget is not None:
                budget.check_storage()
            for col in range(first_col, last_col, 512):
                window = Window(col, row, min(512, last_col-col), min(512, last_row-row))
                part = aggregate_pixels(
                    src.read(1, window=window), window_transform(window, src.transform),
                    nodata=src.nodata if src.nodata is not None else 0, crs=src.crs,
                )
                if eligible_ids is not None:
                    part = part[part.cell_id.isin(eligible_ids)]
                if not part.empty:
                    for column in FRACTION_COLUMNS:
                        part[column] = part[column].astype(float) * part.mapbiomas_valid_pixels
                    parts.append(part)
    if not parts:
        raise ValueError("No valid MapBiomas pixels overlap eligible cells")
    combined = pd.concat(parts, ignore_index=True).groupby("cell_id", sort=True).sum()
    for column in FRACTION_COLUMNS:
        combined[column] = (combined[column] / combined.mapbiomas_valid_pixels).astype(np.float32)
    return combined.reset_index()


def extract_mapbiomas(year: int, root: Path, cells: pd.DataFrame) -> dict[str, Any]:
    budget = Budget(root)
    url = URL_TEMPLATE.format(year=year)
    with counted_vsicurl(url, budget) as (source, record), rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_PAM_ENABLED="NO", GDAL_HTTP_TIMEOUT="60",
        GDAL_CACHEMAX=32 * 1024**2,
    ):
        try:
            with rasterio.open(source) as src:
                record.update(raster_metadata(src))
                if not record["tiled"]:
                    raise RuntimeError("MapBiomas is not tiled; bounded extraction unavailable")
            frame = aggregate_raster(source, eligible_ids=set(cells.cell_id), budget=budget)
        except OSError as exc:
            record.update(year=year, status="unavailable", error=str(exc))
            append_jsonl(root / "mapbiomas_manifest.jsonl", record)
            raise RuntimeError(f"MapBiomas anonymous access unavailable: {record}") from None
    frame["year"] = year
    destination = root / "mapbiomas_fractions.parquet"
    if destination.exists():
        previous = pd.read_parquet(destination)
        frame = pd.concat([previous[previous.year != year], frame], ignore_index=True)
    pending = destination.with_suffix(".parquet.part")
    budget.check_storage(frame.memory_usage(deep=True).sum())
    frame.to_parquet(pending, index=False)
    os.replace(pending, destination)
    record.update(year=year, status="ok", cells=len(cells), sha256=hashlib.sha256(
        pd.util.hash_pandas_object(frame, index=False).values.tobytes(),
    ).hexdigest())
    append_jsonl(root / "mapbiomas_manifest.jsonl", record)
    return record
