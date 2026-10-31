from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import yaml

from bolivia_fire.config import REPO_ROOT, Paths
from bolivia_fire.firms.storage import sha256_file


def synthetic_panel(root: Path, rows_per_split: int = 300) -> None:
    (root / "configs").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO_ROOT / "configs" / "protocol.yaml", root / "configs" / "protocol.yaml")
    shutil.copyfile(
        REPO_ROOT / "configs" / "experiments.yaml", root / "configs" / "experiments.yaml"
    )
    config = yaml.safe_load((root / "configs" / "experiments.yaml").read_text())
    rng = np.random.default_rng(53)
    for split, year in (("train", 2020), ("val_select", 2021), ("val_calib", 2022), ("test", 2023)):
        n = rows_per_split
        counts = rng.poisson(1, n).astype(np.float32)
        frame = pd.DataFrame({name: counts for name in config["strict_features"]})
        frame[config["columns"]["climatology_cell"]] = (counts * 0.2 + 0.1).clip(0, 0.95)
        frame[config["columns"]["climatology_department"]] = (counts * 0.1 + 0.2).clip(0, 0.95)
        frame["week_of_year_sin"] = -0.5
        frame["week_of_year_cos"] = 0.5
        frame["no_prior_detection"] = counts == 0
        frame["any_last_7d"] = counts > 0
        frame["y_primary"] = (rng.random(n) < (0.1 + counts * 0.18)).astype(float)
        frame["y_allconf"] = np.maximum(frame.y_primary, rng.random(n) < 0.2)
        frame["partition"] = split
        cells_per_week = max(100, int(np.ceil(n / 50)))
        synthetic_cell = np.arange(n) % cells_per_week
        frame["geo_holdout"] = synthetic_cell % 20 < 4
        frame[config["columns"]["completeness_28d"]] = True
        frame[config["columns"]["low_coverage"]] = np.arange(n) % 17 == 0
        frame[config["columns"]["completeness_365d"]] = np.arange(n) % 3 == 0
        frame["santa_cruz"] = synthetic_cell % 2 == 0
        frame["national_area_km2"] = 10 + np.arange(n) % 3
        frame["block_id"] = [f"b{index % 20}" for index in synthetic_cell]
        frame["cell_id"] = [f"synthetic_{index % cells_per_week:05d}" for index in range(n)]
        frame["issue_date"] = pd.date_range(f"{year}-01-01", periods=50, freq="W-MON")[
            np.arange(n) // cells_per_week
        ]
        frame.loc[21, "y_primary"] = np.nan
        frame.loc[25, config["columns"]["completeness_28d"]] = False
        frame.loc[3, "partition"] = None
        destination = root / "datasets" / "processed" / "panel" / f"year={year}" / "part.parquet"
        destination.parent.mkdir(parents=True)
        frame.to_parquet(destination, index=False)


def command(root: Path, *argv: str) -> int:
    from bolivia_fire.experiments_cli import register

    parser = argparse.ArgumentParser()
    register(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(list(argv))
    return args.pipeline_handler(args, Paths(root))


def synthetic_chirps(root: Path) -> None:
    covariates = root / "datasets/processed/covariates"
    covariates.mkdir(parents=True, exist_ok=True)
    manifest = covariates / "chirps_manifest.jsonl"
    manifest.write_text('{"synthetic": true}\n')
    for source in (root / "datasets/processed/panel").glob("year=*/part.parquet"):
        frame = pd.read_parquet(source)[["cell_id", "issue_date"]]
        for name, value in {
            "rain_sum_7d": 7.0, "rain_sum_30d": 30.0, "rain_sum_90d": 90.0,
            "dry_days_30d": 2.0, "rain_anomaly_90d": 1.0,
        }.items():
            frame[name] = value
        for name in (
            "rain_complete_7d", "rain_complete_30d", "rain_complete_90d",
            "rain_climatology_complete",
        ):
            frame[name] = True
        table = pa.Table.from_pandas(frame, preserve_index=False).replace_schema_metadata({
            b"product": (
                b"CHIRPS v3.0 final daily rnl (ERA5 disaggregation), native 0.05 degree COG"
            ),
            b"train_years": json.dumps(list(range(2013, 2021))).encode(),
            b"reference_rule": b"training years only, reference window end <= issue",
            b"protocol_sha256": sha256_file(root / "configs/protocol.yaml").encode(),
            b"manifest_sha256": sha256_file(manifest).encode(),
        })
        destination = covariates / "chirps_features" / source.parent.name / "part.parquet"
        destination.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, destination)


def test_synthetic_cli_workflow_guard_and_prediction(tmp_path: Path) -> None:
    synthetic_panel(tmp_path)
    assert command(tmp_path, "train", "--variant", "strict") == 0
    assert command(tmp_path, "select", "--variant", "strict") == 0
    selection = json.loads((tmp_path / "artifacts/evaluation/selection_strict.json").read_text())
    assert len(selection["candidates"]) == 23
    assert selection["exclusions"]["train"]["missing_label"] == 1
    assert selection["exclusions"]["train"]["incomplete_28d"] == 1
    assert selection["exclusions"]["train"]["geographic_holdout"] > 0
    with pytest.raises(ValueError, match="open-final-test"):
        command(tmp_path, "evaluate")
    assert not (tmp_path / "artifacts/evaluation/final_test_log.json").exists()
    assert command(tmp_path, "evaluate", "--open-final-test") == 0
    report = json.loads((tmp_path / "artifacts/evaluation/test_strict.json").read_text())
    assert "2023" in report["per_year"]
    assert report["geographic_holdout"]["rows"] > 0
    assert report["bootstrap"]["temporal"]["replicates"] == 500
    assert len(report["candidate_bootstrap"]["temporal"]) == 4
    assert report["geographic_holdout"]["feature_set"] == "strict_geo"
    assert "train_climatology_cell" not in report["geographic_holdout"]["features"]
    assert "H1" in report["hypotheses"]
    log = json.loads((tmp_path / "artifacts/evaluation/final_test_log.json").read_text())
    assert len(log) == 1 and len(log[0]["protocol_sha256"]) == 64
    assert len(log[0]["panel_snapshot_sha256"]) == 64
    assert len(log[0]["experiments_sha256"]) == 64
    assert len(log[0]["calibrator_ids"]) == 8
    with pytest.raises(ValueError, match="reopen-reason"):
        command(tmp_path, "evaluate", "--open-final-test")
    assert (
        command(
            tmp_path,
            "evaluate",
            "--open-final-test",
            "--reopen-reason",
            "synthetic guard verification",
        )
        == 0
    )
    log = json.loads((tmp_path / "artifacts/evaluation/final_test_log.json").read_text())
    assert len(log) == 2
    assert log[1]["reopening"] is True
    assert log[1]["reopen_reason"] == "synthetic guard verification"
    assert command(tmp_path, "predict", "--split", "test") == 0
    predictions = pd.read_parquet(tmp_path / "artifacts/predictions/strict/test.parquet")
    assert set(predictions.columns) == {"cell_id", "issue_date", "model", "p", "y"}
    assert predictions.p.between(0, 1).all()
    assert predictions.model.str.startswith("strict_geo/").any()


def test_selection_never_scans_test_labels_and_is_poison_invariant(tmp_path: Path) -> None:
    synthetic_panel(tmp_path)
    command(tmp_path, "train")
    command(tmp_path, "select")
    output = tmp_path / "artifacts/evaluation/selection_strict.json"
    before = json.loads(output.read_text())
    test_file = tmp_path / "datasets/processed/panel/year=2023/part.parquet"
    frame = pd.read_parquet(test_file)
    frame["y_primary"] = np.nan
    frame.to_parquet(test_file, index=False)
    command(tmp_path, "train")
    command(tmp_path, "select")
    after = json.loads(output.read_text())
    assert before["selected_model"] == after["selected_model"]
    assert before["candidates"] == after["candidates"]


def test_sensitivity_features_and_missing_retrospective_columns(tmp_path: Path) -> None:
    from bolivia_fire.models.data import experiment_features

    synthetic_panel(tmp_path)
    config = yaml.safe_load((tmp_path / "configs/experiments.yaml").read_text())
    assert "train_climatology_cell" not in experiment_features(config, "strict", "climatology")
    assert "train_climatology_cell" not in experiment_features(config, "strict_geo", None)
    assert "train_climatology_dept" in experiment_features(config, "strict_geo", None)
    assert not any(
        name.startswith("national_") for name in experiment_features(config, "strict", "national")
    )
    config["retrospective_features"] = ["rain_sum_7d"]
    (tmp_path / "configs/experiments.yaml").write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="rain_sum_7d"):
        command(tmp_path, "train", "--variant", "retrospective")
    assert (
        command(tmp_path, "train", "--selection", "all_confidence", "--drop-group", "neighborhood")
        == 0
    )
    assert (tmp_path / "artifacts/models/strict_all_confidence_drop_neighborhood/run.json").exists()


def test_panel_scan_excludes_test_and_purged_labels(tmp_path: Path) -> None:
    from bolivia_fire.models.data import PanelStore

    synthetic_panel(tmp_path)
    with PanelStore(Paths(tmp_path)) as store:
        tables = list(store.batches(["val_select"], ["partition", "y_primary"]))
    assert len(tables) > 0
    assert {value for table in tables for value in table["partition"].to_pylist()} == {"val_select"}


def test_changed_training_snapshot_requires_retraining(tmp_path: Path) -> None:
    synthetic_panel(tmp_path)
    command(tmp_path, "train")
    path = tmp_path / "datasets/processed/panel/year=2020/part.parquet"
    frame = pd.read_parquet(path)
    frame.loc[10, "count_last_7d"] += 1
    frame.to_parquet(path, index=False)
    with pytest.raises(ValueError, match="changed"):
        command(tmp_path, "select")


@pytest.mark.parametrize("variant", ["strict", "strict_geo"])
def test_synthetic_strict_training_ignores_chirps_manifest_appends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variant: str,
) -> None:
    import bolivia_fire.experiments as experiments

    synthetic_panel(tmp_path)
    manifest = tmp_path / "datasets/processed/covariates/chirps_manifest.jsonl"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"synthetic": "initial"}\n')
    original_fit = experiments.fit_logistic

    def fit_with_chirps_append(*args: Any, **kwargs: Any) -> experiments.Candidate:
        candidate = original_fit(*args, **kwargs)
        with manifest.open("a", encoding="utf-8") as stream:
            stream.write('{"synthetic": "appended during training"}\n')
        return candidate

    monkeypatch.setattr(experiments, "fit_logistic", fit_with_chirps_append)
    experiment = experiments.Experiment(Paths(tmp_path), variant=variant)
    report = experiment.train()
    assert len(report["candidates"]) == 23
    assert manifest.read_text().count("\n") == 2
    assert not any("covariates" in path for path in report["dataset_snapshot_hashes"])
    selection = experiment.select()
    assert selection["run_token"] == report["run_token"]
    assert selection["dataset_snapshot_hashes"] == report["dataset_snapshot_hashes"]


def test_synthetic_low_coverage_exclusion_is_counted(tmp_path: Path) -> None:
    from bolivia_fire.models.data import PanelStore, experiment_features

    synthetic_panel(tmp_path)
    config = yaml.safe_load((tmp_path / "configs/experiments.yaml").read_text())
    with PanelStore(Paths(tmp_path)) as store:
        data = store.load(
            ["train"],
            experiment_features(config, "strict", None),
            "y_primary",
            exclude_low_coverage=True,
        )
        assert data.exclusions["target_low_coverage"] > 0
        assert not data.column("target_low_coverage").any()


@pytest.mark.parametrize("extra", [[], ["count_last_7d"]])
def test_synthetic_identical_retrospective_inputs_refuse_fit_and_leave_h2_null(
    tmp_path: Path, extra: list[str],
) -> None:
    synthetic_panel(tmp_path)
    config_path = tmp_path / "configs/experiments.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["retrospective_features"] = extra
    config_path.write_text(yaml.safe_dump(config))
    for stage in ("train", "select"):
        for variant in ("retrospective", "retrospective_geo"):
            with pytest.raises(ValueError, match="additional"):
                command(tmp_path, stage, "--variant", variant)
    assert not (tmp_path / "artifacts/models/retrospective/run.json").exists()
    command(tmp_path, "train")
    command(tmp_path, "select")
    command(tmp_path, "evaluate", "--open-final-test")
    report = json.loads((tmp_path / "artifacts/evaluation/test_strict.json").read_text())
    assert report["hypotheses"]["H2"] == {
        "supported": None, "reason": "retrospective_features_not_additional",
    }


@pytest.mark.parametrize("defect", ["missing", "incomplete", "unverified", None])
@pytest.mark.parametrize("variant", ["strict", "retrospective"])
def test_synthetic_h2_requires_complete_verified_retrospective_rows(
    tmp_path: Path, defect: str | None, variant: str,
) -> None:
    synthetic_panel(tmp_path)
    synthetic_chirps(tmp_path)
    for fitted in ("strict", "retrospective"):
        command(tmp_path, "train", "--variant", fitted)
        command(tmp_path, "select", "--variant", fitted)
    source = tmp_path / "datasets/processed/covariates/chirps_features/year=2023/part.parquet"
    table = pq.read_table(source)
    if defect == "missing":
        table = table.drop(["rain_sum_7d"])
    elif defect == "incomplete":
        values = [True] * len(table)
        values[10] = False
        table = table.set_column(
            table.schema.get_field_index("rain_complete_90d"),
            "rain_complete_90d", pa.array(values),
        )
    elif defect == "unverified":
        table = table.replace_schema_metadata({})
    pq.write_table(table, source)
    command(tmp_path, "evaluate", "--variant", variant, "--open-final-test")
    report = json.loads((tmp_path / f"artifacts/evaluation/test_{variant}.json").read_text())
    h2 = report["hypotheses"]["H2"]
    if defect is None:
        assert isinstance(h2["supported"], bool)
        assert h2["reason"] is None
        assert "strict_retrospective_bootstrap" in report
    else:
        assert h2 == {
            "supported": None,
            "reason": {
                "missing": "retrospective_columns_missing",
                "incomplete": "retrospective_covariates_incomplete",
                "unverified": "retrospective_provenance_invalid",
            }[defect],
        }
        assert "strict_retrospective_bootstrap" not in report


@pytest.mark.parametrize("defect", ["missing", "null", "incomplete", "unverified"])
def test_synthetic_retrospective_train_and_select_refuse_invalid_covariates(
    tmp_path: Path, defect: str,
) -> None:
    from bolivia_fire.experiments import Experiment

    synthetic_panel(tmp_path)
    synthetic_chirps(tmp_path)
    experiment = Experiment(Paths(tmp_path), variant="retrospective")
    experiment.train()
    source = tmp_path / "datasets/processed/covariates/chirps_features/year=2021/part.parquet"
    table = pq.ParquetFile(source).read()
    if defect == "missing":
        table = table.drop(["rain_sum_7d"])
    elif defect == "unverified":
        table = table.replace_schema_metadata({})
    else:
        name = "rain_sum_7d" if defect == "null" else "rain_complete_7d"
        values = table[name].to_pylist()
        values[10] = None if defect == "null" else False
        table = table.set_column(table.schema.get_field_index(name), name, pa.array(values))
    pq.write_table(table, source)
    for stage in ("train", "select"):
        with pytest.raises(ValueError, match="Retrospective.*rain_sum_7d|Retrospective.*CHIRPS"):
            command(tmp_path, stage, "--variant", "retrospective")


def test_calibration_labels_do_not_change_selected_candidate(tmp_path: Path) -> None:
    synthetic_panel(tmp_path)
    command(tmp_path, "train")
    command(tmp_path, "select")
    output = tmp_path / "artifacts/evaluation/selection_strict.json"
    before = json.loads(output.read_text())
    path = tmp_path / "datasets/processed/panel/year=2022/part.parquet"
    frame = pd.read_parquet(path)
    frame["y_primary"] = 1 - frame.y_primary
    frame.to_parquet(path, index=False)
    command(tmp_path, "train")
    command(tmp_path, "select")
    after = json.loads(output.read_text())
    assert before["selected_model"] == after["selected_model"]
    assert before["selected_lightgbm"] == after["selected_lightgbm"]


def test_synthetic_root_cli_experiment_workflow(tmp_path: Path) -> None:
    from bolivia_fire.cli import main

    synthetic_panel(tmp_path)
    for argv in (
        ["train"],
        ["select"],
        ["evaluate", "--open-final-test"],
        ["predict", "--split", "test"],
    ):
        assert main(argv, root=tmp_path) == 0
    output = tmp_path / "artifacts/predictions/strict/test.parquet"
    assert output.exists()
    assert len(pd.read_parquet(output)) > 0
