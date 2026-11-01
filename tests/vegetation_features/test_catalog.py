import pytest

from bolivia_fire.vegetation_features.catalog import validate_statistics_start


def test_synthetic_qa_uses_date_confirmed_by_continuous_statistics():
    statistics = {"MOD13A2_061__1_km_16_days_NDVI_doy2014001000000_aid0001": "2014-01-01"}
    validate_statistics_start(
        "MOD13A2.061__1_km_16_days_VI_Quality_doy2014001000000_aid0001.tif",
        "vi_quality",
        "2014-01-01",
        statistics,
    )
    with pytest.raises(ValueError, match="statistics"):
        validate_statistics_start("synthetic.tif", "ndvi", "2014-01-01", statistics)
