from __future__ import annotations

import json
import os
import platform
import shutil
import threading
import time
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from numpy.typing import NDArray

from bolivia_fire.config import Paths
from bolivia_fire.evaluation.artifacts import atomic_json, hardware, snapshot
from bolivia_fire.firms.storage import sha256_file

FAMILIES = ("lightgbm", "logistic_regression", "xgboost", "catboost", "spatial_cnn")
ALTERNATIVES = FAMILIES[2:]
MODEL_FILES = {
    "xgboost": "xgboost.ubj",
    "catboost": "catboost.cbm",
    "spatial_cnn": "spatial_cnn.pt",
}


def now() -> str:
    return datetime.now(UTC).isoformat()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def log(message: str) -> None:
    print(f"{now()} {message}", flush=True)


def average_precision(y: NDArray, p: NDArray) -> float:
    # The benchmark's tied-score step-wise formula, without unrelated search diagnostics.
    if not len(y) or not 0 < y.sum() < len(y):
        raise ValueError("Search average precision requires both classes")
    order = np.argsort(-p, kind="stable")
    ranked = p[order]
    ends = np.r_[np.flatnonzero(np.diff(ranked)), len(ranked) - 1]
    tp = np.cumsum(y[order], dtype=np.float64)[ends]
    return float(np.dot(np.diff(np.r_[0.0, tp]), tp / (ends + 1)) / tp[-1])


def input_hashes(paths: Paths, *, include_test: bool = False) -> dict[str, str]:
    hashes = snapshot(paths, variant="strict", include_test=include_test)
    for name in (
        "configs/comparison.yaml",
        "datasets/processed/cells.parquet",
        "artifacts/evaluation/selection_strict.json",
    ):
        path = paths.root / name
        if path.exists():
            hashes[name] = sha256_file(path)
    return hashes


def check_hashes(paths: Paths, hashes: dict[str, str]) -> None:
    for name, expected in hashes.items():
        path = paths.root / name
        if not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f"Frozen input changed or hash mismatch: {name}")


def environment() -> dict[str, Any]:
    packages: dict[str, str | None] = {}
    for name in (
        "numpy",
        "pandas",
        "pyarrow",
        "scikit-learn",
        "lightgbm",
        "joblib",
        "xgboost",
        "catboost",
        "torch",
        "psutil",
    ):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": packages,
    }


class Study:
    def __init__(self, paths: Paths) -> None:
        self.paths = paths
        self.config = yaml.safe_load((paths.root / "configs/comparison.yaml").read_text("utf-8"))
        experiments = yaml.safe_load((paths.root / "configs/experiments.yaml").read_text("utf-8"))
        if self.config["features"] != experiments["strict_features"]:
            raise ValueError("Comparison must use the frozen strict feature order")
        self.features: list[str] = self.config["features"]
        self.cache = paths.root / "artifacts/.cache/comparison"
        self.models = paths.root / "artifacts/models/comparison"
        for directory in (
            self.cache / "data",
            self.cache / "predictions",
            self.cache / "tmp",
            self.models,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
        for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
            os.environ[name] = "4"

    def result_path(self, name: str) -> Path:
        return self.paths.root / f"artifacts/evaluation/comparison_{name}.json"

    def save(self, name: str, value: Any) -> None:
        atomic_json(self.result_path(name), value)

    def prediction_path(self, family: str, split: str, calibrated: bool = False) -> Path:
        return self.cache / "predictions" / f"{family}_{split}_{'cal' if calibrated else 'raw'}.npy"

    def save_prediction(
        self, family: str, split: str, p: NDArray, calibrated: bool = False
    ) -> None:
        np.save(self.prediction_path(family, split, calibrated), p.astype(np.float32))

    def prediction(self, family: str, split: str, calibrated: bool = False) -> NDArray:
        return np.load(self.prediction_path(family, split, calibrated))

    def require_unfrozen(self) -> None:
        if self.result_path("selection").exists():
            raise ValueError(
                "Comparison selection is frozen; use a separate checkout for a new study"
            )


class Resources:
    def __init__(self, study: Study, stage: str) -> None:
        import psutil

        self.study, self.stage = study, stage
        self.tick = time.perf_counter()
        self.peak_rss = 0
        self.minimum_free = shutil.disk_usage(study.paths.root).free
        self.violation: str | None = None
        self.process = psutil.Process()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._sample, daemon=True)
        self._measure()
        self.thread.start()

    def _measure(self) -> None:
        import psutil

        rss = 0
        for process in [self.process, *self.process.children(recursive=True)]:
            try:
                rss += process.memory_info().rss
            except psutil.NoSuchProcess:
                continue
        self.peak_rss = max(self.peak_rss, rss)
        self.minimum_free = min(self.minimum_free, shutil.disk_usage(self.study.paths.root).free)
        if rss >= self.study.config["maximum_rss_gib"] * 1024**3:
            self.violation = "Comparison exceeded its process-tree RAM limit"
        if self.minimum_free < self.study.config["minimum_free_gib"] * 1024**3:
            self.violation = "Comparison exhausted its configured free disk reserve"

    def _sample(self) -> None:
        while not self.stop.wait(0.5):
            self._measure()

    def check(self) -> None:
        if self.violation:
            raise RuntimeError(self.violation)

    def record(self) -> dict[str, Any]:
        self.check()
        return {
            "stage": self.stage,
            "runtime_seconds": time.perf_counter() - self.tick,
            "peak_process_tree_rss_gib": self.peak_rss / 1024**3,
            "minimum_free_disk_gib": self.minimum_free / 1024**3,
        }

    def close(self) -> dict[str, Any]:
        self.stop.set()
        self.thread.join(timeout=2)
        self._measure()
        return self.record()

    def __enter__(self) -> Resources:
        return self

    def __exit__(self, *args: object) -> None:
        self.stop.set()
        self.thread.join(timeout=2)


def machine() -> dict[str, Any]:
    result = hardware()
    try:
        import torch

        result.update(
            cuda_available=torch.cuda.is_available(),
            cuda_version=torch.version.cuda,
            cudnn_version=torch.backends.cudnn.version(),
            gpu=torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        )
    except ImportError:
        result["cuda_available"] = False
    return result
