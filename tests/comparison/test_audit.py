from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from bolivia_fire.config import REPO_ROOT, Paths


def test_synthetic_determinism_audit_detects_different_cached_array(tmp_path: Path) -> None:
    pytest.importorskip("catboost")
    from bolivia_fire.comparison.audit import determinism_audit
    from bolivia_fire.comparison.runtime import Study

    (tmp_path / "configs").mkdir()
    for name in ("experiments.yaml", "comparison.yaml"):
        shutil.copyfile(REPO_ROOT / "configs" / name, tmp_path / "configs" / name)
    study = Study(Paths(tmp_path))
    study.save(
        "reference",
        {
            "array_sha256": {"train_y.npy": hashlib.sha256(b"synthetic original").hexdigest()},
            "catboost_binary_sha256": "synthetic reference binary",
            "catboost_trials": [{"index": 0, "parameters": {"depth": 2}, "val_select_ap": 0.5}],
        },
    )
    study.save(
        "catboost_trials",
        [
            {
                "index": 0,
                "parameters": {"depth": 2},
                "val_select_ap": 0.4,
            }
        ],
    )
    (study.cache / "data/train_y.npy").write_bytes(b"synthetic changed")
    result = determinism_audit(study)
    assert result["array_identity"]["train_y.npy"]["identical"] is False
    assert result["trial_parameters"]["0"]["identical"] is True
    assert result["trial_parameters"]["0"]["ap_difference"] == pytest.approx(-0.1)
    assert result["catboost_binary_identical"] is False


def test_synthetic_cpu_audit_preserves_gpu_choice_and_fixed_tree_count(tmp_path: Path) -> None:
    pytest.importorskip("catboost")
    from bolivia_fire.comparison.audit import cpu_check
    from bolivia_fire.comparison.runtime import Study

    (tmp_path / "configs").mkdir()
    for name in ("experiments.yaml", "comparison.yaml"):
        shutil.copyfile(REPO_ROOT / "configs" / name, tmp_path / "configs" / name)
    study = Study(Paths(tmp_path))
    study.config["minimum_free_gib"] = 0
    study.save(
        "catboost_selected",
        {
            "selected_trial": {
                "index": 1,
                "best_round": 2,
                "parameters": {
                    "task_type": "GPU",
                    "devices": "0",
                    "iterations": 4,
                    "depth": 2,
                    "loss_function": "Logloss",
                    "random_seed": 7,
                    "thread_count": 1,
                    "allow_writing_files": False,
                    "verbose": False,
                },
            },
        },
    )
    selected = study.result_path("catboost_selected").read_bytes()
    gpu_model = study.models / "catboost.cbm"
    gpu_model.write_bytes(b"synthetic selected GPU model")
    for split, year in (("train", 2020), ("val_select", 2021)):
        x = np.tile(np.arange(20, dtype=np.float32)[:, None], (1, 21))
        np.save(study.cache / f"data/{split}_x.npy", x)
        np.save(study.cache / f"data/{split}_y.npy", (np.arange(20) >= 10).astype(np.float32))
        pq.write_table(
            pa.table(
                {
                    "issue_date": np.array([f"{year}-01-06"] * 20, dtype="datetime64[D]"),
                    "national_area_km2": np.ones(20),
                }
            ),
            study.cache / f"data/{split}_metadata.parquet",
        )
        (study.cache / f"data/{split}_exclusions.json").write_text("{}", encoding="utf-8")
    result = cpu_check(study)
    assert result["completed"] is True
    assert result["fitted_trees"] == 2
    assert result["parameters"]["task_type"] == "CPU"
    assert result["val_select"]["average_precision"] == 1.0
    assert gpu_model.read_bytes() == b"synthetic selected GPU model"
    assert study.result_path("catboost_selected").read_bytes() == selected
    artifact = json.loads(study.result_path("cpu_check").read_text(encoding="utf-8"))
    assert artifact["reference"] == "Frozen GPU search; CPU audit is not a selection candidate"
