import numpy as np
import pandas as pd
import pytest

from bolivia_fire.vegetation_features.temporal import (
    CompositeSeries,
    join_features,
    seasonal_reference,
)


def synthetic_series():
    starts = pd.to_datetime(["2015-01-01", "2015-01-17", "2015-02-02", "2015-02-18"])
    values = np.array([[0.1], [0.2], [0.3], [0.4]], dtype=np.float32)
    days = starts.to_numpy().astype("datetime64[D]").astype(np.int32)[:, None]
    return CompositeSeries(
        starts, starts + pd.Timedelta(days=16), values, values / 2, np.ones((4, 1)), days, days + 15
    )


def test_synthetic_end_plus_lag_exact_boundary_and_monday():
    series = synthetic_series()
    # The fourth composite ends March 5 inclusive; its 16-day lag ends March 22.
    before = series.for_issue("2015-03-21", 16)
    exact = series.for_issue("2015-03-22", 16)
    monday = series.for_issue("2015-03-23", 16)
    assert before.loc[0, "veg_ndvi_latest"] == pytest.approx(0.3)
    assert exact.loc[0, "veg_ndvi_latest"] == pytest.approx(0.4)
    assert monday.loc[0, "veg_ndvi_change_previous"] == pytest.approx(0.1)
    assert monday.loc[0, "veg_ndvi_change_48d"] == pytest.approx(0.3)
    assert monday.loc[0, "veg_observation_age_days"] == 18
    assert series.for_issue("2015-03-23", 32).loc[0, "veg_ndvi_latest"] == pytest.approx(0.3)


def test_synthetic_every_contributing_pixel_age_gate_and_no_forward_fill():
    series = synthetic_series()
    assert np.isfinite(series.for_issue("2015-04-23", 16).loc[0, "veg_ndvi_latest"])
    assert np.isnan(series.for_issue("2015-04-24", 16).loc[0, "veg_ndvi_latest"])


def test_synthetic_reference_age_128_days_and_previous_gap():
    series = synthetic_series()
    series.oldest[0, 0] -= 100
    assert np.isnan(series.for_issue("2015-03-23", 16).loc[0, "veg_ndvi_change_48d"])
    series.ndvi[2, 0] = np.nan
    series.ndvi[1, 0] = np.nan
    assert np.isnan(series.for_issue("2015-03-23", 16).loc[0, "veg_ndvi_change_previous"])


def test_synthetic_loyo_excludes_own_year_and_holdout_and_requires_four_years():
    starts = pd.to_datetime([f"{year}-01-01" for year in range(2013, 2018)])
    ndvi = np.array([[1.0, 999.0] if year == 2015 else [0.5, 999.0] for year in range(2013, 2018)])
    cells = pd.DataFrame(
        {"cell_id": ["a", "b"], "department": ["D", "D"], "geo_holdout": [False, True]}
    )
    reference, count = seasonal_reference(starts, ndvi, cells, 2015, 0)
    assert reference[0] == 0.5
    assert np.isnan(reference[1])
    assert count[0] == 4
    geographic, _ = seasonal_reference(starts, ndvi, cells, 2015, 0, geography="geographic")
    np.testing.assert_allclose(geographic, [0.5, 0.5])
    reference, _ = seasonal_reference(starts[:4], ndvi[:4], cells, 2015, 0)
    assert np.isnan(reference).all()


def test_synthetic_duplicate_join_protection():
    left = pd.DataFrame({"cell_id": ["a"], "issue_date": [pd.Timestamp("2015-03-23")]})
    with pytest.raises(ValueError, match="Duplicate"):
        join_features(left, pd.concat([left, left]))


def test_synthetic_48_day_tie_prefers_earlier_and_sensitivity_remains_separate():
    starts = pd.to_datetime(["2015-01-01", "2015-01-17", "2015-02-10", "2015-02-26"])
    values = np.array([[0.1], [0.2], [0.3], [0.4]], dtype=np.float32)
    oldest = starts.to_numpy().astype("datetime64[D]").astype(np.int32)[:, None]
    series = CompositeSeries(
        starts, starts + pd.Timedelta(days=16), values, values, np.ones((4, 1)), oldest, oldest
    )
    result = series.for_issue("2015-03-30", 16)
    assert result.loc[0, "veg_ndvi_change_48d"] == pytest.approx(0.3)
    assert series.for_issue("2015-03-30", 32).loc[0, "veg_ndvi_latest"] == pytest.approx(0.3)


def test_synthetic_geographic_reference_weights_cells_then_years():
    starts = pd.to_datetime([f"{year}-01-01" for year in range(2013, 2018)])
    values = np.array(
        [[0.2, 0.8, 999], [0.2, np.nan, 999], [0.2, 0.8, 999], [0.2, 0.8, 999], [1, 1, 999]]
    )
    cells = pd.DataFrame(
        {"cell_id": ["a", "b", "c"], "department": ["D"] * 3, "geo_holdout": [False, False, True]}
    )
    reference, count = seasonal_reference(starts, values, cells, 2017, 0, geography="geographic")
    np.testing.assert_allclose(reference, [0.425] * 3)
    np.testing.assert_array_equal(count, [4] * 3)


def test_synthetic_2026_requires_archive_evidence():
    series = synthetic_series()
    series.starts += pd.DateOffset(years=11)
    series.ends += pd.DateOffset(years=11)
    series.oldest += 4018
    series.latest += 4018
    assert series.for_issue("2026-03-23", 16).veg_ndvi_latest.isna().all()


def test_synthetic_fractional_observation_age_and_subthreshold_nulls():
    series = synthetic_series()
    result = series.for_issue("2015-03-23T12:00:00Z", 16)
    assert result.loc[0, "veg_observation_age_days"] == 18.5
    series.coverage[:] = 0.49
    assert series.for_issue("2015-03-23", 16).iloc[0, :10].isna().all()
