from __future__ import annotations

import gc
import math
import tempfile
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pyarrow.parquet as pq
import yaml

from bolivia_fire.config import Paths
from bolivia_fire.evaluation.metrics import METRIC_NAMES, metrics
from bolivia_fire.models.data import META_COLUMNS, PanelStore, experiment_features
from bolivia_fire.release.build import RESERVE, read_json

PROBABILITY_TOLERANCE = 1e-6
METRIC_TOLERANCE = 1e-8


def require_validation_split(split: str) -> None:
    if split not in {"val_select", "val_calib"}:
        raise ValueError("Metric replay accepts validation partitions only")


def compare_metrics(actual: dict[str, Any], expected: dict[str, Any], *,
                    tolerance: float = METRIC_TOLERANCE) -> dict[str, Any]:
    differences = {}
    failed = []
    for name in METRIC_NAMES:
        if name not in expected:
            continue
        a, b = actual.get(name), expected[name]
        if any(value is not None and not math.isfinite(value) for value in (a, b)):
            differences[name] = None
            failed.append(name)
            continue
        difference = abs(a - b) if a is not None and b is not None else None
        differences[name] = difference
        if (difference is None and a != b) or (difference is not None and difference > tolerance):
            failed.append(name)
    return {"passed": not failed, "tolerance": tolerance,
            "absolute_differences": differences, "failed_metrics": failed}


class ExtractedPanelStore(PanelStore):
    def __init__(self, benchmark: Path, reproduction: Path, scratch: Path) -> None:
        self.paths = Paths(benchmark)
        config = yaml.safe_load((reproduction / "configs/experiments.yaml").read_text("utf-8"))
        self.columns = config["columns"]
        self.minimum_free_bytes = RESERVE
        self.meta_columns = list(dict.fromkeys([*META_COLUMNS, *self.columns.values()]))
        self.files = [p for p in sorted((benchmark / "datasets/processed/panel").glob(
            "year=*/part.parquet")) if int(p.parent.name.split("=")[1]) <= 2022]
        scratch.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch, prefix="validation_")
        self.arrays: list[np.memmap] = []
        self.serial = 0

    def __exit__(self, *args: object) -> None:
        # Windows cannot unlink a matrix while a Python view still owns its mapping.
        for array in self.arrays:
            array.flush()
            array._mmap.close()
        self.arrays.clear()
        self.temporary.cleanup()

    def load(self, splits: list[str], *args: Any, **kwargs: Any) -> Any:
        if any(split not in {"train", "val_select", "val_calib"} for split in splits):
            raise ValueError("Only training features and validation rows may be read")
        return super().load(splits, *args, **kwargs)


def replay_strict(benchmark: Path, reproduction: Path, scratch: Path) -> dict[str, Any]:
    selection = read_json(reproduction / "artifacts/evaluation/selection_strict.json")
    name = selection["selected_model"]
    directory = reproduction / "artifacts/models/strict"
    candidate = joblib.load(directory / f"{name}.joblib")
    candidate.calibrator = joblib.load(directory / f"{name}.calibrator.joblib")
    features = experiment_features(selection["config"], "strict", None)
    checks = []
    with ExtractedPanelStore(benchmark, reproduction, scratch) as store:
        for split in ("val_select", "val_calib"):
            require_validation_split(split)
            data = store.load([split], features, "y_primary")
            saved = pq.read_table(
                reproduction / f"artifacts/predictions/strict/{split}.parquet",
                filters=[("model", "=", f"strict/{name}")]).to_pandas()
            keys = data.metadata.select(["cell_id", "issue_date"]).to_pandas()
            keys["position"] = np.arange(len(keys))
            aligned = keys.merge(saved, on=["cell_id", "issue_date"], how="left",
                                 validate="one_to_one", indicator=True).sort_values("position")
            labels_match = bool(np.array_equal(data.y, aligned.y.to_numpy()))
            probabilities = aligned.p.to_numpy()
            predicted = candidate.predict(data.inputs(candidate.features), calibrated=True)
            difference = float(np.max(np.abs(predicted - probabilities)))
            actual_metrics = metrics(data.y, probabilities, area=data.column("national_area_km2"),
                                     issue=data.column("issue_date"))
            metric_check = compare_metrics(
                actual_metrics, selection["candidates"][name][split]["calibrated"])
            keys_match = bool((aligned._merge == "both").all())
            checks.append({"split": split, "rows": len(data.y), "model": name,
                           "keys_match": keys_match, "labels_match": labels_match,
                           "max_absolute_probability_difference": difference,
                           "probability_tolerance": PROBABILITY_TOLERANCE,
                           "saved_prediction_metrics": actual_metrics,
                           "metric_comparison": metric_check,
                           "passed": (keys_match and labels_match and metric_check["passed"]
                                      and difference <= PROBABILITY_TOLERANCE)})
            del data, saved, aligned, predicted, probabilities, keys
            gc.collect()
    return {"passed": all(c["passed"] for c in checks), "checks": checks}


def replay_alternative(benchmark: Path, reproduction: Path, scratch: Path) -> dict[str, Any]:
    import xgboost as xgb

    config = yaml.safe_load((reproduction / "configs/comparison.yaml").read_text("utf-8"))
    validation = read_json(reproduction / "artifacts/evaluation/comparison_validation.json")
    directory = reproduction / "artifacts/models/comparison"
    cache = reproduction / "artifacts/.cache/comparison"
    model = xgb.Booster()
    model.load_model(directory / "xgboost.ubj")
    device = config["xgboost"]["fixed"]["device"]
    model.set_param({"device": device, "nthread": 4})
    cpu_model = xgb.Booster()
    cpu_model.load_model(directory / "xgboost.ubj")
    cpu_model.set_param({"device": "cpu", "nthread": 4})
    calibrator = joblib.load(directory / "xgboost_calibrator.joblib")
    checks = []
    with ExtractedPanelStore(benchmark, reproduction, scratch) as store:
        # Reuse the original training quantile reference without fitting a model.
        train = store.load(["train"], config["features"], "y_primary")
        reference = xgb.QuantileDMatrix(train.x, label=train.y, feature_names=config["features"],
                                       max_bin=63, nthread=4)
        del train
        gc.collect()
        for split in ("val_select", "val_calib"):
            require_validation_split(split)
            data = store.load([split], config["features"], "y_primary")
            metadata = pq.read_table(cache / f"data/{split}_metadata.parquet")
            labels = np.load(cache / f"data/{split}_y.npy", allow_pickle=False)
            keys_match = all(np.array_equal(data.column(name), metadata[name].to_numpy())
                             for name in ("cell_id", "issue_date"))
            labels_match = bool(np.array_equal(data.y, labels))
            matrix = xgb.QuantileDMatrix(data.x, feature_names=config["features"], max_bin=63,
                                        ref=reference, nthread=4)
            raw = model.predict(matrix).astype(np.float32)
            repeated_matrix = xgb.QuantileDMatrix(
                data.x, feature_names=config["features"], max_bin=63, ref=reference, nthread=4)
            repeated = model.predict(repeated_matrix).astype(np.float32)
            repeat_difference = float(np.max(np.abs(raw - repeated)))
            calibrated = calibrator.predict(raw).astype(np.float32)
            cpu_diagnostic = None
            if device != "cpu":
                cpu_raw = cpu_model.predict(matrix).astype(np.float32)
                cpu_calibrated = calibrator.predict(cpu_raw).astype(np.float32)
                cpu_diagnostic = {"max_raw_difference_from_recorded_backend":
                                  float(np.max(np.abs(cpu_raw - raw))),
                                  "max_calibrated_difference_from_recorded_backend":
                                  float(np.max(np.abs(cpu_calibrated - calibrated)))}
            modes = []
            for mode, calculated, key in (("raw", raw, "uncalibrated"),
                                           ("cal", calibrated, "calibrated")):
                saved = np.load(cache / f"predictions/xgboost_{split}_{mode}.npy",
                                allow_pickle=False)
                difference = float(np.max(np.abs(calculated - saved)))
                values = metrics(labels, saved,
                                 area=metadata["national_area_km2"].to_numpy(),
                                 issue=metadata["issue_date"].to_numpy())
                comparison = compare_metrics(values, validation["xgboost"][split][key])
                modes.append({"mode": key, "max_absolute_probability_difference": difference,
                              "probability_tolerance": PROBABILITY_TOLERANCE,
                              "saved_prediction_metrics": values, "metric_comparison": comparison,
                              "passed": comparison["passed"]
                              and difference <= PROBABILITY_TOLERANCE})
            checks.append({"split": split, "rows": len(labels), "keys_match": keys_match,
                           "labels_match": labels_match, "modes": modes,
                           "repeat_prediction_max_difference": repeat_difference,
                           "cpu_inference_diagnostic": cpu_diagnostic,
                           "passed": (keys_match and labels_match
                                      and repeat_difference == 0.0
                                      and all(m["passed"] for m in modes))})
            del data, matrix, repeated_matrix, metadata, labels, raw, calibrated
            gc.collect()
    return {"passed": all(c["passed"] for c in checks), "learner": "xgboost",
            "device": device, "quantiles": "Original training feature quantiles; no fitting",
            "cpu_limitation": "CPU float differences can be amplified by isotonic interpolation; "
                              "the recorded inference backend is used for the roundtrip",
            "checks": checks, "catboost": "Skipped: recorded GPU scores are not digit reproducible"}


def validate_models(benchmark: Path, reproduction: Path, scratch: Path) -> dict[str, Any]:
    strict = replay_strict(benchmark, reproduction, scratch)
    alternative = replay_alternative(benchmark, reproduction, scratch)
    return {"passed": strict["passed"] and alternative["passed"], "strict": strict,
            "alternative": alternative,
            "deserialization": "Only trusted locally packaged objects loaded; joblib/pickle "
                               "can execute code and is unsafe for untrusted inputs",
            "test_access": False}
