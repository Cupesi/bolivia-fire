from __future__ import annotations

import json

import numpy as np
import pytest

from bolivia_fire.config import Paths


def test_synthetic_model_export_refuses_hash_mismatch_before_loading(tmp_path) -> None:
    from bolivia_fire.live.model import export_model

    evaluation = tmp_path / "artifacts/evaluation"
    evaluation.mkdir(parents=True)
    (evaluation / "selection_strict.json").write_text(json.dumps({"selected_model": "lightgbm_08"}))
    (evaluation / "final_test_log.json").write_text(
        json.dumps(
            [
                {
                    "model_ids": {"strict/lightgbm_08": "f" * 64},
                    "calibrator_ids": {"strict/lightgbm_08": "e" * 64},
                }
            ]
        )
    )
    models = tmp_path / "artifacts/models/strict"
    models.mkdir(parents=True)
    (models / "lightgbm_08.joblib").write_bytes(b"synthetic invalid artifact")
    (models / "lightgbm_08.calibrator.joblib").write_bytes(b"synthetic invalid calibrator")
    with pytest.raises(ValueError, match="primary.*hash"):
        export_model(Paths(tmp_path), tmp_path / "output")


@pytest.mark.local_data
def test_native_export_matches_frozen_candidate_predictions(tmp_path) -> None:
    import joblib
    import pandas as pd

    from bolivia_fire.live.model import FrozenModel, export_model

    paths = Paths()
    card = export_model(paths, tmp_path)
    candidate = joblib.load(paths.root / "artifacts/models/strict/lightgbm_08.joblib")
    x = np.zeros((3, len(candidate.features)), dtype=np.float32)
    x[1] = 1
    x[2] = np.nan
    validation = pd.read_parquet(
        paths.datasets / "processed/panel/year=2021/part.parquet",
        columns=candidate.features,
        filters=[("issue_date", "==", pd.Timestamp("2021-09-06"))],
    ).to_numpy(dtype=np.float32)
    x = np.concatenate([x, validation])
    model = FrozenModel(tmp_path)
    np.testing.assert_array_equal(model.predict(x), candidate.predict(x, calibrated=True))
    assert card["model_id"] == "ed72987f3dcbb1407601ee262b0e416d06a31f047a20e2386c1c4f4e7021b65b"
