from __future__ import annotations

import json
import tracemalloc
import weakref

import numpy as np
import psutil
import pyarrow as pa
import pytest
import yaml
from conftest import commit_synthetic_amendment, refresh_synthetic_registration, write_json
from sklearn.dummy import DummyClassifier

from bolivia_fire.evaluation.artifacts import save_candidate
from bolivia_fire.extension.runner import Extension
from bolivia_fire.firms.storage import sha256_file
from bolivia_fire.models.data import PanelData
from bolivia_fire.models.estimators import Candidate


def test_synthetic_amended_fit_restarts_and_preserves_base_metrics(
    synthetic_registration, monkeypatch
):
    from bolivia_fire.extension.access import check_hashes
    from bolivia_fire.extension.amendment import (
        authorize_availability,
        metric_artifacts,
        verify_amendment,
    )
    from bolivia_fire.extension.registration import verify_registration

    paths, blobs = synthetic_registration
    directory = paths.root / "artifacts/evaluation"
    h3 = "retrospective_vegetation_lag16"
    identity = "retrospective_land_cover"
    write_json(
        directory / f"extension_checkpoint_{h3}.json",
        {"run_id": h3, "completed_configurations": 1, "results": [{"average_precision": 0.4}]},
    )
    write_json(
        directory / "extension_selection.json",
        {
            "runs": {h3: {"status": "evaluated_eligible"}},
            "hypotheses": {"H3": {"status": "inconclusive"}},
        },
    )
    commit_synthetic_amendment(paths, blobs)
    registration = verify_registration(paths)
    baseline = metric_artifacts(registration)
    base_bytes = {path: path.read_bytes() for path in directory.glob("*.json")}

    class SyntheticAmendedInputs:
        input_bytes = 0

        def __init__(self, registration, plan_path):
            self.amendment = authorize_availability(registration, {identity, h3})
            self.hashes = {
                "configs/experiments.yaml": sha256_file(paths.root / "configs/experiments.yaml"),
                **self.amendment["prior_artifact_hashes"],
            }
            self.available = {
                run["run_id"]: {
                    "status": "evaluated_eligible"
                    if run["run_id"] in {identity, h3}
                    else "not_evaluated"
                }
                for run in registration.runs
            }

        def validate(self):
            check_hashes(registration, self.hashes)
            verify_amendment(registration)

        def close(self):
            pass

        def release(self, *panels):
            pass

        def load(self, run, stage, resources, **options):
            assert run["run_id"] != h3, "Preserved H3 rows must never be reloaded"
            names = run["ordered_features"]
            n = 32
            year = {"train": 2013, "val_select": 2021, "val_calib": 2022, "test": 2024}[stage]
            return PanelData(
                np.random.default_rng(4).normal(size=(n, len(names))).astype(np.float32),
                np.arange(n) % 2,
                pa.table(
                    {
                        "cell_id": np.arange(n),
                        "issue_date": [f"{year}-01-07"] * n,
                        "national_area_km2": np.ones(n),
                        "block_id": np.arange(n) % 4,
                    }
                ),
                names,
                {"eligible": n},
            )

        plan_support = load

    def synthetic_comparators(experiment):
        for name, entry in experiment.contract["comparators"].items():
            experiment.comparators[name] = Candidate(
                "synthetic",
                DummyClassifier().fit(np.zeros((4, len(entry["features"]))), [0, 1, 0, 1]),
                entry["features"],
                calibration_status="fitted",
            )

    monkeypatch.setattr("bolivia_fire.extension.runner.FrozenInputs", SyntheticAmendedInputs)
    monkeypatch.setattr(Extension, "_load_comparators", synthetic_comparators)
    first = Extension(paths, "synthetic_plan.json")
    first.configurations = first.configurations[:1]
    first.contract["estimation"]["search"].update(boosting_rounds=5, early_stopping_rounds=2)
    select_geography = first._select_geography

    def synthetic_interruption(geography, reports):
        select_geography(geography, reports)
        raise RuntimeError("synthetic interruption after completed fit")

    monkeypatch.setattr(first, "_select_geography", synthetic_interruption)
    try:
        with pytest.raises(RuntimeError, match="synthetic interruption"):
            first.select()
        checkpoint = first.evaluation_directory / f"extension_checkpoint_{identity}.json"
        assert checkpoint.is_file()
        assert json.loads(checkpoint.read_text("utf-8"))["completed_configurations"] == 1
        assert not (directory / checkpoint.name).exists()
    finally:
        first.close()
    assert metric_artifacts(registration) == baseline
    assert verify_amendment(registration)

    restarted = Extension(paths, "synthetic_plan.json")
    restarted.configurations = restarted.configurations[:1]
    monkeypatch.setattr(
        "bolivia_fire.extension.runner.lgb.train",
        lambda *args, **kwargs: pytest.fail("A completed amended fit must be resumed"),
    )
    try:
        selected = restarted.select()
        assert selected["runs"][identity]["status"] == "evaluated_eligible"
        assert restarted._frozen()["selection_sha256"] == sha256_file(restarted.selection_path)
        with restarted._resources("synthetic_scoring_preflight") as resources:
            planned, _ = restarted._family("temporal", "test", resources, labels=False)
        assert set(planned) == {identity}
        assert restarted.selection_path.parent == checkpoint.parent
        assert verify_amendment(restarted.registration)
        assert metric_artifacts(restarted.registration) == baseline
        assert all(path.read_bytes() == data for path, data in base_bytes.items())
    finally:
        restarted.close()


def test_synthetic_amended_families_preserve_h3_and_use_separate_common_rows(
    synthetic_registration,
):
    from types import SimpleNamespace

    from bolivia_fire.extension.registration import verify_registration

    paths, _ = synthetic_registration
    registration = verify_registration(paths)
    extension = Extension.__new__(Extension)
    extension.registration = registration
    extension.runs = registration.runs
    extension.contract = registration.contract
    calls = []

    def synthetic_load(run, stage, resources, **kwargs):
        identity = run["run_id"]
        calls.append(identity)
        if "vegetation" in identity and "land_cover" not in identity:
            pytest.fail("Amended execution attempted to reload preserved H3 rows")
        cells = [1, 2] if "vegetation_land_cover" in identity else [1, 2, 3]
        return PanelData(
            np.ones((len(cells), 1)),
            np.zeros(len(cells)),
            pa.table({"cell_id": cells, "issue_date": ["2021-01-04"] * len(cells)}),
            ["synthetic"],
            {"eligible": len(cells)},
        )

    extension.source = SimpleNamespace(
        amendment={"amendment_id": "synthetic-amendment"},
        available={run["run_id"]: {"status": "evaluated_eligible"} for run in extension.runs},
        load=synthetic_load,
        release=lambda *args: None,
    )
    families, _ = extension._family(
        "temporal",
        "val_select",
        SimpleNamespace(check=lambda **kwargs: None),
    )
    assert not any("vegetation_lag" in identity for identity in calls)
    assert families["retrospective_land_cover"]["retrospective_land_cover"].column(
        "cell_id"
    ).tolist() == [1, 2, 3]
    combined = "retrospective_vegetation_land_cover_lag16"
    assert families[combined][combined].column("cell_id").tolist() == [1, 2]


@pytest.mark.parametrize(
    "combined,check_lifetime",
    [
        ((False, False), False),
        ((True, False), False),
        ((False, True), False),
        ((True, True), False),
        ((False, False), True),
    ],
)
def test_synthetic_selection_never_touches_test_and_preserves_unavailable_groups(
    synthetic_registration,
    monkeypatch,
    combined,
    check_lifetime,
):
    paths, blobs = synthetic_registration
    use_override = combined == (True, False)
    if use_override:
        monkeypatch.setenv("BOLIVIA_FIRE_EXTENSION_MEMORY_LIMIT_GIB", "20")
    else:
        monkeypatch.delenv("BOLIVIA_FIRE_EXTENSION_MEMORY_LIMIT_GIB", raising=False)
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
    training_arrays = []
    temporal_arrays = []
    dataset_refs = []

    def available(run):
        if run["run_id"].startswith("retrospective_vegetation_land_cover_lag"):
            return combined[run["availability_lag_days"] == 32]
        return run["status"] != "not_evaluated"

    class SyntheticInputs:
        input_bytes = 0
        hashes = {"configs/experiments.yaml": sha256_file(paths.root / "configs/experiments.yaml")}
        available = {
            run["run_id"]: {"status": ("evaluated_eligible" if available(run) else "not_evaluated")}
            for run in contract["run_manifest"]
        }

        def __init__(self, *args):
            pass

        def validate(self):
            pass

        def close(self):
            pass

        def release(self, *panels):
            pass

        def plan_support(self, run, stage, resources, **options):
            return self.load(run, stage, resources, labels=False, **options)

        def load(self, run, stage, resources, **options):
            if check_lifetime and stage == "train":
                assert all(ref() is None for ref in training_arrays), "Previous train retained"
                assert all(ref() is None for ref in dataset_refs), "Previous dataset retained"
            if check_lifetime and run.get("geography") == "geographic":
                assert all(ref() is None for ref in temporal_arrays), "Temporal frame retained"
            assert stage in {"train", "val_select", "val_calib", "test"}
            if stage == "test" and options.get("labels", True):
                log = json.loads(
                    (paths.root / "artifacts/evaluation/final_test_log.json").read_text("utf-8")
                )
                assert log[-1]["state"] == "opened", "Labels accessed before durable opening"
            events.append((run.get("run_id"), stage))
            names = run["ordered_features"]
            n = 50_000 if check_lifetime else 24
            year = {"train": 2013, "val_select": 2021, "val_calib": 2022, "test": 2024}[stage]
            rng = np.random.default_rng(4)
            x = rng.normal(size=(n, len(names))).astype(np.float32)
            if stage == "train":
                training_arrays.append(weakref.ref(x))
            elif run.get("geography") == "temporal":
                temporal_arrays.append(weakref.ref(x))
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
    if check_lifetime:
        from bolivia_fire.extension import runner

        original_support = runner.common_support
        original_datasets = runner.lightgbm_datasets

        def tracked_support(panels):
            result = original_support(panels)
            if not any("geo" in (identity or "") for identity, _ in events):
                temporal_arrays.extend(weakref.ref(data.x) for data in result[0].values())
            return result

        def tracked_datasets(*args):
            result = original_datasets(*args)
            dataset_refs.extend(weakref.ref(data) for data in result)
            return result

        monkeypatch.setattr(runner, "common_support", tracked_support)
        monkeypatch.setattr(runner, "lightgbm_datasets", tracked_datasets)
        monkeypatch.setattr(
            runner,
            "paired_report",
            lambda *args, **kwargs: {"decision": {"status": "inconclusive"}},
        )
        monkeypatch.setattr(runner, "compare_lags", lambda *args, **kwargs: {})
    extension = Extension(paths, "synthetic_plan.json")
    if check_lifetime:
        extension.configurations = extension.configurations[:1]
        initial_rss = psutil.Process().memory_info().rss
        tracemalloc.start()
    try:
        report = extension.select()
        assert report["effective_memory_limit_gib"] == (20 if use_override else 12)
        assert report["registered_memory_limit_gib"] == 12
        assert report["memory_limit_source"] == (
            "BOLIVIA_FIRE_EXTENSION_MEMORY_LIMIT_GIB" if use_override else "registered_contract"
        )
        assert len(events) > 0
        assert all(stage != "test" for _, stage in events), "Test rows touched by selection"
        assert len(report["runs"]) == 10
        assert sum(value["status"] == "not_evaluated" for value in report["runs"].values()) == (
            6 - sum(combined)
        )
        assert report["hypotheses"]["H4"]["status"] == "not_evaluated"
        if not combined[0]:
            assert report["hypotheses"]["H5"]["status"] == "not_evaluated"
        elif not combined[1]:
            assert (
                report["lag_sensitivity"]["retrospective_vegetation_land_cover_lag16"]["status"]
                == "not_evaluated"
            )
        assert report["hypotheses"]["H3"]["status"] == "inconclusive"
        assert len(report["ordered_configurations"]) == (1 if check_lifetime else 20)
        if check_lifetime:
            assert all(ref() is None for ref in training_arrays)
            assert all(ref() is None for ref in temporal_arrays)
            assert all(ref() is None for ref in dataset_refs)
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            matrix_bytes = 50_000 * 36 * 4
            assert peak < matrix_bytes * 32
            rss_peak = max(
                json.loads(path.read_text("utf-8"))["peak_process_tree_rss_bytes"]
                for path in (paths.root / "artifacts/evaluation").glob("extension_resources_*.json")
            )
            assert rss_peak - initial_rss < matrix_bytes * 48 + 64 * 1024**2
            print(
                f"synthetic sequential fits: rows=50000 fits=4 peak_traced_bytes={peak} "
                f"initial_rss_bytes={initial_rss} peak_rss_bytes={rss_peak}"
            )
            return
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
        assert len(scored["lag_sensitivity"]) == 2 + int(all(combined))
        assert scored["runs"]["retrospective_vegetation_lag16"]["rows"] == 24
        log = json.loads(
            (paths.root / "artifacts/evaluation/final_test_log.json").read_text("utf-8")
        )
        assert [entry["state"] for entry in log] == ["opened", "completed"]
    finally:
        if check_lifetime and tracemalloc.is_tracing():
            tracemalloc.stop()
        extension.close()


def test_synthetic_released_datasets_preserve_fitted_outputs(synthetic_registration):
    import copy
    import time
    from types import SimpleNamespace

    import joblib
    import lightgbm as lgb

    from bolivia_fire.comparison.runtime import average_precision
    from bolivia_fire.extension.registration import verify_registration
    from bolivia_fire.extension.resources import Resources
    from bolivia_fire.models.estimators import lightgbm_datasets, search_parameters

    paths, _ = synthetic_registration
    reg = verify_registration(paths)
    extension = Extension.__new__(Extension)
    extension.registration, extension.paths = reg, paths
    extension.contract = copy.deepcopy(reg.contract)
    extension.contract["estimation"]["search"].update(
        boosting_rounds=25, early_stopping_rounds=5, num_threads=2
    )
    settings = extension.contract["estimation"]["search"]
    extension.configurations = search_parameters(seed=7, space=settings["space"])[:2]
    extension.memory_limit_gib, extension.memory_limit_source = 12, "registered_contract"
    extension.tick = time.perf_counter()
    extension.models = paths.root / "artifacts/models/extension"
    run = reg.runs[0]
    names = run["ordered_features"]
    rng = np.random.default_rng(441)

    def panel(n, year):
        x = rng.normal(size=(n, len(names))).astype(np.float32)
        y = (x[:, 0] + x[:, 1] * 0.3 > 0).astype(np.float32)
        metadata = pa.table(
            {
                "cell_id": np.arange(n),
                "issue_date": [f"{year}-01-07"] * n,
                "national_area_km2": np.ones(n),
                "block_id": np.arange(n) % 4,
            }
        )
        return PanelData(x, y, metadata, names, {"eligible": n})

    train, selection, calibration = panel(4096, 2013), panel(512, 2021), panel(512, 2022)
    baseline = Candidate(
        "synthetic", DummyClassifier().fit(train.x, train.y), names, calibration_status="fitted"
    )
    extension.comparators = {run["comparator"]: baseline, run["contextual_comparator"]: baseline}
    extension.comparator_training_counts = {}
    extension.source = SimpleNamespace(
        hashes={"configs/experiments.yaml": sha256_file(paths.root / "configs/experiments.yaml")},
        available={run["run_id"]: {}},
        validate=lambda: None,
    )
    # Reference retains both raw arrays and datasets, as the previous implementation did.
    datasets = lightgbm_datasets(train.x, train.y, selection.x, selection.y, names)
    reference_candidates = []
    for index, configuration in enumerate(extension.configurations):
        parameters = {
            "objective": "binary",
            "metric": "average_precision",
            "verbosity": -1,
            "seed": 7,
            "deterministic": True,
            "force_col_wise": True,
            "num_threads": settings["num_threads"],
            "max_bin": settings["max_bin"],
            "feature_pre_filter": False,
            **configuration,
        }
        estimator = lgb.train(
            parameters,
            datasets[0],
            num_boost_round=settings["boosting_rounds"],
            valid_sets=[datasets[1]],
            callbacks=[lgb.early_stopping(settings["early_stopping_rounds"], verbose=False)],
        )
        candidate = Candidate(f"lightgbm_{index:02d}", estimator, names)
        reference_candidates.append(
            (average_precision(selection.y, candidate.predict(selection.x)), candidate)
        )
    _, reference = min(reference_candidates, key=lambda item: (-item[0], item[1].name))
    reference.calibrate(calibration.x, calibration.y)
    with Resources(reg, "synthetic_identical") as resources:
        report = extension._fit(
            run,
            train,
            selection,
            calibration,
            resources,
            {
                "calibration_panels": {
                    run["comparator"]: calibration,
                    run["contextual_comparator"]: calibration,
                }
            },
        )
    fitted = joblib.load(paths.root / report["model_path"])
    assert report["selected_model"] == reference.name
    assert [entry["val_select_uncalibrated_ap"] for entry in report["search"]] == [
        ap for ap, _ in reference_candidates
    ]
    assert len(np.unique(reference.predict(calibration.x))) > 10
    assert fitted.predict(calibration.x).tobytes() == reference.predict(calibration.x).tobytes()
    assert (
        fitted.predict(calibration.x, calibrated=True).tobytes()
        == reference.predict(calibration.x, calibrated=True).tobytes()
    )
    assert fitted.estimator.model_to_string() == reference.estimator.model_to_string()
    checkpoint = paths.root / f"artifacts/evaluation/extension_fit_{run['run_id']}.json"
    report.pop("implementation_hashes")
    checkpoint.write_text(json.dumps(report), encoding="utf-8")
    with (
        Resources(reg, "synthetic_old_implementation") as resources,
        pytest.raises(ValueError, match="different implementation"),
    ):
        extension._fit(run, train, selection, calibration, resources, {})
