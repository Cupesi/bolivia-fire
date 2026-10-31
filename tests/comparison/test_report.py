from __future__ import annotations

import pytest


def test_synthetic_reproduction_identifies_tolerance_failures() -> None:
    from bolivia_fire.comparison.report import reproduction

    def synthetic_validation(ap: float) -> dict:
        return {
            family: {
                split: {kind: {"average_precision": ap} for kind in ("uncalibrated", "calibrated")}
                for split in ("val_select", "val_calib")
            }
            for family in ("lightgbm", "logistic_regression", "xgboost", "catboost", "spatial_cnn")
        }

    observed, reference = synthetic_validation(0.25), synthetic_validation(0.25)
    observed["catboost"]["val_select"]["uncalibrated"]["average_precision"] = 0.250002
    result = reproduction(observed, reference)
    assert result["all_within_tolerance"] is False
    catboost = result["models"]["catboost"]["val_select"]["uncalibrated"]
    assert catboost["ap_difference"] == pytest.approx(0.000002)
    assert catboost["within_tolerance"] is False
    assert result["models"]["xgboost"]["val_calib"]["calibrated"]["within_tolerance"] is True
