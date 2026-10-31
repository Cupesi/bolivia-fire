from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from matplotlib import font_manager
from PIL import Image
from shapely.geometry import box

from bolivia_fire import figures


def test_chart_style_uses_times_and_ordered_serif_fallbacks() -> None:
    assert figures.STYLE["font.family"] == "serif"
    assert figures.STYLE["font.serif"] == [
        "Times New Roman", "Liberation Serif", "TeX Gyre Termes", "DejaVu Serif"
    ]
    assert figures.STYLE["mathtext.fontset"] == "stix"


def test_chart_font_falls_back_to_available_serif(monkeypatch: pytest.MonkeyPatch) -> None:
    serif = font_manager.findfont(font_manager.FontProperties(family=["DejaVu Serif"]))
    monkeypatch.setattr(figures.font_manager, "findfont", lambda *args, **kwargs: serif)
    with figures.chart_style() as resolved:
        assert resolved == "DejaVu Serif"


def test_static_export_rejects_sans_serif(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, synthetic_dataset: tuple
) -> None:
    sans = font_manager.findfont(font_manager.FontProperties(family=["DejaVu Sans"]))
    monkeypatch.setattr(figures.font_manager, "findfont", lambda *args, **kwargs: sans)
    data = figures.collect_dataset(*synthetic_dataset)
    with pytest.raises(RuntimeError, match="serif.*DejaVu Sans"):
        figures.write_dataset_figures(
            data, box(-65, -18, -62, -16), {}, artifact_directory=tmp_path
        )
    assert not list(tmp_path.glob("*.png"))


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
    )
    assert set(outputs) == {"map", "weekly", "seasonal", "partitions"}
    for name, output in outputs.items():
        assert output.name == f"dataset_{name}.png"
        assert 0 < output.stat().st_size < 300_000
        with Image.open(output) as image:
            assert image.info["FontFamily"] in figures.STYLE["font.serif"]
            assert image.info["MathTextFontSet"] == "stix"
            assert image.width >= 1200
            assert image.info["dpi"][0] == pytest.approx(300, abs=1)
            from bolivia_fire.animations import clear_border

            assert clear_border(image)
    assert not list(tmp_path.rglob("*.pdf"))


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


@pytest.fixture
def synthetic_results_root(tmp_path: Path) -> Path:
    directory = tmp_path / "artifacts" / "evaluation"
    directory.mkdir(parents=True)
    models = ("train_climatology", "persistence", "logistic_regression", "lightgbm_03")
    metrics = {}
    bootstrap = {}
    for index, model in enumerate(models):
        versions, intervals = {}, {}
        for version in ("uncalibrated", "calibrated"):
            ap = round(0.1 * (index + 1) + (0.01 if version == "calibrated" else 0), 2)
            versions[version] = {
                "average_precision": ap,
                "recall_at_area_budget_5pct": ap + 0.1,
                "recall_at_area_budget_10pct": ap + 0.2,
                "rows": 20,
                "missing_probability": 0,
                "reliability": [
                    {"bin": 0, "count": 10.0, "mean_probability": 0.01, "positive_rate": 0.02},
                    {"bin": 1, "count": 10.0, "mean_probability": 0.3, "positive_rate": 0.25},
                ],
            }
            intervals[version] = {
                "replicates": 500,
                "blocks": 24,
                "confidence_level": 0.95,
                "paired_rows": 20,
                "metrics": {
                    "average_precision": {
                        "lower": round(ap - 0.04, 2),
                        "upper": round(ap + 0.06, 2),
                    }
                },
                "paired_difference": {"average_precision": {"lower": -0.03, "upper": 0.07}},
            }
        metrics[model], bootstrap[model] = versions, intervals
    report = {
        "split": "test",
        "variant": "strict",
        "selected_model": "lightgbm_03",
        "best_baseline": "persistence",
        "models": metrics,
        "per_year": {str(year): metrics for year in (2023, 2024, 2025)},
        "geographic_holdout": {
            "feature_set": "strict_geo",
            "selected_model": "lightgbm_03",
            "best_baseline": "train_climatology",
            "models": metrics,
        },
        "candidate_bootstrap": {"temporal": bootstrap, "geographic": bootstrap},
        "strict_retrospective_bootstrap": {
            "paired_difference": {"average_precision": {"lower": -0.01, "upper": 0.02}},
            "replicates": 500,
            "paired_rows": 20,
        },
        "hypotheses": {"H2": {"supported": True, "reason": None}},
        "final_test_opening": {"timestamp_utc": "2026-01-01T00:00:00+00:00"},
        "protocol_sha256": "synthetic-protocol",
        "experiments_sha256": "synthetic-config",
        "model_ids": {
            f"{variant}/{model}": f"synthetic-{model}"
            for variant in ("strict", "strict_geo")
            for model in models
        },
        "calibrator_ids": {
            f"{variant}/{model}": f"synthetic-calibrator-{model}"
            for variant in ("strict", "strict_geo")
            for model in models
        },
    }
    (directory / "test_strict.json").write_text(json.dumps(report))
    for variant, baseline in (("strict", "persistence"), ("strict_geo", "train_climatology")):
        selection = {
            "selected_model": "lightgbm_03",
            "selected_lightgbm": "lightgbm_03",
            "best_baseline": baseline,
            "protocol_sha256": "synthetic-protocol",
            "experiments_sha256": "synthetic-config",
            "model_ids": {model: f"synthetic-{model}" for model in models},
            "calibrator_ids": {model: f"synthetic-calibrator-{model}" for model in models},
        }
        (directory / f"selection_{variant}.json").write_text(json.dumps(selection))
    (directory / "final_test_log.json").write_text(json.dumps([report["final_test_opening"]]))
    return tmp_path


def test_synthetic_results_preserve_recorded_values_and_sources(
    synthetic_results_root: Path,
) -> None:
    data = figures.collect_results(synthetic_results_root)
    temporal = data.summary["figures"]["temporal"]
    assert temporal["best_baseline"] == "persistence"
    rows = {row["model"]: row for row in temporal["models"]}
    assert rows["train_climatology"]["average_precision"] == 0.1
    assert rows["logistic_regression"]["average_precision"] == pytest.approx(0.31)
    assert rows["lightgbm_03"]["ap_interval"] == {"lower": 0.37, "upper": 0.47}
    assert rows["lightgbm_03"]["paired_difference"] == pytest.approx(0.21)
    assert rows["lightgbm_03"]["paired_interval"] == {"lower": -0.03, "upper": 0.07}
    assert temporal["source"] == "artifacts/evaluation/test_strict.json"
    assert rows["lightgbm_03"]["probability_version"] == "calibrated"
    assert data.summary["figures"]["geographic"]["best_baseline"] == "train_climatology"
    assert data.summary["figures"]["h2"]["estimate"] is None
    assert data.summary["figures"]["h2"]["interval"] == {"lower": -0.01, "upper": 0.02}
    assert len(data.summary["figures"]["per_year"]["values"]) == 12
    assert data.summary["figures"]["reliability"]["models"][0]["bins"][1]["count"] == 10.0
    assert len(data.summary["figures"]["area_budget"]["populations"]) == 2
    assert synthetic_results_root.as_posix() not in json.dumps(data.summary)


def test_synthetic_results_reject_mismatched_selection(synthetic_results_root: Path) -> None:
    path = synthetic_results_root / "artifacts/evaluation/selection_strict.json"
    selection = json.loads(path.read_text())
    selection["selected_model"] = "logistic_regression"
    path.write_text(json.dumps(selection))
    with pytest.raises(ValueError, match="selection"):
        figures.collect_results(synthetic_results_root)


def test_synthetic_results_do_not_invent_paired_point_on_different_support(
    synthetic_results_root: Path,
) -> None:
    path = synthetic_results_root / "artifacts/evaluation/test_strict.json"
    report = json.loads(path.read_text())
    report["candidate_bootstrap"]["temporal"]["lightgbm_03"]["calibrated"]["paired_rows"] = 18
    path.write_text(json.dumps(report))
    row = figures.collect_results(synthetic_results_root).summary["figures"]["temporal"]["models"][
        -1
    ]
    assert row["paired_difference"] is None
    assert row["paired_interval"] == {"lower": -0.03, "upper": 0.07}


@pytest.fixture
def synthetic_example_week(synthetic_dataset: tuple) -> tuple:
    cells = synthetic_dataset[0]
    panel = pd.DataFrame(
        {
            "cell_id": ["a", "b"] * 3,
            "issue_date": pd.to_datetime(
                ["2024-08-05"] * 2 + ["2024-08-12"] * 2 + ["2024-08-19"] * 2
            ),
            "partition": ["test"] * 6,
            "n_primary": [2, 3, 2, 3, 99, 99],
            "y_primary": [1, 1, 1, 1, None, None],
            "target_complete": [True] * 4 + [False] * 2,
        }
    )
    predictions = pd.DataFrame(
        {
            "cell_id": ["a", "b"] * 3,
            "issue_date": panel.issue_date,
            "model": ["strict/lightgbm_03", "strict_geo/lightgbm_03"] * 3,
            "p": [0.1, 0.8, 0.2, 0.7, 0.3, 0.6],
            "y": panel.y_primary,
        }
    )
    return cells, panel, predictions


def test_synthetic_example_selects_complete_peak_week_with_earliest_tie(
    synthetic_example_week: tuple,
) -> None:
    example = figures.collect_results_map(*synthetic_example_week, "lightgbm_03", "lightgbm_03")
    assert example.summary["issue_date"] == "2024-08-05"
    assert example.summary["detections"] == 5
    assert example.summary["positive_cells"] == 2
    assert example.summary["derived"] is True
    assert example.cells.p.tolist() == pytest.approx([0.1, 0.8])


def test_synthetic_results_render_seven_png_only(
    synthetic_results_root: Path,
    synthetic_example_week: tuple,
    tmp_path: Path,
) -> None:
    data = figures.collect_results(synthetic_results_root)
    example = figures.collect_results_map(*synthetic_example_week, "lightgbm_03", "lightgbm_03")
    country = box(-64.1, -17.1, -62.9, -16.9)
    outputs = figures.write_results_figures(
        data,
        example,
        country,
        {"Synthetic department": country},
        artifact_directory=tmp_path / "figures",
    )
    assert set(outputs) == {
        "temporal",
        "reliability",
        "per_year",
        "geographic",
        "area_budget",
        "map",
        "h2",
    }
    for name, output in outputs.items():
        assert output.name == f"results_{name}.png"
        with Image.open(output) as image:
            assert image.width >= 1200
            assert image.info["dpi"][0] == pytest.approx(300, abs=1)
            from bolivia_fire.animations import clear_border

            assert clear_border(image)
    assert not list(tmp_path.rglob("*.pdf"))
