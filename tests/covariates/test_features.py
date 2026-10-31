from datetime import date

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest
from rasterio.transform import from_origin

from bolivia_fire.covariates import join_retrospective
from bolivia_fire.covariates.chirps import rainfall_features
from bolivia_fire.covariates.mapbiomas import aggregate_pixels, previous_year_features
from bolivia_fire.covariates.raster import grid_alignment, pixel_indices, rainfall_nodata


def test_synthetic_native_index_mapping_and_offset():
    transform = from_origin(-180, 60, 0.05, 0.05)
    rows, cols = pixel_indices(transform, [-69.975, -69.925], [-22.975, -22.925])
    np.testing.assert_array_equal(rows, [1659, 1658])
    np.testing.assert_array_equal(cols, [2200, 2201])
    assert grid_alignment(transform)["aligned"]
    shifted = from_origin(-179.99, 60.01, 0.05, 0.05)
    assert not grid_alignment(shifted)["aligned"]
    assert grid_alignment(shifted)["longitude_offset_deg"] == pytest.approx(0.01)
    rounded_scale = from_origin(-180, 60, 0.05000000074505806, 0.05000000074505806)
    assert not grid_alignment(rounded_scale)["aligned"]


def test_synthetic_nodata_is_missing_never_dry():
    result = rainfall_nodata(np.array([[0, -9999, 2, -123]], dtype=np.float32), -123)
    np.testing.assert_array_equal(np.isnan(result), [[False, True, False, True]])
    assert result[0, 0] == 0


def test_synthetic_antecedent_windows_and_future_perturbations():
    dates = pd.date_range("2019-01-01", "2021-04-10")
    synthetic_rain = np.ones((len(dates), 2), dtype=np.float32)
    issue = date(2021, 4, 5)
    result = rainfall_features(dates, synthetic_rain, ["a", "b"], [issue], [2019, 2020])
    assert result.rain_sum_7d.tolist() == [7, 7]
    assert result.rain_sum_30d.tolist() == [30, 30]
    assert result.rain_sum_90d.tolist() == [90, 90]
    assert result.dry_days_30d.tolist() == [0, 0]
    assert result.rain_anomaly_90d.tolist() == [1, 1]
    assert result.rain_complete_90d.all()
    synthetic_rain[dates >= pd.Timestamp(issue)] = 100000
    changed = rainfall_features(dates, synthetic_rain, ["a", "b"], [issue], [2019, 2020])
    pd.testing.assert_frame_equal(result, changed)
    synthetic_rain[dates.year == 2021] *= 2
    changed = rainfall_features(dates, synthetic_rain, ["a", "b"], [issue], [2019, 2020])
    assert changed.rain_anomaly_90d.tolist() == [2, 2]


def test_synthetic_missing_days_and_zero_climatology():
    dates = pd.date_range("2020-01-01", "2021-04-04")
    synthetic_rain = np.zeros((len(dates), 2), dtype=np.float32)
    synthetic_rain[-1, 0] = np.nan
    result = rainfall_features(dates, synthetic_rain, ["a", "b"], [date(2021, 4, 5)], [2020])
    assert not result.rain_complete_7d.iloc[0]
    assert np.isnan(result.rain_sum_7d.iloc[0])
    assert np.isnan(result.dry_days_30d.iloc[0])
    assert result.dry_days_30d.iloc[1] == 30
    assert result.rain_anomaly_90d.isna().all()


def test_synthetic_training_issue_anomaly_does_not_read_future_training_rain():
    dates = pd.date_range("2019-01-01", "2020-12-31")
    synthetic_rain = np.ones((len(dates), 1), dtype=np.float32)
    issue = date(2019, 4, 8)
    before = rainfall_features(dates, synthetic_rain, ["a"], [issue], [2019, 2020])
    synthetic_rain[dates >= pd.Timestamp(issue)] = 1000
    after = rainfall_features(dates, synthetic_rain, ["a"], [issue], [2019, 2020])
    pd.testing.assert_frame_equal(before, after)


def test_synthetic_landcover_pixel_centers_and_nodata():
    synthetic_codes = np.array([[3, 15, 33, 27], [3, 12, 24, 0]], dtype=np.uint8)
    transform = from_origin(-63, -17, 0.025, 0.025)
    result = aggregate_pixels(synthetic_codes, transform, nodata=0)
    assert len(result) == 2
    west, east = result.sort_values("cell_id").itertuples(index=False)
    assert west.mapbiomas_forest_fraction == 0.5
    assert west.mapbiomas_farming_fraction == 0.25
    assert west.mapbiomas_nonforest_fraction == 0.25
    assert east.mapbiomas_water_fraction == pytest.approx(1 / 3)
    assert east.mapbiomas_nonvegetated_fraction == pytest.approx(1 / 3)
    assert east.mapbiomas_other_fraction == pytest.approx(1 / 3)
    assert east.mapbiomas_valid_pixels == 3


def test_synthetic_previous_year_rule_and_join(tmp_path):
    panel = pd.DataFrame({"cell_id": ["a", "a"], "issue_date": pd.to_datetime(
        ["2023-01-02", "2024-01-01"]), "y_primary": [0, 1]})
    land = pd.DataFrame({"cell_id": ["a", "a", "a"], "year": [2022, 2023, 2024],
                         "mapbiomas_forest_fraction": [0.2, 0.3, 0.99]})
    selected = previous_year_features(panel, land)
    assert selected.mapbiomas_forest_fraction.tolist() == [0.2, 0.3]
    folder = tmp_path / "mapbiomas_fractions.parquet"
    land.to_parquet(folder, index=False)
    joined = join_retrospective(pa.Table.from_pandas(panel), root=tmp_path)
    assert isinstance(joined, pa.Table)
    assert joined.column("mapbiomas_forest_fraction").to_pylist() == [0.2, 0.3]
    assert joined.column("y_primary").to_pylist() == [0, 1]


def test_synthetic_join_rejects_duplicate_keys(tmp_path):
    land = pd.DataFrame({"cell_id": ["a", "a"], "year": [2022, 2022],
                         "mapbiomas_forest_fraction": [0.2, 0.3]})
    land.to_parquet(tmp_path / "mapbiomas_fractions.parquet", index=False)
    panel = pa.table({"cell_id": ["a"], "issue_date": [date(2023, 1, 2)]})
    with pytest.raises(ValueError, match="duplicate"):
        join_retrospective(panel, root=tmp_path)
