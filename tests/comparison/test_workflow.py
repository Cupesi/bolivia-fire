from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest

from bolivia_fire.config import REPO_ROOT, Paths
from bolivia_fire.evaluation.metrics import metrics


def test_synthetic_selection_ap_uses_stepwise_ties() -> None:
    from bolivia_fire.comparison.runtime import average_precision

    y = np.array([1, 0, 1, 0], dtype=np.float32)
    p = np.array([0.9, 0.8, 0.8, 0.1], dtype=np.float32)
    assert average_precision(y, p) == pytest.approx(5 / 6, abs=1e-14)
    assert average_precision(y, p) == metrics(y, p)["average_precision"]


def test_synthetic_split_reuses_benchmark_metrics() -> None:
    from bolivia_fire.comparison.data import Split

    y = np.array([1, 0, 1, 0], dtype=np.float32)
    p = np.array([0.9, 0.8, 0.8, 0.1], dtype=np.float32)
    metadata = pa.table(
        {
            "issue_date": np.array(["2022-01-03"] * 4, dtype="datetime64[D]"),
            "national_area_km2": [1.0, 2.0, 3.0, 4.0],
        }
    )
    split = Split("val_calib", np.zeros((4, 1)), y, metadata, {})
    observed = split.score(p)
    assert observed == metrics(y, p, area=[1, 2, 3, 4], issue=split.issue)
    assert observed["average_precision"] == pytest.approx(5 / 6)
    assert observed["brier_score"] == pytest.approx(0.175)


def test_synthetic_cli_refuses_unguarded_test_without_reading_data(tmp_path: Path) -> None:
    from bolivia_fire.cli import main

    assert main(["compare", "evaluate"], root=tmp_path) == 1
    assert main(["compare", "evaluate", "--post-hoc-test"], root=tmp_path) == 1
    assert not (tmp_path / "artifacts/evaluation/final_test_log.json").exists()


def test_synthetic_cli_exposes_comparison_stages() -> None:
    from bolivia_fire.cli import _parser

    for stage in ("prepare", "train", "audit", "select", "report"):
        args = _parser().parse_args(["compare", stage])
        assert args.comparison_stage == stage
        assert callable(args.pipeline_handler)
    args = _parser().parse_args(
        [
            "compare",
            "evaluate",
            "--post-hoc-test",
            "--reason",
            "exploratory",
            "--reopen-reason",
            "audit",
        ]
    )
    assert args.post_hoc_test is True
    assert args.reason == "exploratory"
    assert args.reopen_reason == "audit"


def test_synthetic_test_scoring_starts_after_durable_opening(tmp_path: Path, monkeypatch) -> None:
    from bolivia_fire.comparison.guard import freeze_selection
    from bolivia_fire.comparison.workflow import Comparison

    (tmp_path / "configs").mkdir()
    for name in ("protocol.yaml", "experiments.yaml", "comparison.yaml"):
        shutil.copyfile(REPO_ROOT / "configs" / name, tmp_path / "configs" / name)
    paths = Paths(tmp_path)
    freeze_selection(paths, {"artifact_hashes": {}, "input_hashes": {}, "decision": "synthetic"})

    def synthetic_score(self, opening: dict) -> dict:
        entries = json.loads((tmp_path / "artifacts/evaluation/final_test_log.json").read_text())
        assert entries[-1] == opening
        assert opening["type"] == "post_hoc_comparison"
        return {"synthetic": True}

    monkeypatch.setattr(Comparison, "_score_test", synthetic_score)
    assert Comparison(paths).evaluate(post_hoc_test=True, reason="exploratory") == {
        "synthetic": True
    }
