from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
import lightgbm as lgb
import numpy as np
from numpy.typing import NDArray
from sklearn.isotonic import IsotonicRegression

from bolivia_fire.config import Paths
from bolivia_fire.firms.storage import durable_write, sha256_file
from bolivia_fire.live.files import read_json, write_json
from bolivia_fire.live.source import PROXY


def export_model(paths: Paths, out: Path) -> dict[str, Any]:
    evaluation = paths.root / "artifacts/evaluation"
    selection = read_json(evaluation / "selection_strict.json")
    primary = read_json(evaluation / "final_test_log.json")[0]
    name = selection["selected_model"]
    identifier = f"strict/{name}"
    directory = paths.root / "artifacts/models/strict"
    model_path = directory / f"{name}.joblib"
    calibrator_path = directory / f"{name}.calibrator.joblib"
    model_hash, calibrator_hash = sha256_file(model_path), sha256_file(calibrator_path)
    if (
        model_hash != primary["model_ids"][identifier]
        or calibrator_hash != primary["calibrator_ids"][identifier]
    ):
        raise ValueError("Frozen artifacts do not match primary evaluation opening hashes")
    protocol_hash = sha256_file(paths.root / "configs/protocol.yaml")
    if protocol_hash != primary["protocol_sha256"]:
        raise ValueError("Protocol does not match primary evaluation opening hash")
    candidate = joblib.load(model_path)
    calibrator = joblib.load(calibrator_path)
    if not isinstance(candidate.estimator, lgb.Booster) or candidate.transformer is not None:
        raise ValueError("Selected strict model must be a native untransformed LightGBM booster")
    if calibrator is not None and (
        candidate.calibrator is None
        or not np.array_equal(calibrator.X_thresholds_, candidate.calibrator.X_thresholds_)
        or not np.array_equal(calibrator.y_thresholds_, candidate.calibrator.y_thresholds_)
    ):
        raise ValueError("Separate and embedded frozen calibrators differ")
    model_out = out / "model"
    model_out.mkdir(parents=True, exist_ok=True)
    durable_write(model_out / "model.txt", candidate.estimator.model_to_string().encode())
    calibration = {"status": candidate.calibration_status, "x": [], "y": []}
    if calibrator is not None:
        calibration.update(x=calibrator.X_thresholds_.tolist(), y=calibrator.y_thresholds_.tolist())
        calibration["dtype"] = calibrator.X_thresholds_.dtype.name
    write_json(model_out / "calibrator.json", calibration)
    card = {
        "model_id": model_hash,
        "model_name": name,
        "variant": "strict",
        "features": candidate.features,
        "protocol_sha256": protocol_hash,
        "source_model_sha256": model_hash,
        "source_calibrator_sha256": calibrator_hash,
        "primary_opening_utc": primary["timestamp_utc"],
        "model_txt_sha256": sha256_file(model_out / "model.txt"),
        "calibrator_json_sha256": sha256_file(model_out / "calibrator.json"),
        "training_product": "VIIRS_SNPP_SP",
        "inference_product": "VIIRS_SNPP_NRT",
        "nrt_type_proxy": PROXY,
    }
    write_json(model_out / "model_card.json", card)
    return card


class FrozenModel:
    def __init__(self, out: Path) -> None:
        folder = out / "model"
        self.card = read_json(folder / "model_card.json")
        for filename, field in (
            ("model.txt", "model_txt_sha256"),
            ("calibrator.json", "calibrator_json_sha256"),
        ):
            if sha256_file(folder / filename) != self.card[field]:
                raise ValueError(f"Exported {filename} hash mismatch")
        self.booster = lgb.Booster(model_file=str(folder / "model.txt"))
        calibration = read_json(folder / "calibrator.json")
        self.calibrator = None
        if calibration["x"]:
            estimator = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1)
            estimator.X_thresholds_ = np.asarray(calibration["x"], dtype=calibration["dtype"])
            estimator.y_thresholds_ = np.asarray(calibration["y"], dtype=calibration["dtype"])
            estimator.X_min_, estimator.X_max_ = (
                estimator.X_thresholds_[0],
                estimator.X_thresholds_[-1],
            )
            # Rebuild interpolation without fitting; sklearn casts inputs to threshold dtype.
            estimator._build_f(estimator.X_thresholds_, estimator.y_thresholds_)
            self.calibrator = estimator

    def predict(self, x: NDArray) -> NDArray[np.float32]:
        probability = self.booster.predict(x, num_threads=4)
        if self.calibrator is not None:
            probability = self.calibrator.predict(probability)
        return probability.astype(np.float32)
