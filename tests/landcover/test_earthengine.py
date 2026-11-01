from __future__ import annotations

import pytest

from bolivia_fire.landcover.earthengine import normalize_features


def test_synthetic_missing_feature_is_retained_as_missing() -> None:
    features = [
        {
            "type": "Feature",
            "geometry": None,
            "properties": {
                "cell_id": "synthetic-cell-a",
                "groups": [{"class_code": 3, "sum": 60}, {"class_code": 15, "sum": 40}],
            },
        }
    ]
    frame = normalize_features(features, ["synthetic-cell-a", "synthetic-cell-b"], 2012)
    assert frame.cell_id.tolist() == ["synthetic-cell-a", "synthetic-cell-b"]
    assert frame.year.tolist() == [2012, 2012]
    assert frame.iloc[0].mapbiomas_forest_fraction == pytest.approx(0.6)
    assert bool(frame.iloc[0].response_feature_present) is True
    assert bool(frame.iloc[1].response_feature_present) is False
    assert bool(frame.iloc[1].available) is False
    assert frame.iloc[1].isna()["mapbiomas_forest_fraction"]


def test_synthetic_unrequested_and_duplicate_features_are_rejected() -> None:
    feature = {
        "type": "Feature",
        "geometry": None,
        "properties": {"cell_id": "synthetic-unrequested", "groups": []},
    }
    with pytest.raises(ValueError, match="Unrequested"):
        normalize_features([feature], ["synthetic-requested"], 2012)
    with pytest.raises(ValueError, match="Duplicate"):
        normalize_features([feature, feature], ["synthetic-unrequested"], 2012)


def test_synthetic_duplicate_class_groups_are_added() -> None:
    feature = {
        "type": "Feature",
        "geometry": None,
        "properties": {
            "cell_id": "synthetic-cell",
            "groups": [
                {"class_code": 3, "sum": 20},
                {"class_code": 3, "sum": 30},
                {"class_code": 15, "sum": 50},
            ],
        },
    }
    frame = normalize_features([feature], ["synthetic-cell"], 2012)
    assert frame.iloc[0].mapbiomas_forest_fraction == 0.5
