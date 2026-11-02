from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bolivia_fire.landcover.core import FEATURES
from bolivia_fire.landcover.features import issue_features


def synthetic_maps() -> pd.DataFrame:
    rows = []
    for year, group in ((2012, "forest"), (2014, "water"), (2018, "farming")):
        for cell, available in (("synthetic-west", True), ("synthetic-east", False)):
            rows.append({
                "cell_id": cell, "year": year, "map_year": year,
                "available": available, "mapped_area_fraction": 1.0 if available else 0.9,
                **{feature: float(feature == f"mapbiomas_{group}_fraction")
                   if available else np.nan for feature in FEATURES},
            })
    return pd.DataFrame(rows)


def test_synthetic_reference_year_never_uses_future_and_expires_after_two_years() -> None:
    dates = pd.to_datetime([
        "2013-01-07", "2014-01-06", "2015-01-05", "2016-01-04", "2017-01-02",
    ], utc=True)
    result = issue_features(synthetic_maps(), ["synthetic-west", "synthetic-east"], dates)
    west = result.loc[result.cell_id.eq("synthetic-west")].reset_index(drop=True)
    assert west.land_cover_nominal_map_year.tolist() == [2012, 2013, 2014, 2015, 2016]
    assert west.land_cover_map_year.iloc[:4].tolist() == [2012, 2012, 2014, 2014]
    assert pd.isna(west.land_cover_map_year.iloc[4])
    assert west.mapbiomas_forest_fraction.iloc[:4].tolist() == [1, 1, 0, 0]
    assert west.mapbiomas_water_fraction.iloc[:4].tolist() == [0, 0, 1, 1]
    assert west.loc[4, list(FEATURES)].isna().all()
    assert result.loc[result.cell_id.eq("synthetic-east"), list(FEATURES)].isna().all().all()
    assert len(result) == 10 and not result.duplicated(["cell_id", "issue_date"]).any()


def test_synthetic_issue_join_rejects_duplicate_maps_and_non_monday_dates() -> None:
    annual = synthetic_maps()
    with pytest.raises(ValueError, match="Duplicate"):
        issue_features(pd.concat([annual, annual.iloc[:1]]), ["synthetic-west"],
                       pd.to_datetime(["2013-01-07"], utc=True))
    with pytest.raises(ValueError, match="Monday"):
        issue_features(annual, ["synthetic-west"], pd.to_datetime(["2013-01-08"], utc=True))
