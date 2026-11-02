from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from bolivia_fire.notebook_helpers import (
    extension_evaluation,
    extension_feature_example,
    extension_selections,
)


def write_synthetic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def table(section: Any, title: str) -> pd.DataFrame:
    return next(frame for name, frame in section.tables if title in name)


def test_synthetic_missing_extension_outputs_are_pending(tmp_path: Path) -> None:
    for section in (
        extension_feature_example(tmp_path, "synthetic", "2024-09-02"),
        extension_selections(tmp_path),
        extension_evaluation(tmp_path),
    ):
        assert not section.tables
        assert section.messages
        assert all("pending" in message.lower() for message in section.messages)


def test_synthetic_feature_example_reads_dates_coverage_qa_and_fractions(tmp_path: Path) -> None:
    base = tmp_path / "datasets/processed/vegetation"
    path = base / "features/lag=16/geography=temporal/year=2024/part.parquet"
    path.parent.mkdir(parents=True)
    pd.DataFrame(
        [{
            "cell_id": "synthetic", "issue_date": pd.Timestamp("2024-09-02"),
            "veg_ndvi_latest": 0.4, "veg_evi_latest": 0.3,
            "veg_valid_pixel_fraction": 0.75, "veg_observation_age_days": 32.0,
            "composite_start": pd.Timestamp("2024-07-27"),
            "composite_end_boundary": pd.Timestamp("2024-08-12"),
            "oldest_observation": pd.Timestamp("2024-07-30"),
            "latest_observation": pd.Timestamp("2024-08-01"),
            "vegetation_available": False, "vegetation_lag_days": 16,
        }]
    ).to_parquet(path, index=False)
    pd.DataFrame([{"cell_id": "synthetic", "national_area_km2": 20.0}]).to_parquet(
        tmp_path / "datasets/processed/cells.parquet", index=False
    )
    composite = base / "composites/2024209.parquet"
    composite.parent.mkdir()
    pd.DataFrame([{
        "cell_id": "synthetic", "ndvi": 0.4, "evi": 0.3,
        "valid_fraction": 0.75, "oldest_day": 19934, "latest_day": 19936,
    }]).to_parquet(composite, index=False)
    write_synthetic_json(base / "source.json", {"composites": {"2024209": {"bundles": [{
        "start": "2024-07-27", "end_boundary": "2024-08-12",
    }]}}})
    write_synthetic_json(composite.with_suffix(".audit.json"), {
        "accepted_pixels": 3, "qa_rejected_pixels": 1, "scale_applied_count": 1,
    })
    land = tmp_path / "datasets/processed/landcover/mapbiomas_2023.parquet"
    land.parent.mkdir()
    pd.DataFrame([{
        "cell_id": "synthetic", "year": 2023, "map_year": 2023, "available": True,
        "mapped_area_fraction": 1.0, "mapbiomas_forest_fraction": 0.6,
        "mapbiomas_nonforest_fraction": 0.1, "mapbiomas_farming_fraction": 0.2,
        "mapbiomas_nonvegetated_fraction": 0.05, "mapbiomas_water_fraction": 0.05,
    }]).to_parquet(land, index=False)

    section = extension_feature_example(tmp_path, "synthetic", "2024-09-02")
    features = table(section, "Recorded vegetation")
    assert features.iloc[0]["veg_ndvi_latest"] == 0.4
    assert features.iloc[0]["vegetation_available"] == False  # noqa: E712
    dates = table(section, "Availability")
    assert dates.iloc[0]["available_from_utc"] == pd.Timestamp("2024-08-28", tz="UTC")
    assert dates.iloc[0]["oldest_observation_age_days"] == 34.0
    areas = table(section, "Area weighting")
    assert areas.iloc[0]["national_cell_area_km2"] == 20.0
    assert areas.iloc[0]["accepted_area_km2"] == 15.0
    assert table(section, "QA audit").iloc[0]["qa_rejected_pixels"] == 1
    assert table(section, "Land-cover").iloc[0]["mapbiomas_forest_fraction"] == 0.6


def test_synthetic_feature_example_never_substitutes_another_issue(tmp_path: Path) -> None:
    path = tmp_path / (
        "datasets/processed/vegetation/features/lag=16/geography=temporal/year=2024/part.parquet"
    )
    path.parent.mkdir(parents=True)
    pd.DataFrame([{
        "cell_id": "synthetic", "issue_date": pd.Timestamp("2024-08-26"),
        "veg_ndvi_latest": 0.7,
    }]).to_parquet(path, index=False)
    section = extension_feature_example(tmp_path, "synthetic", "2024-09-02")
    assert not section.tables
    assert any("pending" in message.lower() for message in section.messages)


@pytest.fixture
def synthetic_selection(tmp_path: Path) -> dict[str, Any]:
    record = {
        "hypotheses": {"H3": {"status": "inconclusive", "reason": "no_detected_gain"},
                       "H4": {"status": "not_evaluated", "reason": "unavailable"}},
        "runs": {"synthetic_lag16": {
            "run": {"run_id": "synthetic_lag16", "hypothesis": "H3",
                    "geography": "temporal", "availability_lag_days": 16, "role": "primary"},
            "status": "evaluated_eligible", "selected_model": "synthetic_model",
            "calibration_status": "fitted", "search": [{"name": "synthetic_model",
                "val_select_uncalibrated_ap": 0.24}],
            "validation": {
                "rows": 17, "positives": 3, "prevalence": 3 / 17,
                "row_key_sha256": "synthetic-row-hash",
                "calibrated": {"average_precision": 0.25},
                "comparator": {"average_precision": 0.2},
                "bootstrap": {"replicates": 500, "seed": 7, "blocks": 12,
                    "paired_difference": {"average_precision": {
                        "lower": -0.03, "upper": 0.1, "reason": None}}},
                "decision": {"status": "inconclusive", "reason": "no_detected_gain"},
            },
            "support": {"val_calib": {"synthetic_lag16": {
                "eligible_before_intersection": 20, "paired_rows": 17,
                "lost_to_common_support": 3, "row_key_sha256": "synthetic-row-hash"}}},
        }},
    }
    write_synthetic_json(tmp_path / "artifacts/evaluation/extension_selection.json", record)
    return record


def test_synthetic_selection_and_evaluation_preserve_recorded_values(
    tmp_path: Path, synthetic_selection: dict[str, Any],
) -> None:
    selected = extension_selections(tmp_path)
    assert table(selected, "Selection records").iloc[0]["selected_model"] == "synthetic_model"
    evaluated = extension_evaluation(tmp_path)
    assert table(evaluated, "H3/H4/H5").iloc[1]["status"] == "not_evaluated"
    intervals = table(evaluated, "Paired validation")
    assert intervals.iloc[0]["paired_ap_lower"] == -0.03
    assert intervals.iloc[0]["paired_ap_upper"] == 0.1
    assert intervals.iloc[0]["rows"] == 17
    support = table(evaluated, "Identical-row")
    assert support.iloc[0]["lost_to_common_support"] == 3
    assert support.iloc[0]["row_key_sha256"] == "synthetic-row-hash"


def test_synthetic_per_run_selection_naming_is_supported(
    tmp_path: Path, synthetic_selection: dict[str, Any],
) -> None:
    directory = tmp_path / "artifacts/evaluation"
    (directory / "extension_selection.json").unlink()
    write_synthetic_json(directory / "selection_extension_synthetic.json",
                         synthetic_selection["runs"]["synthetic_lag16"])
    assert table(extension_selections(tmp_path), "Selection records").iloc[0][
        "selected_model"
    ] == "synthetic_model"
    decision = table(extension_evaluation(tmp_path), "H3/H4/H5").iloc[0]
    assert decision["hypothesis"] == "H3"
    assert decision["status"] == "inconclusive"


@pytest.mark.parametrize("logged", [False, True])
def test_synthetic_test_results_require_a_matching_logged_reopening(
    tmp_path: Path, synthetic_selection: dict[str, Any], logged: bool,
) -> None:
    directory = tmp_path / "artifacts/evaluation"
    write_synthetic_json(directory / "extension_test_synthetic.json", {
        "scope": "test", "classification": "exploratory", "opening_id": "synthetic-opening",
        "selection_sha256": "synthetic-selection-hash", "runs": {
            "synthetic_lag16": synthetic_selection["runs"]["synthetic_lag16"]["validation"]},
    })
    if logged:
        write_synthetic_json(directory / "final_test_log.json", [{
            "type": "extension_test", "state": "opened", "opening_id": "synthetic-opening",
            "reopening": True, "classification": "exploratory", "reason": "synthetic reason",
            "selection_sha256": "synthetic-selection-hash", "timestamp_utc": "synthetic-time",
        }])
    section = extension_evaluation(tmp_path)
    test_tables = [frame for title, frame in section.tables if "Logged exploratory test" in title]
    if logged:
        assert test_tables[0].iloc[0]["calibrated_ap"] == 0.25
        assert table(section, "Logged reopening").iloc[0]["reason"] == "synthetic reason"
    else:
        assert not test_tables
        assert any("pending" in message.lower() for message in section.messages)


@pytest.mark.parametrize("notebook_name", ["02_features", "03_modeling", "04_evaluation"])
def test_synthetic_extension_cells_run_when_all_outputs_are_absent(
    tmp_path: Path, notebook_name: str,
) -> None:
    path = Path(__file__).parents[1] / "notebooks" / f"{notebook_name}.ipynb"
    notebook = json.loads(path.read_text(encoding="utf-8"))
    messages: list[str] = []
    namespace = {"root": tmp_path, "cell_id": "synthetic", "issue": "2024-09-02",
                 "display": lambda value: messages.append(str(value)), "Markdown": str}
    cells = [cell for cell in notebook["cells"] if cell["cell_type"] == "code"
             and "extension-vegetation" in cell.get("metadata", {}).get("tags", [])]
    assert cells
    for cell in cells:
        exec("".join(cell["source"]), namespace)


def test_synthetic_undefined_interval_stays_null(
    tmp_path: Path, synthetic_selection: dict[str, Any],
) -> None:
    report = synthetic_selection["runs"]["synthetic_lag16"]["validation"]
    report["bootstrap"]["paired_difference"]["average_precision"] = {
        "lower": None, "upper": None, "reason": "undefined_in_all_replicates",
    }
    write_synthetic_json(tmp_path / "artifacts/evaluation/extension_selection.json",
                         synthetic_selection)
    row = table(extension_evaluation(tmp_path), "Paired validation").iloc[0]
    assert row["paired_ap_lower"] is None
    assert row["paired_ap_upper"] is None
    assert row["interval_reason"] == "undefined_in_all_replicates"


def test_synthetic_logged_test_retains_unavailable_group_reason(
    tmp_path: Path, synthetic_selection: dict[str, Any],
) -> None:
    directory = tmp_path / "artifacts/evaluation"
    opening = {
        "type": "extension_test", "state": "opened", "opening_id": "synthetic-opening",
        "classification": "exploratory", "reopening": True, "reason": "synthetic reason",
        "selection_sha256": "synthetic-selection-hash",
    }
    write_synthetic_json(directory / "final_test_log.json", [opening])
    write_synthetic_json(directory / "extension_test_synthetic.json", {
        "classification": "exploratory", "opening_id": "synthetic-opening",
        "selection_sha256": "synthetic-selection-hash",
        "runs": {"synthetic_H4": {"status": "not_evaluated", "reason": "unavailable"}},
    })
    row = table(extension_evaluation(tmp_path), "Logged exploratory test").iloc[0]
    assert row["status"] == "not_evaluated"
    assert row["reason"] == "unavailable"


def test_synthetic_amendment_replaces_unavailable_runs_and_keeps_original_h3(
    tmp_path: Path, synthetic_selection: dict[str, Any],
) -> None:
    directory = tmp_path / "artifacts/evaluation"
    synthetic_selection["runs"]["synthetic_land_cover"] = {
        "run": {"run_id": "synthetic_land_cover", "hypothesis": "H4"},
        "status": "not_evaluated", "reason": "unavailable",
    }
    write_synthetic_json(directory / "extension_selection.json", synthetic_selection)
    amended_run = {
        "run": {"run_id": "synthetic_land_cover", "hypothesis": "H4",
                "geography": "temporal", "role": "primary"},
        "status": "evaluated_eligible", "selected_model": "synthetic_amended_model",
        "validation": {"rows": 23, "calibrated": {"average_precision": 0.3}},
    }
    amendment = directory / "amendments/synthetic-amendment/extension_selection.json"
    write_synthetic_json(amendment, {
        "hypotheses": {
            "H3": {"status": "inconclusive", "reason": "no_detected_gain"},
            "H4": {"status": "inconclusive", "reason": "no_detected_gain"},
        },
        "runs": {"synthetic_land_cover": amended_run},
    })
    write_synthetic_json(directory / "extension_archive_attempt_002/extension_selection.json", {
        "runs": {"synthetic_archived": amended_run},
    })

    selected = table(extension_selections(tmp_path), "Selection records").set_index("run_id")
    assert set(selected.index) == {"synthetic_lag16", "synthetic_land_cover"}
    assert selected.loc["synthetic_land_cover", "selected_model"] == "synthetic_amended_model"
    assert selected.loc["synthetic_land_cover", "artifact"] == (
        "artifacts/evaluation/amendments/synthetic-amendment/extension_selection.json"
    )
    evaluated = extension_evaluation(tmp_path)
    decisions = table(evaluated, "H3/H4/H5").set_index("hypothesis")
    assert decisions.index.is_unique
    assert decisions.loc["H3", "status"] == "inconclusive"
    assert decisions.loc["H4", "status"] == "inconclusive"
    intervals = table(evaluated, "Paired validation").set_index("run_id")
    assert intervals.loc["synthetic_land_cover", "calibrated_ap"] == 0.3
    assert intervals.loc["synthetic_lag16", "calibrated_ap"] == 0.25


def test_synthetic_amendment_test_uses_global_opening_and_shows_confirmation_refusal(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "artifacts/evaluation"
    amendment = directory / "amendments/synthetic-amendment"
    write_synthetic_json(directory / "final_test_log.json", [{
        "type": "extension_test", "state": "opened", "opening_id": "synthetic-opening",
        "classification": "exploratory", "reopening": True, "reason": "synthetic reason",
        "selection_sha256": "synthetic-selection-hash",
    }])
    write_synthetic_json(amendment / "extension_test_synthetic.json", {
        "classification": "exploratory", "opening_id": "synthetic-opening",
        "selection_sha256": "synthetic-selection-hash", "runs": {
            "synthetic_land_cover": {"rows": 29, "calibrated": {"average_precision": 0.35}},
        },
    })
    write_synthetic_json(directory / "extension_test_original.json", {
        "classification": "exploratory", "opening_id": "synthetic-opening",
        "selection_sha256": "synthetic-selection-hash", "runs": {
            "synthetic_vegetation": {"rows": 31, "calibrated": {"average_precision": 0.2}},
            "synthetic_land_cover": {"status": "not_evaluated", "reason": "unavailable"},
        },
    })
    write_synthetic_json(amendment / "extension_confirmation_guard.json", {
        "status": "refused", "reason": "synthetic missing prerequisite",
        "blockers": ["synthetic unavailable archive"], "confirmation_opening_consumed": False,
    })

    evaluated = extension_evaluation(tmp_path)
    tests = pd.concat([frame for title, frame in evaluated.tables
                       if title.startswith("Logged exploratory test")]).set_index("run_id")
    assert tests.index.is_unique
    assert tests.loc["synthetic_vegetation", "calibrated_ap"] == 0.2
    row = tests.loc["synthetic_land_cover"]
    assert row["calibrated_ap"] == 0.35
    assert row["rows"] == 29
    assert row["artifact"] == (
        "artifacts/evaluation/amendments/synthetic-amendment/extension_test_synthetic.json"
    )
    guard = table(evaluated, "Recorded confirmation guards").iloc[0]
    assert guard["status"] == "refused"
    assert guard["blockers"] == ["synthetic unavailable archive"]
    assert guard["confirmation_opening_consumed"] == False  # noqa: E712
