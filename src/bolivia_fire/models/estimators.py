from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import lightgbm as lgb
import numpy as np
from numpy.typing import NDArray
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

BATCH_SIZE = 100_000


def negative_sample(y: NDArray, *, seed: int) -> tuple[NDArray, NDArray]:
    rng = np.random.default_rng(seed)
    positives, negatives = np.flatnonzero(y == 1), np.flatnonzero(y == 0)
    chosen = rng.choice(negatives, size=max(1, int(np.ceil(len(negatives) * 0.1))), replace=False)
    indices = np.sort(np.r_[positives, chosen])
    return indices, np.where(y[indices] == 0, 10.0, 1.0)


class FeatureTransform:
    def __init__(self, names: list[str]) -> None:
        self.logarithmic = np.array(
            [
                not (
                    name.startswith("week_of_year_")
                    or name.startswith("window_obs_complete_")
                    or name
                    in {
                        "feature_window_complete",
                        "no_prior_detection",
                        "any_last_7d",
                    }
                )
                for name in names
            ]
        )
        self.mean = np.zeros(len(names))
        self.scale = np.ones(len(names))

    def _logged(self, x: NDArray) -> NDArray:
        result = np.array(x, dtype=np.float64, copy=True)
        selected = result[:, self.logarithmic]
        if np.any(selected < 0):
            raise ValueError("Log1p features must be nonnegative")
        result[:, self.logarithmic] = np.log1p(selected)
        return result

    def fit(self, x: NDArray) -> None:
        counts = np.zeros(x.shape[1], dtype=np.int64)
        sums, squares = np.zeros(x.shape[1]), np.zeros(x.shape[1])
        for start in range(0, len(x), BATCH_SIZE):
            batch = self._logged(x[start : start + BATCH_SIZE])
            counts += np.isfinite(batch).sum(axis=0)
            sums += np.nansum(batch, axis=0)
            squares += np.nansum(batch * batch, axis=0)
        self.mean = np.divide(sums, counts, out=np.zeros_like(sums), where=counts > 0)
        variance = np.maximum(0, squares - counts * self.mean**2) / max(1, len(x))
        self.scale = np.where(variance > 1e-16, np.sqrt(variance), 1)

    def transform(self, x: NDArray) -> NDArray:
        values = self._logged(x)
        values = np.where(np.isfinite(values), values, self.mean)
        return ((values - self.mean) / self.scale).astype(np.float32)


@dataclass
class Candidate:
    name: str
    estimator: Any
    features: list[str]
    transformer: FeatureTransform | None = None
    calibrator: IsotonicRegression | None = None
    calibration_status: str = "unfitted"
    parameters: dict[str, Any] = field(default_factory=dict)

    def predict(self, x: NDArray, *, calibrated: bool = False) -> NDArray:
        result = np.empty(len(x), dtype=np.float32)
        for start in range(0, len(x), BATCH_SIZE):
            batch = x[start : start + BATCH_SIZE]
            if self.transformer:
                batch = self.transformer.transform(batch)
            if hasattr(self.estimator, "predict_proba"):
                probability = self.estimator.predict_proba(batch)[:, 1]
            elif isinstance(self.estimator, lgb.Booster):
                probability = self.estimator.predict(batch, num_threads=4)
            else:
                probability = self.estimator.predict(batch)
            if calibrated and self.calibrator is not None:
                finite = np.isfinite(probability)
                probability[finite] = self.calibrator.predict(probability[finite])
            result[start : start + len(batch)] = probability
        return result

    def calibrate(self, x: NDArray, y: NDArray) -> None:
        probability = self.predict(x)
        valid = np.isfinite(probability)
        self.calibrator = None
        if not valid.any():
            self.calibration_status = "no_predictions_identity"
        elif len(np.unique(y[valid])) < 2:
            self.calibration_status = "single_class_identity"
        else:
            self.calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1)
            self.calibrator.fit(probability[valid], y[valid])
            self.calibration_status = "fitted"


def fit_logistic(x: NDArray, y: NDArray, features: list[str], *, seed: int) -> Candidate:
    if len(np.unique(y)) != 2:
        raise ValueError("Logistic training requires both classes")
    transform = FeatureTransform(features)
    transform.fit(x)
    rows, weights = negative_sample(y, seed=seed)
    model = LogisticRegression(solver="lbfgs", max_iter=1000, random_state=seed)
    model.fit(transform.transform(x[rows]), y[rows], sample_weight=weights)
    return Candidate(
        "logistic_regression",
        model,
        features,
        transformer=transform,
        parameters={
            "negative_fraction": 0.1,
            "negative_weight": 10,
            "sampled_rows": len(rows),
            "solver": "lbfgs",
        },
    )


def search_parameters(
    *,
    seed: int,
    space: dict[str, list[Any]] | None = None,
) -> list[dict[str, Any]]:
    rng = np.random.default_rng(seed)
    options = space or {
        "num_leaves": [7, 15, 31, 63],
        "learning_rate": [0.03, 0.06, 0.1],
        "min_data_in_leaf": [50, 200, 1000],
        "feature_fraction": [0.7, 0.85, 1.0],
        "lambda_l2": [0, 1, 10],
    }
    if np.prod([len(set(values)) for values in options.values()]) < 20:
        raise ValueError("Frozen search space must contain at least 20 distinct configurations")
    result: list[dict[str, Any]] = []
    while len(result) < 20:
        value = {name: rng.choice(values).item() for name, values in options.items()}
        if value not in result:
            result.append(value)
    return result


def lightgbm_datasets(
    x: NDArray,
    y: NDArray,
    validation_x: NDArray,
    validation_y: NDArray,
    features: list[str],
) -> tuple[lgb.Dataset, lgb.Dataset]:
    parameters = {"max_bin": 63, "feature_pre_filter": False, "data_random_seed": 7}
    train = lgb.Dataset(x, label=y, feature_name=features, params=parameters, free_raw_data=False)
    validation = lgb.Dataset(
        validation_x,
        label=validation_y,
        reference=train,
        feature_name=features,
        params=parameters,
        free_raw_data=False,
    )
    return train, validation


def fit_lightgbm(
    train: lgb.Dataset,
    validation: lgb.Dataset,
    features: list[str],
    parameters: dict[str, Any],
    *,
    seed: int,
    index: int,
    settings: dict[str, Any] | None = None,
) -> Candidate:
    settings = settings or {
        "boosting_rounds": 400,
        "early_stopping_rounds": 30,
        "max_bin": 63,
        "num_threads": 4,
    }
    params = {
        "objective": "binary",
        "metric": "average_precision",
        "verbosity": -1,
        "seed": seed,
        "deterministic": True,
        "force_col_wise": True,
        "num_threads": settings["num_threads"],
        "max_bin": settings["max_bin"],
        "feature_pre_filter": False,
        **parameters,
    }
    model = lgb.train(
        params,
        train,
        num_boost_round=settings["boosting_rounds"],
        valid_sets=[validation],
        callbacks=[lgb.early_stopping(settings["early_stopping_rounds"], verbose=False)],
    )
    return Candidate(
        f"lightgbm_{index:02d}",
        model,
        features,
        parameters={**params, "best_iteration": model.best_iteration},
    )
