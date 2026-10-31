from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from bolivia_fire.comparison.data import load_split
from bolivia_fire.comparison.runtime import Resources, Study, now, read_json
from bolivia_fire.firms.storage import sha256_file


def determinism_audit(study: Study) -> dict[str, Any]:
    from catboost import _catboost

    reference = read_json(study.result_path("reference"))
    arrays = {}
    for name, expected in reference["array_sha256"].items():
        observed = sha256_file(study.cache / "data" / name)
        arrays[name] = {
            "source_sha256": expected,
            "rerun_sha256": observed,
            "identical": expected == observed,
        }
    trials = read_json(study.result_path("catboost_trials"))
    parameters = {}
    for trial in trials:
        original = reference["catboost_trials"][trial["index"]]
        parameters[str(trial["index"])] = {
            "identical": trial["parameters"] == original["parameters"],
            "source_ap": original["val_select_ap"],
            "rerun_ap": trial["val_select_ap"],
            "ap_difference": trial["val_select_ap"] - original["val_select_ap"],
        }
    binary_hash = sha256_file(Path(_catboost.__file__))
    result = {
        "array_identity": arrays,
        "trial_parameters": parameters,
        "source_binary_sha256": reference["catboost_binary_sha256"],
        "rerun_binary_sha256": binary_hash,
        "catboost_binary_identical": binary_hash == reference["catboost_binary_sha256"],
        "interpretation": (
            "Different GPU scores with identical data, parameters and binary are consistent "
            "with documented reduction nondeterminism; the frozen search is unchanged. "
            "Any observed data or binary differences are reported separately."
        ),
        "reference": "https://catboost.ai/docs/en/features/training-on-gpu",
    }
    study.save("determinism_audit", result)
    return result


def cpu_check(study: Study, *, budget_seconds: int = 600) -> dict[str, Any]:
    from catboost import CatBoostClassifier

    study.require_unfrozen()
    selected = read_json(study.result_path("catboost_selected"))["selected_trial"]
    parameters = {
        **selected["parameters"],
        "task_type": "CPU",
        "iterations": selected["best_round"],
    }
    parameters.pop("devices", None)
    with Resources(study, "catboost_cpu_reproducibility_check") as monitor:
        train, validation = load_split(study, "train"), load_split(study, "val_select")
        tick = time.perf_counter()

        class BudgetCallback:
            def after_iteration(self, info: Any) -> bool:
                monitor.check()
                return time.perf_counter() - tick < budget_seconds

        model = CatBoostClassifier(**parameters)
        model.fit(train.x, train.y, callbacks=[BudgetCallback()])
        path = study.models / "catboost_cpu_check.cbm"
        model.save_model(str(path))
        prediction = model.predict_proba(validation.x, thread_count=4)[:, 1].astype(np.float32)
        restored = CatBoostClassifier()
        restored.load_model(str(path))
        reloaded = restored.predict_proba(validation.x, thread_count=4)[:, 1].astype(np.float32)
        if not np.array_equal(prediction, reloaded):
            raise ValueError("CPU audit model prediction roundtrip is not exact")
        result = {
            "recorded_utc": now(),
            "parameters": parameters,
            "source_gpu_trial": selected["index"],
            "fixed_gpu_tree_count": selected["best_round"],
            "fitted_trees": model.tree_count_,
            "completed": model.tree_count_ == selected["best_round"],
            "budget_seconds": budget_seconds,
            "val_select": validation.score(prediction),
            "model_sha256": sha256_file(path),
            "prediction_roundtrip_exact": True,
            "reference": "Frozen GPU search; CPU audit is not a selection candidate",
            "scope": "One CPU refit at the GPU-selected tree count; no retuning or test scoring",
            "resources": monitor.close(),
        }
        study.save("cpu_check", result)
        return result
