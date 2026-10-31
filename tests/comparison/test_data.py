from __future__ import annotations

import shutil
from pathlib import Path

import joblib
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from bolivia_fire.config import REPO_ROOT, Paths
from bolivia_fire.models.estimators import FeatureTransform


def test_synthetic_grid_masks_held_out_labels_but_keeps_feature_context(tmp_path: Path) -> None:
    pytest.importorskip("torch")
    from bolivia_fire.comparison.data import Split
    from bolivia_fire.comparison.runtime import Study
    from bolivia_fire.comparison.spatial import Grid

    (tmp_path / "configs").mkdir()
    for name in ("experiments.yaml", "comparison.yaml"):
        shutil.copyfile(REPO_ROOT / "configs" / name, tmp_path / "configs" / name)
    study = Study(Paths(tmp_path))
    directory = tmp_path / "datasets/processed"
    directory.mkdir(parents=True)
    pq.write_table(
        pa.table(
            {
                "cell_id": ["synthetic_a", "synthetic_held", "synthetic_b"],
                "row": [10, 10, 11],
                "col": [20, 21, 20],
            }
        ),
        directory / "cells.parquet",
    )
    dates = np.array(["2021-01-04"] * 3, dtype="datetime64[D]")
    frame = pa.table(
        {
            "cell_id": ["synthetic_a", "synthetic_held", "synthetic_b"],
            "issue_date": dates,
            "partition": ["val_select"] * 3,
            **{name: [1.0, 2.0, 3.0] for name in study.features},
        }
    )
    panel = directory / "panel/year=2021/part.parquet"
    panel.parent.mkdir(parents=True)
    pq.write_table(frame, panel)
    transform = FeatureTransform(study.features)
    transform.fit(np.array([[1.0] * 21, [3.0] * 21]))
    joblib.dump(transform, study.models / "train_transform.joblib")
    split = Split(
        "val_select",
        np.array([[1.0] * 21, [3.0] * 21]),
        np.array([0, 1]),
        pa.table(
            {
                "cell_id": ["synthetic_a", "synthetic_b"],
                "issue_date": dates[[0, 2]],
            }
        ),
        {},
    )
    grid = Grid(study, split)
    assert grid.x.shape == (1, 21, 2, 2)
    assert grid.y.tolist() == [[[0.0, -1.0], [1.0, -1.0]]]
    np.testing.assert_array_equal(grid.x[0, :, 0, 1], transform.transform(np.full((1, 21), 2))[0])
    np.testing.assert_array_equal(grid.x[0, :, 1, 1], np.zeros(21))
    np.testing.assert_array_equal(grid.y.reshape(-1)[grid.flat_index], split.y)


def test_synthetic_test_split_refuses_access_before_opening(tmp_path: Path) -> None:
    from bolivia_fire.comparison.data import load_split

    class SyntheticStudy:
        pass

    with pytest.raises(ValueError, match="opening"):
        load_split(SyntheticStudy(), "test")
