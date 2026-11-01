from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from numpy.typing import NDArray
from pyproj import CRS

MODIS_CRS = CRS.from_proj4("+proj=sinu +R=6371007.181 +units=m +no_defs")
FILLS = {
    "ndvi": -3000,
    "evi": -3000,
    "vi_quality": 65535,
    "pixel_reliability": -1,
    "composite_doy": -1,
}


def decode_index(raw: NDArray, *, scale: float, offset: float, nodata: float) -> NDArray:
    if raw.dtype.kind not in "iu" or not np.isclose(scale, 0.0001, rtol=0, atol=1e-12):
        raise ValueError("Ambiguous scaling: require unscaled integers and metadata scale 0.0001")
    if offset != 0 or nodata != -3000:
        raise ValueError("Conflicting index offset or fill metadata")
    result = raw.astype(np.float32) * np.float32(scale)
    result[(raw == nodata) | (raw < -2000) | (raw > 10000)] = np.nan
    return result


def quality_mask(qa: NDArray, reliability: NDArray) -> NDArray:
    return (
        (reliability == 0)
        & (qa >= 0)
        & (qa < 65535)
        & ((qa & 3) == 0)
        & np.isin((qa >> 2) & 15, [0, 1, 2, 4])
        & np.isin((qa >> 6) & 3, [1, 2])
        & ((qa & 256) == 0)
        & ((qa & 1024) == 0)
        & (((qa >> 11) & 7) == 1)
        & ((qa & 16384) == 0)
        & ((qa & 32768) == 0)
    )


def observation_days(doy: NDArray, start: str, end_boundary: str) -> NDArray:
    first = np.datetime64(start, "D")
    end = np.datetime64(end_boundary, "D")
    year = first.astype("datetime64[Y]")
    next_year = year + np.timedelta64(1, "Y")
    days = year.astype("datetime64[D]").astype(np.int64) + doy.astype(np.int64) - 1
    valid = (
        (doy >= 1)
        & (doy <= 366)
        & (days >= first.astype(int))
        & (days < end.astype(int))
        & (days < next_year.astype("datetime64[D]").astype(int))
    )
    return np.where(valid, days, -1).astype(np.int32)


@dataclass
class Pixels:
    ndvi: NDArray
    evi: NDArray
    accepted: NDArray
    days: NDArray
    audit: dict[str, Any]


def read_pixels(
    files: dict[str, Path],
    start: str,
    end_boundary: str,
    *,
    expected_grid: tuple | None = None,
) -> Pixels:
    arrays, metadata = {}, {}
    grid = None
    for role, path in files.items():
        with rasterio.open(path) as src:
            if src.count != 1 or src.crs is None or not CRS(src.crs).equals(MODIS_CRS):
                raise ValueError("Expected one-band native MODIS sinusoidal raster")
            transform = src.transform
            if (
                transform.b != 0
                or transform.d != 0
                or transform.a <= 0
                or transform.e >= 0
                or not np.isclose(transform.a, 926.62543305, atol=1e-6, rtol=0)
                or not np.isclose(-transform.e, transform.a, atol=1e-9, rtol=0)
            ):
                raise ValueError("Unexpected native MODIS affine transform")
            identity = (src.shape, tuple(transform), src.crs.to_wkt())
            if expected_grid is not None and identity != expected_grid:
                raise ValueError("Composite native grid differs from the cached area weights")
            if grid is not None and grid != identity:
                raise ValueError("Composite layers do not share the exact native grid")
            grid = identity
            raw = src.read(1, masked=False)
            scale, offset = src.scales[0], src.offsets[0]
            tags = src.tags()
            if (
                float(tags.get("scale_factor", scale)) != scale
                or float(tags.get("add_offset", offset)) != offset
            ):
                raise ValueError("Conflicting GDAL and tagged scaling metadata")
            if src.nodata != FILLS[role]:
                raise ValueError(f"Unexpected fill metadata for {role}")
            if role in {"ndvi", "evi"}:
                values = decode_index(raw, scale=scale, offset=offset, nodata=src.nodata)
                physical = raw.astype(np.float64) * scale + offset
                good = np.isfinite(values)
                if not np.allclose(
                    values[good], physical[good].astype(np.float32), atol=6e-8, rtol=0
                ):
                    raise ValueError("Raw and metadata-decoded index scaling disagree")
                rejected = int(((raw != src.nodata) & ~good).sum())
            else:
                if raw.dtype.kind not in "iu" or scale != 1 or offset != 0:
                    raise ValueError("QA, reliability and DOY must be unscaled integers")
                values, rejected = raw, 0
            arrays[role] = values
            metadata[role] = {
                "dtype": src.dtypes[0],
                "nodata": src.nodata,
                "scale": scale,
                "offset": offset,
                "reader": "rasterio raw; automatic scaling disabled",
                "raw_nonfill_min": float(raw[raw != src.nodata].min())
                if (raw != src.nodata).any()
                else None,
                "raw_nonfill_max": float(raw[raw != src.nodata].max())
                if (raw != src.nodata).any()
                else None,
                "out_of_range_pixels": rejected,
            }
    days = observation_days(arrays["composite_doy"], start, end_boundary)
    qa = quality_mask(arrays["vi_quality"], arrays["pixel_reliability"])
    accepted = qa & np.isfinite(arrays["ndvi"]) & np.isfinite(arrays["evi"]) & (days >= 0)
    return Pixels(
        arrays["ndvi"],
        arrays["evi"],
        accepted,
        days,
        {
            "layers": metadata,
            "accepted_pixels": int(accepted.sum()),
            "qa_rejected_pixels": int((~qa).sum()),
            "date_rejected_pixels": int((days < 0).sum()),
            "total_pixels": int(days.size),
            "scale_applied_count": 1,
        },
    )
