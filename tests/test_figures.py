from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from PIL import Image
from shapely.geometry import box

from bolivia_fire import figures


@pytest.fixture
def synthetic_dataset() -> tuple:
    cells = pd.DataFrame(
        {
            "cell_id": ["a", "b"],
            "west": [-64.0, -63.0],
            "east": [-63.95, -62.95],
            "south": [-17.0, -17.0],
            "north": [-16.95, -16.95],
            "longitude": [-63.975, -62.975],
            "latitude": [-16.975, -16.975],
            "national_area_km2": [25.0, 20.0],
            "block_id": ["b-64_-17", "b-63_-17"],
            "geo_holdout": [False, True],
        }
    )
    issues = pd.to_datetime(
        ["2020-12-14", "2020-12-21", "2020-12-28", "2021-01-04", "2022-01-03", "2023-01-02"]
    )
    rows = []
    for index, issue in enumerate(issues):
        for cell_index, cell in cells.iterrows():
            complete = index != 1
            positive = int((index + cell_index) % 2 == 0)
            rows.append(
                {
                    "cell_id": cell.cell_id,
                    "issue_date": issue,
                    "partition": ["train", "train", None, "val_select", "val_calib", "test"][index],
                    "geo_holdout": cell.geo_holdout,
                    "y_primary": positive if complete else None,
                    "n_primary": positive,
                    "target_complete": complete,
                    "window_obs_complete_28d": index != 3,
                    "target_low_coverage": index == 4,
                }
            )
    panel = pd.DataFrame(rows)
    days = pd.DataFrame(
        {
            "date": pd.to_datetime(["2020-12-14", "2020-12-15", "2020-12-16", "2020-12-17"]),
            "acquired": [True, False, True, False],
            "outage_hours": [0.0, 0.0, 8.0, 24.0],
            "observed": [True, False, False, False],
        }
    )
    detections = pd.DataFrame(
        {
            "acq_datetime": pd.to_datetime(
                ["2020-12-14", "2020-12-14", "2020-12-15", "2021-01-04"], utc=True
            ),
            "in_bolivia": [True, True, False, True],
            "primary": [True, False, True, True],
            "allconf": [True, True, True, True],
        }
    )
    outages = pd.DataFrame(
        {
            "start_utc": pd.to_datetime(["2020-12-16T04:00Z"], utc=True),
            "end_utc": pd.to_datetime(["2020-12-18T00:00Z"], utc=True),
            "comment": ["Synthetic sensor outage"],
        }
    )
    protocol = {
        "protocol_version": "synthetic",
        "partitions": {
            "train": ["2020-01-01", "2020-12-31"],
            "val_select": ["2021-01-01", "2021-12-31"],
            "val_calib": ["2022-01-01", "2022-12-31"],
            "test": ["2023-01-01", "2023-12-31"],
        },
    }
    return cells, panel, days, detections, outages, protocol


def test_synthetic_summary_has_explicit_counts(synthetic_dataset: tuple, tmp_path: Path) -> None:
    data = figures.collect_dataset(*synthetic_dataset)
    destination = tmp_path / "summary.json"
    summary = figures.write_dataset_summary(data, destination)
    assert json.loads(destination.read_text()) == summary
    assert summary["eligible_cells"] == 2
    assert summary["national_area_km2"] == 45.0
    assert summary["issue_weeks"] == 6
    assert summary["rows_per_partition"] == {
        "train": 4,
        "val_select": 2,
        "val_calib": 2,
        "test": 2,
        "purged": 2,
    }
    train = summary["partition_statistics"]["train"]["non_held_out"]
    assert train == {
        "rows": 2,
        "labelled_rows": 1,
        "positive_rows": 1,
        "labelled_share": 0.5,
        "positive_rate": 1.0,
    }
    assert summary["partition_statistics"]["train"]["held_out"]["positive_rate"] == 0.0
    assert summary["detections_per_year"] == [
        {"year": 2020, "selected": 1, "all_confidence": 2},
        {"year": 2021, "selected": 1, "all_confidence": 1},
    ]
    unobserved = summary["observation_days_per_year"][0]
    assert unobserved["unobserved_days"] == 3
    assert unobserved["missing_acquisition_only"] == 1
    assert unobserved["documented_outage_only"] == 1
    assert unobserved["missing_acquisition_and_outage"] == 1
    assert unobserved["outage_causes"] == {"Synthetic sensor outage": 2}
    excluded = summary["fitting_exclusions"]["train"]
    assert excluded == {
        "candidate_rows": 6,
        "purged_partition": 2,
        "missing_label": 2,
        "geographic_holdout": 1,
        "incomplete_28d": 0,
        "eligible": 1,
        "low_coverage_sensitivity_additional": 0,
    }
    assert summary["fitting_exclusions"]["val_select"]["incomplete_28d"] == 1
    assert summary["fitting_exclusions"]["val_calib"]["low_coverage_sensitivity_additional"] == 1


def test_synthetic_frequency_and_calendar_are_protocol_consistent(synthetic_dataset: tuple) -> None:
    data = figures.collect_dataset(*synthetic_dataset)
    assert data.training_frequency.set_index("cell_id").frequency.to_dict() == {"a": 1.0, "b": 0.0}
    assert data.seasonal_cycle.week_index.between(0, 51).all()
    assert data.issue_calendar.partition.isna().sum() == 1
    weekly = data.weekly_detections.set_index("issue_date")
    assert weekly.loc[pd.Timestamp("2020-12-14"), "selected"] == 1
    assert weekly.loc[pd.Timestamp("2020-12-14"), "all_confidence"] == 2
    changed = list(synthetic_dataset)
    changed[1] = changed[1].copy()
    changed[1].loc[changed[1].partition == "test", "y_primary"] = 1
    alternative = figures.collect_dataset(*changed)
    pd.testing.assert_frame_equal(data.training_frequency, alternative.training_frequency)
    pd.testing.assert_frame_equal(data.weekly_detections, alternative.weekly_detections)


def test_synthetic_artifacts_are_readable_and_small(
    synthetic_dataset: tuple, tmp_path: Path
) -> None:
    data = figures.collect_dataset(*synthetic_dataset)
    country = box(-64.1, -17.1, -62.9, -16.9)
    outputs = figures.write_dataset_figures(
        data,
        country,
        {"Synthetic department": country},
        artifact_directory=tmp_path / "artifacts",
        pdf_directory=tmp_path / "pdf",
    )
    assert set(outputs) == {"map", "weekly", "seasonal", "partitions"}
    for name, output in outputs.items():
        assert output.name == f"dataset_{name}.png"
        assert 0 < output.stat().st_size < 300_000
        with Image.open(output) as image:
            assert image.width >= 1200
            assert image.info["dpi"][0] == pytest.approx(300, abs=1)
        assert (tmp_path / "pdf" / f"dataset_{name}.pdf").read_bytes().startswith(b"%PDF")


def test_synthetic_parquet_inputs_match_frames(synthetic_dataset: tuple, tmp_path: Path) -> None:
    expected = figures.collect_dataset(*synthetic_dataset)
    inputs = list(synthetic_dataset)
    for index, name in enumerate(("cells", "panel", "days", "detections")):
        path = tmp_path / f"{name}.parquet"
        inputs[index].to_parquet(path, index=False)
        inputs[index] = [path] if name == "panel" else path
    actual = figures.collect_dataset(*inputs)
    assert actual.summary == expected.summary
    pd.testing.assert_frame_equal(actual.training_frequency, expected.training_frequency)


def test_synthetic_detection_dates_use_utc(synthetic_dataset: tuple) -> None:
    inputs = list(synthetic_dataset)
    inputs[3] = inputs[3].copy()
    inputs[3].loc[3, "acq_datetime"] = pd.Timestamp("2021-01-01T01:00Z")
    data = figures.collect_dataset(*inputs)
    assert data.summary["detections_per_year"] == [
        {"year": 2020, "selected": 1, "all_confidence": 2},
        {"year": 2021, "selected": 1, "all_confidence": 1},
    ]


def test_synthetic_missing_labels_keep_seasonal_gaps(synthetic_dataset: tuple) -> None:
    data = figures.collect_dataset(*synthetic_dataset)
    seasonal = data.seasonal_cycle.set_index(["partition", "week_index"])
    assert seasonal.loc[("train", 49), "positive_rate"] == 0.5
    assert seasonal.loc[("train", 49), "labelled_rows"] == 2
    assert pd.isna(seasonal.loc[("train", 50), "positive_rate"])
    assert seasonal.loc[("train", 50), "labelled_rows"] == 0
