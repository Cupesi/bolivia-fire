from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

from bolivia_fire.vegetation_features.pixels import (
    MODIS_CRS,
    decode_index,
    observation_days,
    quality_mask,
    read_pixels,
)
from bolivia_fire.vegetation_features.weights import WeightMap, aggregate, build_weights


def test_synthetic_recorded_integer_metadata_scales_once():
    raw = np.array([-3000, -2000, 5000, 10000], dtype=np.int16)
    values = decode_index(raw, scale=0.0001, offset=0, nodata=-3000)
    np.testing.assert_allclose(values[1:], [-0.2, 0.5, 1.0], atol=1e-7)
    assert np.isnan(values[0])
    with pytest.raises(ValueError, match="scal"):
        decode_index(values, scale=0.0001, offset=0, nodata=-3000)


@pytest.mark.parametrize(
    "scale,offset,nodata", [(1, 0, -3000), (0.0001, 1, -3000), (0.0001, 0, -9999)]
)
def test_synthetic_conflicting_metadata_rejected(scale, offset, nodata):
    with pytest.raises(ValueError):
        decode_index(np.array([5000], dtype=np.int16), scale=scale, offset=offset, nodata=nodata)


def test_synthetic_qa_bit_fields():
    good = (1 << 6) | (1 << 11)
    assert quality_mask(np.array([good, good | 512, good | 16]), np.zeros(3)).all()
    bad = [
        good | 1,
        good | 12,
        good & ~192,
        good | 256,
        good | 1024,
        good | 4096,
        good | 16384,
        good | 32768,
        65535,
    ]
    assert not quality_mask(np.array(bad), np.zeros(len(bad))).any()
    assert not quality_mask(np.array([good] * 5), np.array([-1, 1, 2, 3, 4])).any()


def test_synthetic_observation_dates_within_period_and_leap_year():
    days = observation_days(np.array([353, 365, 366, -1]), "2015-12-19", "2016-01-01")
    assert days[0] == np.datetime64("2015-12-19", "D").astype(int)
    assert days[1] == np.datetime64("2015-12-31", "D").astype(int)
    assert (days[2:] == -1).all()
    assert observation_days(np.array([366]), "2016-12-18", "2017-01-01")[0] > 0


def test_synthetic_all_invalid_pixels_are_missing():
    weights = WeightMap(
        np.array([0, 0]), np.array([0, 1]), np.array([1.0, 1.0]), np.array([2.0]), (1, 2)
    )
    result = aggregate(
        weights,
        np.array([0.1, 0.9]),
        np.array([0.2, 0.8]),
        np.array([False, False]),
        np.array([1, 2]),
    )
    assert result["valid_fraction"][0] == 0
    assert np.isnan(result["ndvi"][0])
    assert np.isnan(result["evi"][0])
    assert result["oldest_day"][0] == -1


def test_synthetic_exact_half_coverage_uses_common_area_weights():
    weights = WeightMap(
        np.array([0, 0]), np.array([0, 1]), np.array([1.0, 1.0]), np.array([2.0]), (1, 2)
    )
    result = aggregate(
        weights,
        np.array([0.1, 0.9]),
        np.array([0.2, 0.8]),
        np.array([True, False]),
        np.array([101, 102]),
    )
    assert result["valid_fraction"][0] == 0.5
    assert result["ndvi"][0] == pytest.approx(0.1)
    assert result["latest_day"][0] == 101


def test_synthetic_border_intersections_and_weight_sum(tmp_path: Path):
    path = tmp_path / "synthetic.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=2,
        height=2,
        count=1,
        dtype="int16",
        crs="EPSG:4326",
        transform=from_origin(0, 0.1, 0.05, 0.05),
    ) as target:
        target.write(np.ones((1, 2, 2), dtype=np.int16))
    country = box(0.025, 0, 0.1, 0.1)
    parts = [box(0, 0, 0.05, 0.1).intersection(country), box(0.05, 0, 0.1, 0.1)]
    weights = build_weights(path, parts)
    totals = np.bincount(weights.cell, weights.area, minlength=2)
    np.testing.assert_allclose(totals, weights.denominator, rtol=1e-6)
    assert weights.denominator[0] / weights.denominator[1] == pytest.approx(0.5, rel=1e-6)
    assert len(weights.area) == 4


@pytest.fixture
def synthetic_native_layers(tmp_path: Path):
    layers = {
        "ndvi": (5000, "int16", -3000, 0.0001),
        "evi": (2500, "int16", -3000, 0.0001),
        "vi_quality": (2112, "uint16", 65535, 1),
        "pixel_reliability": (0, "int32", -1, 1),
        "composite_doy": (1, "int16", -1, 1),
    }
    files = {}
    for role, (value, dtype, nodata, scale) in layers.items():
        path = tmp_path / f"synthetic_{role}.tif"
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            count=1,
            width=2,
            height=2,
            crs=MODIS_CRS,
            dtype=dtype,
            nodata=nodata,
            transform=from_origin(-7602035.0527422, -1069325.7497397, 926.62543305, 926.62543305),
        ) as target:
            target.write(np.full((1, 2, 2), value, dtype=dtype))
            target.scales = (scale,)
            target.offsets = (0,)
            target.update_tags(scale_factor=str(scale), add_offset="0")
        files[role] = path
    return files


def test_synthetic_geotiff_metadata_decodes_recorded_integer_storage(synthetic_native_layers):
    pixels = read_pixels(synthetic_native_layers, "2015-01-01", "2015-01-17")
    np.testing.assert_allclose(pixels.ndvi, 0.5)
    np.testing.assert_allclose(pixels.evi, 0.25)
    assert pixels.accepted.all()
    assert pixels.audit["scale_applied_count"] == 1


def test_synthetic_changed_native_grid_rejected(synthetic_native_layers):
    with rasterio.open(synthetic_native_layers["ndvi"]) as src:
        expected = (src.shape, tuple(src.transform), src.crs.to_wkt())
    with rasterio.open(synthetic_native_layers["ndvi"], "r+") as src:
        src.transform = from_origin(-7602036.0527422, -1069325.7497397, 926.62543305, 926.62543305)
    with pytest.raises(ValueError, match="grid"):
        read_pixels(synthetic_native_layers, "2015-01-01", "2015-01-17", expected_grid=expected)
