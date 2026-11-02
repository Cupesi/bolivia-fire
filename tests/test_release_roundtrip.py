from __future__ import annotations

import numpy as np
import pytest

from bolivia_fire.release.roundtrip import compare_metrics, require_validation_split


@pytest.mark.parametrize("actual, expected", [(float("nan"), 0.5), (0.5, float("nan")),
                                              (float("inf"), float("inf"))])
def test_synthetic_nonfinite_metric_cannot_pass(actual: float, expected: float) -> None:
    result = compare_metrics({"average_precision": actual}, {"average_precision": expected})
    assert not result["passed"]
    assert result["failed_metrics"] == ["average_precision"]
    assert result["absolute_differences"]["average_precision"] is None


def test_synthetic_metric_comparison_records_tolerance_and_mismatch() -> None:
    first = {"average_precision": 0.5, "brier_score": 0.125}
    second = {"average_precision": 0.5 + 1e-10, "brier_score": 0.125}
    assert compare_metrics(first, second, tolerance=1e-8)["passed"]
    second["brier_score"] = 0.25
    result = compare_metrics(first, second, tolerance=1e-8)
    assert not result["passed"]
    assert np.isclose(result["absolute_differences"]["brier_score"], 0.125)


def test_synthetic_metric_replay_refuses_final_test() -> None:
    import pytest

    for split in ("val_select", "val_calib"):
        require_validation_split(split)
    with pytest.raises(ValueError, match="validation"):
        require_validation_split("test")


def test_synthetic_extracted_store_closes_mappings_before_cleanup(tmp_path) -> None:
    import tempfile
    from pathlib import Path

    from bolivia_fire.release.roundtrip import ExtractedPanelStore

    store = ExtractedPanelStore.__new__(ExtractedPanelStore)
    store.temporary = tempfile.TemporaryDirectory(dir=tmp_path)
    directory = Path(store.temporary.name)
    mapping = np.memmap(directory / "synthetic.f32", mode="w+", shape=(2,), dtype=np.float32)
    store.arrays = [mapping]
    store.__exit__()
    assert not directory.exists()
