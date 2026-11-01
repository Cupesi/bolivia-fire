from __future__ import annotations

import json

import numpy as np
import pyarrow as pa
import pytest
import yaml
from conftest import refresh_synthetic_registration
from sklearn.dummy import DummyClassifier

from bolivia_fire.evaluation.artifacts import save_candidate
from bolivia_fire.extension.runner import Extension
from bolivia_fire.firms.storage import sha256_file
from bolivia_fire.models.data import PanelData
from bolivia_fire.models.estimators import Candidate


def test_synthetic_selection_never_touches_test_and_preserves_unavailable_groups(
    synthetic_registration,
    monkeypatch,
):
    paths, blobs = synthetic_registration
    contract_path = paths.root / "configs/extension_vegetation.yaml"
    contract = yaml.safe_load(contract_path.read_text("utf-8"))
    for entry in contract["comparators"].values():
        directory = paths.root / "artifacts/models" / entry["selection"].split("selection_")[1][:-5]
        candidate = Candidate(
            entry["selected_model"],
            DummyClassifier(strategy="prior").fit(
                np.zeros((4, len(entry["features"]))), [0, 1, 0, 1]
            ),
            entry["features"],
            calibration_status="fitted",
        )
        model = directory / f"{candidate.name}.joblib"
        calibrator = directory / f"{candidate.name}.calibrator.joblib"
        save_candidate(model, candidate)
        save_candidate(calibrator, None)
        selection = paths.root / entry["selection"]
        selection.write_text(json.dumps({"selected_model": candidate.name}), encoding="utf-8")
        entry.update(
            selection_sha256=sha256_file(selection),
            model_sha256=sha256_file(model),
            calibrator_sha256=sha256_file(calibrator),
        )
    contract_path.write_text(yaml.safe_dump(contract, sort_keys=False), encoding="utf-8")
    refresh_synthetic_registration(paths, blobs)
    events = []

    class SyntheticInputs:
        input_bytes = 0
        hashes = {"configs/experiments.yaml": sha256_file(paths.root / "configs/experiments.yaml")}
        available = {
            run["run_id"]: {
                "status": (
                    "not_evaluated" if run["status"] == "not_evaluated" else "evaluated_eligible"
                )
            }
            for run in contract["run_manifest"]
        }

        def __init__(self, *args):
            pass

        def validate(self):
            pass

        def close(self):
            pass

        def load(self, run, stage, resources, **options):
            assert stage in {"train", "val_select", "val_calib", "test"}
            if stage == "test" and options.get("labels", True):
                log = json.loads(
                    (paths.root / "artifacts/evaluation/final_test_log.json").read_text("utf-8")
                )
                assert log[-1]["state"] == "opened", "Labels accessed before durable opening"
            events.append((run.get("run_id"), stage))
            names = run["ordered_features"]
            n = 24
            year = {"train": 2013, "val_select": 2021, "val_calib": 2022, "test": 2024}[stage]
            rng = np.random.default_rng(4)
            x = rng.normal(size=(n, len(names))).astype(np.float32)
            y = np.arange(n) % 2
            return PanelData(
                x,
                y,
                pa.table(
                    {
                        "cell_id": np.arange(n),
                        "issue_date": [f"{year}-01-07"] * n,
                        "national_area_km2": np.ones(n),
                        "santa_cruz": np.arange(n) % 3 == 0,
                        "block_id": np.arange(n) % 4,
                    }
                ),
                names,
                {"eligible": n},
            )

    monkeypatch.setattr("bolivia_fire.extension.runner.FrozenInputs", SyntheticInputs)
    extension = Extension(paths, "synthetic_plan.json")
    try:
        report = extension.select()
        assert len(events) > 0
        assert all(stage != "test" for _, stage in events), "Test rows touched by selection"
        assert len(report["runs"]) == 10
        assert sum(value["status"] == "not_evaluated" for value in report["runs"].values()) == 6
        assert report["hypotheses"]["H4"]["status"] == "not_evaluated"
        assert report["hypotheses"]["H5"]["status"] == "not_evaluated"
        assert report["hypotheses"]["H3"]["status"] == "inconclusive"
        assert len(report["ordered_configurations"]) == 20
        selected = report["runs"]["retrospective_vegetation_lag16"]
        assert selected["selected_model"] == "lightgbm_00"
        assert selected["selection_rule"] == "uncalibrated val_select average_precision"
        assert not (paths.root / "artifacts/evaluation/final_test_log.json").exists()
        with pytest.raises(ValueError, match="frozen"):
            extension.select()
        with pytest.raises(ValueError, match="explicit"):
            extension.score(scope="test", explicit=False, reason="synthetic")
        scored = extension.score(scope="test", explicit=True, reason="synthetic exploratory test")
        assert scored["classification"] == "exploratory"
        assert len(scored["runs"]) == 10
        assert len(scored["lag_sensitivity"]) == 2
        assert scored["runs"]["retrospective_vegetation_lag16"]["rows"] == 24
        log = json.loads(
            (paths.root / "artifacts/evaluation/final_test_log.json").read_text("utf-8")
        )
        assert [entry["state"] for entry in log] == ["opened", "completed"]
    finally:
        extension.close()
