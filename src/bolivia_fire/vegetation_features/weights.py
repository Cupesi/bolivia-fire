from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
import shapely
from numpy.typing import NDArray
from pyproj import Transformer
from shapely.geometry.base import BaseGeometry

from bolivia_fire.grid import geodesic_area_km2


@dataclass
class WeightMap:
    cell: NDArray
    pixel: NDArray
    area: NDArray
    denominator: NDArray
    shape: tuple[int, int]

    def save(self, path: Path) -> None:
        temporary = path.with_suffix(".part")
        with temporary.open("wb") as stream:
            np.savez_compressed(
                stream,
                cell=self.cell,
                pixel=self.pixel,
                area=self.area,
                denominator=self.denominator,
                shape=self.shape,
            )
        temporary.replace(path)

    @classmethod
    def load(cls, path: Path) -> WeightMap:
        with np.load(path) as archive:
            return cls(
                archive["cell"],
                archive["pixel"],
                archive["area"],
                archive["denominator"],
                tuple(archive["shape"]),
            )


def build_weights(
    raster: Path,
    parts: Sequence[BaseGeometry],
    *,
    checkpoint: Callable[[], None] | None = None,
) -> WeightMap:
    with rasterio.open(raster) as src:
        affine, crs, height, width = src.transform, src.crs, src.height, src.width
    to_native = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform
    to_geo = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform
    inverse = ~affine
    native_step = abs(affine.a) / 4
    cell_arrays, pixel_arrays, areas = [], [], []
    denominator = np.empty(len(parts))
    for index, original in enumerate(parts):
        if checkpoint and index % 100 == 0:
            checkpoint()
        part = shapely.segmentize(original, 0.001)
        denominator[index] = geodesic_area_km2(part)
        bounds = shapely.transform(part, to_native, interleaved=False).bounds
        col0, row0 = inverse @ (bounds[0], bounds[3])
        col1, row1 = inverse @ (bounds[2], bounds[1])
        rows, cols = np.meshgrid(
            np.arange(max(0, math.floor(row0) - 1), min(height, math.ceil(row1) + 1)),
            np.arange(max(0, math.floor(col0) - 1), min(width, math.ceil(col1) + 1)),
            indexing="ij",
        )
        rows, cols = rows.ravel(), cols.ravel()
        x, y = affine @ (cols, rows)
        polygons = shapely.segmentize(shapely.box(x, y + affine.e, x + affine.a, y), native_step)
        polygons = shapely.transform(polygons, to_geo, interleaved=False)
        intersections = shapely.intersection(polygons, part)
        positive = shapely.area(intersections) > 0
        weights = np.array([geodesic_area_km2(p) for p in intersections[positive]])
        cell_arrays.append(np.full(len(weights), index, dtype=np.int32))
        pixel_arrays.append((rows[positive] * width + cols[positive]).astype(np.int32))
        areas.append(weights)
    result = WeightMap(
        np.concatenate(cell_arrays),
        np.concatenate(pixel_arrays),
        np.concatenate(areas),
        denominator,
        (height, width),
    )
    sums = np.bincount(result.cell, result.area, minlength=len(parts))
    if not np.allclose(sums, denominator, atol=1e-7, rtol=1e-5):
        raise ValueError("Raster footprints do not cover every complete national cell portion")
    return result


def aggregate(
    weights: WeightMap,
    ndvi: NDArray,
    evi: NDArray,
    accepted: NDArray,
    days: NDArray,
) -> dict[str, NDArray]:
    count = len(weights.denominator)
    selected = accepted.ravel()[weights.pixel]
    cells, area, pixels = weights.cell[selected], weights.area[selected], weights.pixel[selected]
    total = np.bincount(cells, area, minlength=count)
    fraction = total / weights.denominator
    if (fraction > 1.00001).any():
        raise ValueError("Accepted pixel area exceeds national cell area")
    fraction = np.minimum(fraction, 1)
    valid = fraction >= 0.5
    result = {"valid_fraction": fraction.astype(np.float32)}
    for name, values in (("ndvi", ndvi), ("evi", evi)):
        numerator = np.bincount(cells, area * values.ravel()[pixels], minlength=count)
        result[name] = np.divide(
            numerator, total, out=np.full(count, np.nan), where=valid & (total > 0)
        ).astype(np.float32)
    oldest, latest = np.full(count, 2**31 - 1, dtype=np.int32), np.full(count, -1, dtype=np.int32)
    np.minimum.at(oldest, cells, days.ravel()[pixels])
    np.maximum.at(latest, cells, days.ravel()[pixels])
    result["oldest_day"] = np.where(valid, oldest, -1)
    result["latest_day"] = np.where(valid, latest, -1)
    return result
