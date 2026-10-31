from __future__ import annotations

import numpy as np
import pytest


def test_persistence_bins_use_only_train_frequencies() -> None:
    from bolivia_fire.models.baselines import Persistence

    model = Persistence().fit(
        np.array([0, 0, 1, 2, 3, 4, 5, 6]), np.array([0, 1, 1, 0, 0, 1, 1, 1])
    )
    np.testing.assert_allclose(model.predict(np.array([0, 1, 3, 20])), [0.5, 1, 1 / 3, 1])


def test_negative_sampling_is_seeded_and_reweighted() -> None:
    from bolivia_fire.models.estimators import negative_sample

    y = np.r_[np.ones(20), np.zeros(320)]
    rows, weights = negative_sample(y, seed=7)
    assert y[rows].sum() == 20
    assert len(rows) == 52
    assert (weights[y[rows] == 0] == 10).all()
    assert (weights[y[rows] == 1] == 1).all()
    np.testing.assert_array_equal(rows, negative_sample(y, seed=7)[0])


@pytest.mark.parametrize(("negatives", "sampled", "weight"), [(3, 1, 3), (23, 3, 23 / 3)])
def test_synthetic_rounded_negative_sample_preserves_population_mass(
    negatives: int, sampled: int, weight: float,
) -> None:
    from bolivia_fire.models.estimators import negative_sample

    y = np.r_[np.ones(2), np.zeros(negatives)]
    rows, weights = negative_sample(y, seed=7)
    assert len(rows) == 2 + sampled
    np.testing.assert_allclose(weights[y[rows] == 0], weight)
    assert weights[y[rows] == 0].sum() == pytest.approx(negatives)
    assert weights[y[rows] == 1].tolist() == [1, 1]


def test_transform_uses_train_statistics_and_preserves_signed_seasonality() -> None:
    from bolivia_fire.models.estimators import FeatureTransform

    train = np.array([[0, -1, np.nan], [3, 1, 1], [8, 0, 3]], dtype=np.float32)
    transform = FeatureTransform(["count_last_7d", "week_of_year_sin", "train_climatology"])
    transform.fit(train)
    np.testing.assert_allclose(transform.transform(train).mean(axis=0), 0, atol=1e-6)
    before = transform.mean.copy()
    assert np.isfinite(transform.transform(np.array([[1000, -1, np.nan]]))).all()
    np.testing.assert_array_equal(transform.mean, before)


def test_calibration_is_monotonic_and_records_single_class() -> None:
    from bolivia_fire.models.baselines import Climatology
    from bolivia_fire.models.estimators import Candidate

    candidate = Candidate("train_climatology", Climatology(0), ["train_climatology"])
    candidate.calibrate(np.array([[0.1], [0.2], [0.8], [0.9]]), np.array([0, 0, 1, 1]))
    predicted = candidate.predict(np.array([[0.05], [0.5], [0.95]]), calibrated=True)
    assert (np.diff(predicted) >= 0).all()
    assert candidate.calibration_status == "fitted"
    candidate.calibrate(np.array([[0.1], [0.2]]), np.array([0, 0]))
    assert candidate.calibration_status == "single_class_identity"
    assert candidate.calibrator is None


def test_random_search_has_twenty_deterministic_bounded_configurations() -> None:
    from bolivia_fire.models.estimators import search_parameters

    values = search_parameters(seed=7)
    assert len(values) == 20
    assert values == search_parameters(seed=7)
    assert len({tuple(sorted(item.items())) for item in values}) == 20
    assert all(7 <= item["num_leaves"] <= 63 for item in values)


def test_logistic_fits_population_weighted_subsample() -> None:
    from bolivia_fire.models.estimators import fit_logistic

    rng = np.random.default_rng(4)
    x = rng.exponential(size=(2000, 1)).astype(np.float32)
    y = (rng.random(2000) < 1 / (1 + np.exp(2 - 2 * x[:, 0]))).astype(np.int8)
    model = fit_logistic(x, y, ["count_last_7d"], seed=7)
    assert model.predict(np.array([[0], [10]], dtype=np.float32))[0] < 0.5
    assert model.predict(np.array([[0], [10]], dtype=np.float32))[1] > 0.9
    with pytest.raises(ValueError, match="classes"):
        fit_logistic(x, np.zeros(2000), ["count_last_7d"], seed=7)
