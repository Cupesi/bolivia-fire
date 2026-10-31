from __future__ import annotations

import numpy as np
import pytest


def test_hand_computed_metrics_and_reliability() -> None:
    from bolivia_fire.evaluation.metrics import metrics

    result = metrics([0, 1, 1, 0], [0.1, 0.4, 0.8, 0.2])
    assert result["average_precision"] == 1
    assert result["roc_auc"] == 1
    assert result["brier_score"] == pytest.approx(0.1125)
    assert result["log_loss"] == pytest.approx(-np.log([0.9, 0.4, 0.8, 0.8]).mean())
    assert result["expected_calibration_error"] == pytest.approx(0.275)
    assert sum(row["count"] for row in result["reliability"]) == 4


def test_tied_ap_is_stepwise_and_single_class_has_reasons() -> None:
    from bolivia_fire.evaluation.metrics import metrics

    assert metrics([1, 0, 1], [0.5, 0.5, 0.1])["average_precision"] == pytest.approx(7 / 12)
    result = metrics([0, 0], [0.2, 0.8])
    assert result["average_precision"] is None
    assert result["roc_auc"] is None
    assert result["undefined_reasons"]["roc_auc"] == "single_class"
    assert result["brier_score"] == pytest.approx(0.34)


def test_fractional_area_budget_pools_positives_over_weeks() -> None:
    from bolivia_fire.evaluation.metrics import recall_at_area_budget

    # Synthetic weeks: first 5% of 100 km2 takes half of a positive 10 km2 cell.
    result = recall_at_area_budget(
        [1, 0, 1, 1], [0.9, 0.1, 0.9, 0.1], [10, 90, 50, 50], [1, 1, 2, 2], 0.05
    )
    assert result == pytest.approx((0.5 + 0.1) / 3)


def test_equal_frequency_bins_and_invalid_inputs() -> None:
    from bolivia_fire.evaluation.metrics import metrics

    result = metrics([0, 1] * 10, [0.5] * 20)
    assert len(result["reliability"]) == 10
    assert all(row["count"] == 2 for row in result["reliability"])
    with pytest.raises(ValueError):
        metrics([0, 1], [0.1, np.nan])
    assert metrics([], [])["undefined_reasons"]["brier_score"] == "empty_subset"


def test_paired_block_bootstrap_identical_predictions_have_zero_difference() -> None:
    from bolivia_fire.evaluation.bootstrap import block_bootstrap

    result = block_bootstrap(
        np.array([0, 1, 1, 0]),
        np.array([0.1, 0.8, 0.9, 0.2]),
        np.array(["a", "a", "b", "b"]),
        baseline=np.array([0.1, 0.8, 0.9, 0.2]),
        replicates=500,
        seed=7,
    )
    assert result["replicates"] == 500
    assert result["paired_difference"]["brier_score"]["lower"] == 0
    assert result["paired_difference"]["brier_score"]["upper"] == 0
    assert result == block_bootstrap(
        np.array([0, 1, 1, 0]),
        np.array([0.1, 0.8, 0.9, 0.2]),
        np.array(["a", "a", "b", "b"]),
        baseline=np.array([0.1, 0.8, 0.9, 0.2]),
        replicates=500,
        seed=7,
    )


def test_weighted_tied_metrics_match_explicit_replication() -> None:
    from bolivia_fire.evaluation.metrics import metrics

    y, p, weights = np.array([0, 1, 1, 0]), np.array([0.2, 0.4, 0.4, 0.9]), [2, 3, 1, 0]
    weighted = metrics(y, p, weights=weights)
    repeated = metrics(np.repeat(y, weights), np.repeat(p, weights))
    for name in (
        "average_precision",
        "brier_score",
        "log_loss",
        "roc_auc",
        "expected_calibration_error",
    ):
        assert weighted[name] == pytest.approx(repeated[name])


def test_reliability_ties_retain_input_order() -> None:
    from bolivia_fire.evaluation.metrics import metrics

    table = metrics([1] + [0] * 10, [0.5] * 11)["reliability"]
    assert table[0]["positive_rate"] == pytest.approx(10 / 11)
    assert table[-1]["positive_rate"] == 0


@pytest.mark.parametrize(
    ("lower", "upper", "h1", "h2"),
    [
        (0.01, 0.1, True, False),
        (-0.1, 0.1, False, True),
        (0.0, 0.1, False, True),
        (None, None, None, None),
    ],
)
def test_hypothesis_decision_oracles(lower, upper, h1, h2) -> None:
    from bolivia_fire.evaluation.decisions import hypothesis_decision

    interval = {"lower": lower, "upper": upper}
    assert hypothesis_decision(interval)["supported"] is h1
    assert hypothesis_decision(interval, contains_zero=True)["supported"] is h2
