import numpy as np
import pyarrow as pa
import pytest

from bolivia_fire.extension.inference import common_support, decision, paired_report
from bolivia_fire.models.data import PanelData


def synthetic_panel(cells, probabilities):
    return PanelData(
        np.array(probabilities, dtype=np.float32).reshape(-1, 1),
        np.array([cell % 2 for cell in cells]),
        pa.table(
            {
                "cell_id": cells,
                "issue_date": ["2022-01-03"] * len(cells),
                "national_area_km2": [1.0] * len(cells),
            }
        ),
        ["synthetic_feature"],
        {},
    )


def test_synthetic_common_support_aligns_order_and_counts():
    aligned, counts = common_support(
        {
            "baseline": synthetic_panel([3, 1, 2, 4], [0.8, 0.9, 0.2, 0.1]),
            "vegetation": synthetic_panel([4, 3, 1], [0.1, 0.8, 0.9]),
        }
    )
    assert aligned["baseline"].column("cell_id").tolist() == [1, 3, 4]
    assert aligned["vegetation"].column("cell_id").tolist() == [1, 3, 4]
    assert counts["baseline"]["lost_to_common_support"] == 1
    assert counts["vegetation"]["lost_to_common_support"] == 0


def test_synthetic_duplicate_keys_refuse_comparison():
    with pytest.raises(ValueError, match="Duplicate"):
        common_support({"a": synthetic_panel([1, 1], [0.4, 0.4])})


@pytest.mark.parametrize(
    "interval,calibration,expected",
    [
        ({"lower": 0.001, "upper": 0.1}, "fitted", "adds_information"),
        ({"lower": 0, "upper": 0.1}, "fitted", "inconclusive"),
        ({"lower": -0.2, "upper": -0.1}, "fitted", "inconclusive"),
        ({"lower": None, "upper": None}, "fitted", "inconclusive"),
        ({"lower": float("nan"), "upper": 0.1}, "fitted", "inconclusive"),
        ({"lower": 0.01, "upper": 0.1}, "single_class_identity", "inconclusive"),
    ],
)
def test_synthetic_decision_requires_finite_positive_interval(interval, calibration, expected):
    assert decision(interval, calibration_status=calibration)["status"] == expected


def test_synthetic_paired_report_reuses_same_rows_and_zero_difference():
    data = synthetic_panel([1, 2, 3, 4], [0.9, 0.1, 0.8, 0.2])
    p = data.x[:, 0]
    report = paired_report(data, p, p, calibration_status="fitted")
    assert report["bootstrap"]["replicates"] == 500
    assert report["bootstrap"]["paired_difference"]["average_precision"] == {
        "lower": 0.0,
        "upper": 0.0,
        "valid_replicates": 500,
        "reason": None,
    }
    assert report["decision"]["status"] == "inconclusive"


def test_synthetic_single_class_bootstrap_has_no_valid_differences():
    data = synthetic_panel([1, 3], [0.9, 0.8])
    report = paired_report(data, data.x[:, 0], data.x[:, 0], calibration_status="fitted")
    assert report["bootstrap"]["paired_difference"]["average_precision"] == {
        "lower": None,
        "upper": None,
        "valid_replicates": 0,
        "reason": "undefined_in_all_replicates",
    }
    assert report["decision"]["status"] == "inconclusive"
