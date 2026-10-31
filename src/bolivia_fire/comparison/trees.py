from __future__ import annotations

import gc
import time
from typing import Any

import numpy as np
from numpy.typing import NDArray

from bolivia_fire.comparison.data import Split, load_split
from bolivia_fire.comparison.runtime import Resources, Study, average_precision, log, now


def train_xgboost(study: Study, train: Split, validation: Split, monitor: Resources) -> list[dict]:
    import xgboost as xgb

    settings = study.config["xgboost"]
    tick = time.perf_counter()
    dtrain = xgb.QuantileDMatrix(
        train.x,
        label=train.y,
        feature_names=study.features,
        max_bin=63,
        nthread=4,
    )
    dvalidation = xgb.QuantileDMatrix(
        validation.x,
        label=validation.y,
        feature_names=study.features,
        max_bin=63,
        ref=dtrain,
        nthread=4,
    )

    def exact_ap(prediction: NDArray, matrix: Any) -> tuple[str, float]:
        return "average_precision", average_precision(validation.y, prediction)

    class BudgetCallback(xgb.callback.TrainingCallback):
        def after_iteration(self, model: Any, epoch: int, evals_log: dict) -> bool:
            monitor.check()
            return time.perf_counter() - tick > study.config["max_family_seconds"]

    trials: list[dict] = []
    best_ap = -1.0
    for index, varied in enumerate(settings["configs"]):
        started = time.perf_counter()
        parameters = {**settings["fixed"], **varied}
        model = xgb.train(
            parameters,
            dtrain,
            num_boost_round=settings["max_rounds"],
            evals=[(dvalidation, "validation")],
            custom_metric=exact_ap,
            maximize=True,
            early_stopping_rounds=settings["early_stopping_rounds"],
            verbose_eval=False,
            callbacks=[BudgetCallback()],
        )
        best_round = model.best_iteration + 1
        model = model[:best_round]
        p = model.predict(dvalidation).astype(np.float32)
        ap = average_precision(validation.y, p)
        trial = {
            "index": index,
            "parameters": parameters,
            "best_round": best_round,
            "val_select_ap": ap,
            "runtime_seconds": time.perf_counter() - started,
        }
        trials.append(trial)
        if ap > best_ap:
            best_ap = ap
            model.save_model(study.models / "xgboost.ubj")
            study.save_prediction("xgboost", "val_select", p)
            study.save(
                "xgboost_importance",
                {
                    "gain": model.get_score(importance_type="gain"),
                    "split_count": model.get_score(importance_type="weight"),
                },
            )
        study.save("xgboost_trials", trials)
        log(f"XGBoost {index + 1}/{len(settings['configs'])}: AP={ap:.8f}, trees={best_round}")
        del model
        gc.collect()
        if time.perf_counter() - tick > study.config["max_family_seconds"]:
            break
    return trials


def train_catboost(study: Study, train: Split, validation: Split, monitor: Resources) -> list[dict]:
    from catboost import CatBoostClassifier, Pool

    settings = study.config["catboost"]
    tick = time.perf_counter()
    train_pool = Pool(train.x, label=train.y, feature_names=study.features)
    validation_pool = Pool(validation.x, label=validation.y, feature_names=study.features)
    trials: list[dict] = []
    best_ap = -1.0
    for index, varied in enumerate(settings["configs"]):
        monitor.check()
        started = time.perf_counter()
        parameters = {**settings["fixed"], **varied}
        model = CatBoostClassifier(**parameters)
        model.fit(train_pool)
        trial_best_ap, best_round, selected_p = -1.0, 0, None
        checkpoints = []
        for step, prediction in enumerate(
            model.staged_predict_proba(
                validation_pool,
                eval_period=settings["exact_ap_checkpoint_every_trees"],
                thread_count=4,
            )
        ):
            p = prediction[:, 1].astype(np.float32)
            ap = average_precision(validation.y, p)
            trees = min(
                (step + 1) * settings["exact_ap_checkpoint_every_trees"], parameters["iterations"]
            )
            checkpoints.append({"trees": trees, "average_precision": ap})
            if ap > trial_best_ap:
                trial_best_ap, best_round, selected_p = ap, trees, p
        model.shrink(best_round)
        trial = {
            "index": index,
            "parameters": parameters,
            "best_round": best_round,
            "val_select_ap": trial_best_ap,
            "runtime_seconds": time.perf_counter() - started,
            "checkpoints": checkpoints,
        }
        trials.append(trial)
        if trial_best_ap > best_ap:
            best_ap = trial_best_ap
            model.save_model(str(study.models / "catboost.cbm"))
            study.save_prediction("catboost", "val_select", selected_p)
            study.save(
                "catboost_importance",
                dict(
                    zip(
                        study.features,
                        model.get_feature_importance().tolist(),
                        strict=True,
                    )
                ),
            )
            study.save("catboost_effective_parameters", model.get_all_params())
        study.save("catboost_trials", trials)
        log(
            f"CatBoost {index + 1}/{len(settings['configs'])}: "
            f"AP={trial_best_ap:.8f}, trees={best_round}"
        )
        del model
        gc.collect()
        if time.perf_counter() - tick > study.config["max_family_seconds"]:
            break
    return trials


def train_trees(study: Study, family: str) -> dict[str, Any]:
    study.require_unfrozen()
    with Resources(study, family) as monitor:
        train = load_split(study, "train")
        validation = load_split(study, "val_select")
        run = train_xgboost if family == "xgboost" else train_catboost
        trials = run(study, train, validation, monitor)
        selected = max(trials, key=lambda item: item["val_select_ap"])
        result = {
            "family": family,
            "selected_utc": now(),
            "selected_trial": selected,
            "completed_configurations": len(trials),
            "planned_configurations": len(study.config[family]["configs"]),
            "selection_source": "val_select exact AP only",
            "resources": monitor.close(),
            "val_select_metrics": validation.score(study.prediction(family, "val_select")),
        }
        study.save(f"{family}_selected", result)
        return result


def predict_tree(study: Study, family: str, split: Split) -> NDArray:
    if family == "xgboost":
        import xgboost as xgb

        model = xgb.Booster()
        model.load_model(study.models / "xgboost.ubj")
        model.set_param({"device": study.config["xgboost"]["fixed"]["device"], "nthread": 4})
        train = load_split(study, "train")
        # Generic DMatrix inference changes float-level ties; preserve the search quantiles.
        reference = xgb.QuantileDMatrix(
            train.x,
            label=train.y,
            feature_names=study.features,
            max_bin=63,
            nthread=4,
        )
        matrix = xgb.QuantileDMatrix(
            split.x,
            feature_names=study.features,
            max_bin=63,
            ref=reference,
            nthread=4,
        )
        return model.predict(matrix).astype(np.float32)
    from catboost import CatBoostClassifier

    model = CatBoostClassifier()
    model.load_model(str(study.models / "catboost.cbm"))
    return model.predict_proba(split.x, thread_count=4)[:, 1].astype(np.float32)
