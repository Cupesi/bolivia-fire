import pandas as pd

from bolivia_fire.labels import explain_label, labels_for_issue


def synthetic_detections():
    return pd.DataFrame(
        {
            "cell_id": ["a"] * 5,
            "acq_datetime": pd.to_datetime(
                [
                    "2024-09-01T23:59Z",
                    "2024-09-02T00:00Z",
                    "2024-09-08T23:59Z",
                    "2024-09-09T00:00Z",
                    "2024-09-03T00:00Z",
                ]
            ),
            "type": [0, 0, 0, 0, 0],
            "confidence": ["n", "n", "l", "h", "n"],
            "in_bolivia": [True, True, True, True, False],
            "source_file": ["synthetic.csv"] * 5,
            "source_line": [1, 2, 3, 4, 5],
        }
    )


def synthetic_days():
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-09-01", "2024-09-15"),
            "acquired": True,
            "observed": True,
            "bbox_detection_count": 1,
        }
    )


def test_synthetic_target_half_open_selection_country_and_explanation():
    detections = synthetic_detections()
    cells = pd.DataFrame({"cell_id": ["a", "b"]})
    result = labels_for_issue(cells, detections, synthetic_days(), "2024-09-02")
    assert result.n_primary.tolist() == [1, 0]
    assert result.n_allconf.tolist() == [2, 0]
    assert result.y_primary.tolist() == [1, 0]
    assert result.target_complete.all()
    rows = explain_label(detections, "a", "2024-09-02")
    assert rows.source_line.tolist() == [2]
    assert len(rows) == result.n_primary.iloc[0]
    before_deleted = detections[detections.acq_datetime >= pd.Timestamp("2024-09-02", tz="UTC")]
    pd.testing.assert_frame_equal(
        result, labels_for_issue(cells, before_deleted, synthetic_days(), "2024-09-02")
    )


def test_synthetic_missing_day_yields_null_and_zero_bbox_is_only_quality_flag():
    days = synthetic_days()
    days.loc[days.date == pd.Timestamp("2024-09-04"), "acquired"] = False
    days.loc[days.date == pd.Timestamp("2024-09-05"), "bbox_detection_count"] = 0
    result = labels_for_issue(
        pd.DataFrame({"cell_id": ["a", "b"]}), synthetic_detections(), days, "2024-09-02"
    )
    assert result.y_primary.isna().all()
    assert result.y_allconf.isna().all()
    assert not result.target_complete.any()
    assert result.target_low_coverage.all()
