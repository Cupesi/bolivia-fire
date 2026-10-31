import numpy as np
import pandas as pd
import pytest

from bolivia_fire.config import Paths, load_protocol
from bolivia_fire.features import DailyHistory, train_climatology


def synthetic_cells():
    return pd.DataFrame(
        {
            "cell_id": ["a", "b", "c"],
            "row": [10, 10, 13],
            "col": [10, 11, 13],
            "geo_holdout": [False, False, True],
        }
    )


def synthetic_history_detections():
    return pd.DataFrame(
        {
            "cell_id": ["a", "a", "b", "c", "a", "a", "a", "a"],
            "acq_datetime": pd.to_datetime(
                [
                    "2024-09-01T23:59Z",
                    "2024-08-26T00:00Z",
                    "2024-08-25T23:59Z",
                    "2024-08-20T00:00Z",
                    "2023-09-04T00:00Z",
                    "2024-09-02T00:00Z",
                    "2024-08-31T00:00Z",
                    "2024-08-31T01:00Z",
                ]
            ),
            "type": [0, 0, 0, 0, 0, 0, 0, 1],
            "confidence": ["n", "h", "n", "n", "n", "n", "l", "n"],
            "in_bolivia": True,
            "frp": [10, 20, 30, 40, 50, 1000, 2000, 3000],
        }
    )


def synthetic_days():
    return pd.DataFrame(
        {
            "date": pd.date_range("2023-01-01", "2024-09-10"),
            "acquired": True,
        }
    )


def synthetic_features(detections):
    history = DailyHistory(
        synthetic_cells(), detections, synthetic_days(), "2022-09-03", "2024-09-09"
    )
    return history.features("2024-09-02")


def test_synthetic_strict_windows_neighbors_and_recency():
    result = synthetic_features(synthetic_history_detections()).set_index("cell_id")
    assert result.loc["a", "count_last_7d"] == 2
    assert result.loc["b", "count_last_7d"] == 0
    assert result.loc["b", "days_since_last"] == 8
    assert result.loc["a", "frp_sum_28d"] == 30
    assert result.loc["a", "same_week_last_year"] == 1
    assert result.loc["a", "neighborhood_3x3_last_28d"] == 3
    assert result.loc["a", "neighborhood_7x7_last_28d"] == 4
    assert result.loc["a", "national_last_7d"] == 2
    assert result.loc["a", "national_last_28d"] == 4
    assert result.feature_window_complete.all()
    assert result.window_complete_28d.all()


@pytest.mark.parametrize("mutation", ["delete", "perturb"])
def test_synthetic_future_detections_cannot_change_any_feature(mutation):
    detections = synthetic_history_detections()
    expected = synthetic_features(detections)
    future = detections.acq_datetime >= pd.Timestamp("2024-09-02", tz="UTC")
    if mutation == "delete":
        detections = detections[~future]
    else:
        detections.loc[future, "frp"] = 999999
        detections.loc[future, "cell_id"] = "b"
        detections = pd.concat([detections, detections[future]] * 2, ignore_index=True)
        # Repeat only future rows; historical rows retain their exact multiplicity.
        detections = pd.concat(
            [
                synthetic_history_detections()[~future],
                detections[detections.acq_datetime >= pd.Timestamp("2024-09-02", tz="UTC")],
            ],
            ignore_index=True,
        )
    pd.testing.assert_frame_equal(expected, synthetic_features(detections))


def test_synthetic_missing_warmup_is_flagged_and_recency_capped():
    empty = synthetic_history_detections().iloc[:0]
    history = DailyHistory(synthetic_cells(), empty, synthetic_days(), "2022-01-01", "2023-01-03")
    result = history.features("2023-01-02")
    assert not result.feature_window_complete.any()
    assert not result.window_complete_28d.any()
    assert result.no_prior_detection.all()
    assert (result.days_since_last == 730).all()


def test_synthetic_old_known_detection_is_capped_but_not_marked_unknown():
    detections = synthetic_history_detections().iloc[:1].copy()
    detections["acq_datetime"] = pd.to_datetime(["2021-01-01T00:00Z"])
    result = synthetic_features(detections).set_index("cell_id")
    assert result.loc["a", "days_since_last"] == 730
    assert not result.loc["a", "no_prior_detection"]
    assert result.loc["b", "no_prior_detection"]


def test_synthetic_national_history_includes_boundary_point_in_ineligible_cell():
    detections = synthetic_history_detections().iloc[:1].copy()
    detections["cell_id"] = "boundary_only"
    result = synthetic_features(detections)
    assert (result.national_last_7d == 1).all()
    assert (result.count_last_7d == 0).all()


def test_synthetic_climatology_excludes_own_year_and_wraps_weeks():
    labels = pd.DataFrame(
        {
            "cell_id": ["a"] * 4,
            "issue_date": pd.to_datetime(["2019-01-07", "2020-01-06", "2020-12-21", "2021-01-04"]),
            "partition": ["train", "train", "train", "val_select"],
            "y_primary": pd.array([0, 1, 1, 1], dtype="Int8"),
            "geo_holdout": False,
        }
    )
    queries = pd.DataFrame(
        {"cell_id": ["a", "a"], "issue_date": pd.to_datetime(["2020-01-06", "2021-01-04"])}
    )
    actual = train_climatology(labels, queries, train_years=range(2013, 2021))
    np.testing.assert_allclose(actual, [0, 2 / 3])
    labels.loc[labels.issue_date.dt.year == 2020, "y_primary"] = 0
    changed = train_climatology(labels, queries, train_years=range(2013, 2021))
    assert changed[0] == actual[0]


def test_synthetic_panel_streams_week_rows_with_partial_targets(tmp_path):
    from bolivia_fire.panel import build_panel

    paths = Paths(tmp_path)
    processed, interim = paths.datasets / "processed", paths.datasets / "interim"
    processed.mkdir(parents=True)
    interim.mkdir(parents=True)
    cells = synthetic_cells().assign(
        santa_cruz=False, department="Synthetic", national_area_km2=1.0
    )
    cells.to_parquet(processed / "cells.parquet", index=False)
    detections = synthetic_history_detections()
    detections.loc[detections.cell_id.eq("b"), "acq_datetime"] = pd.Timestamp(
        "2021-01-01", tz="UTC"
    )
    detections.to_parquet(interim / "detections.parquet", index=False)
    days = synthetic_days().assign(bbox_detection_count=1)
    days.to_parquet(interim / "acquisition_days.parquet", index=False)
    summary = build_panel(paths, load_protocol(), "2024-09-02", "2024-09-09")
    result = pd.read_parquet(processed / "panel" / "year=2024" / "part.parquet")
    assert summary["rows"] == 6
    assert result.groupby("issue_date").size().tolist() == [3, 3]
    assert result.partition.eq("test").all()
    assert result.geo_holdout.sum() == 2
    assert result.loc[result.issue_date.eq("2024-09-02"), "target_complete"].all()
    assert result.loc[result.issue_date.eq("2024-09-09"), "y_primary"].isna().all()
    assert result.train_climatology.isna().all()
    assert not result.loc[result.cell_id.eq("b"), "no_prior_detection"].any()
    assert (result.loc[result.cell_id.eq("b"), "days_since_last"] == 730).all()


def test_pipeline_cli_registers_without_requiring_a_firms_key():
    from bolivia_fire.cli import _parser

    args = _parser().parse_args(["build", "panel", "--start-issue", "2024-09-02"])
    assert args.start_issue == pd.Timestamp("2024-09-02").date()
    args = _parser().parse_args(["explain-label", "r1450c2336", "2024-09-02"])
    assert args.cell_id == "r1450c2336"
