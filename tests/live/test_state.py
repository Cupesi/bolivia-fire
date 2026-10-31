from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bolivia_fire.features import DailyHistory


def synthetic_cells() -> pd.DataFrame:
    return pd.DataFrame({"cell_id": ["a", "b", "c"], "row": [1, 1, 4], "col": [1, 2, 4]})


def synthetic_detections() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "cell_id": ["a", "a", "a", "b", "c", "boundary", "a"],
            "acq_datetime": pd.to_datetime(
                [
                    "2023-01-01Z",
                    "2024-09-01Z",
                    "2024-09-08Z",
                    "2023-06-01Z",
                    "2022-01-01Z",
                    "2024-09-01Z",
                    "2024-09-02Z",
                ],
                format="%Y-%m-%dZ",
                utc=True,
            ),
            "type": [0, 0, 0, 0, 0, 0, 1],
            "confidence": "n",
            "in_bolivia": True,
            "frp": np.array([1, 10.1, 20.2, 30, 40, 50, 999], dtype=np.float32),
        }
    )


def test_synthetic_sparse_features_match_benchmark_for_two_issues() -> None:
    from bolivia_fire.live.state import LiveState, aggregate_detections, feature_rows

    cells, detections = synthetic_cells(), synthetic_detections()
    days = pd.DataFrame(
        {
            "date": pd.date_range("2022-01-01", "2024-09-15"),
            "acquired": True,
            "observed": True,
            "source": "SP",
            "bbox_count": 1,
        }
    )
    state = LiveState(aggregate_detections(detections), days).roll(pd.Timestamp("2024-09-15"))
    assert len(state.days) == 400
    benchmark = DailyHistory(cells, detections, days, "2022-09-03", "2024-09-09")
    for issue in ("2024-09-02", "2024-09-09"):
        pd.testing.assert_frame_equal(
            feature_rows(cells, state, pd.Timestamp(issue)), benchmark.features(issue)
        )
    assert feature_rows(cells, state, pd.Timestamp("2024-09-02")).days_since_last.tolist() == [
        1,
        459,
        730,
    ]


def test_synthetic_state_replaces_days_on_resume_and_preserves_old_recency(tmp_path) -> None:
    from bolivia_fire.live.state import LiveState

    counts = pd.DataFrame(
        {
            "date": pd.to_datetime(["2023-12-31", "2025-02-03"]),
            "cell_id": ["a", "b"],
            "n": [3, 2],
            "frp_sum": [1.5, 2.5],
        }
    )
    days = pd.DataFrame(
        {
            "date": pd.date_range("2023-12-31", "2025-02-03"),
            "acquired": True,
            "observed": True,
            "source": "NRT",
            "bbox_count": 1,
        }
    )
    state = LiveState(counts, days).roll(pd.Timestamp("2025-02-03"))
    state.save(tmp_path)
    loaded = LiveState.load(tmp_path)
    assert len(loaded.days) == 400
    assert loaded.counts.n.tolist() == [2]
    assert loaded.prior.set_index("cell_id").loc["a", "date"] == pd.Timestamp("2023-12-31")
    replacement_days = days.iloc[-1:]
    empty = counts.iloc[:0]
    loaded.merge(empty, replacement_days)
    loaded.merge(empty, replacement_days)
    assert loaded.counts.empty
    assert not loaded.days.date.duplicated().any()


def test_synthetic_observation_is_null_if_any_target_day_is_unobserved() -> None:
    from bolivia_fire.live.state import LiveState, observation

    counts = pd.DataFrame(
        {"date": pd.to_datetime(["2026-10-20"]), "cell_id": ["a"], "n": [2], "frp_sum": [1.0]}
    )
    days = pd.DataFrame(
        {
            "date": pd.date_range("2026-10-19", "2026-10-25"),
            "acquired": True,
            "observed": [True] * 6 + [False],
            "source": "NRT",
            "bbox_count": 1,
        }
    )
    result = observation(synthetic_cells(), LiveState(counts, days), pd.Timestamp("2026-10-19"))
    assert result["complete"] is False
    assert result["y"] == [None, None, None]
    assert result["n"] == [2, 0, 0]
    assert result["unobserved_days"] == ["2026-10-25"]
    days.loc[6, "observed"] = True
    result = observation(synthetic_cells(), LiveState(counts, days), pd.Timestamp("2026-10-19"))
    assert result["complete"] is True
    assert result["y"] == [1, 0, 0]
    days.loc[6, "acquired"] = False
    result = observation(synthetic_cells(), LiveState(counts, days), pd.Timestamp("2026-10-19"))
    assert result["complete"] is False
    assert result["y"] == [None, None, None]


def test_synthetic_verification_has_hand_computed_oracles() -> None:
    from bolivia_fire.live.verify import verification_row

    cells = synthetic_cells().assign(national_area_km2=[1.0, 1.0, 18.0])
    forecast = {"issue_date": "2026-10-19", "cell_index": [0, 1, 2], "p": [0.9, 0.6, 0.1]}
    observed = {"complete": True, "cell_index": [0, 1, 2], "y": [1, 0, 1]}
    row = verification_row(cells, forecast, observed, np.array([0.2, 0.3, 0.1]))
    assert row["ap"] == (1 + 2 / 3) / 2
    assert row["brier"] == (0.1**2 + 0.6**2 + 0.9**2) / 3
    assert row["roc_auc"] == 0.5
    assert row["recall_area_5"] == 0.5
    assert row["recall_area_10"] == 0.5
    assert row["climatology_ap"] == (1 / 2 + 2 / 3) / 2
    assert abs(row["ece"] - (0.1 + 0.6 + 0.9) / 3) < 1e-12
    assert verification_row(cells, forecast, {**observed, "complete": False}, np.zeros(3)) is None


def test_synthetic_forecast_publication_is_immutable(tmp_path) -> None:
    from bolivia_fire.live.files import publish_forecast, read_json

    payload = {"issue_date": "2026-10-26", "cell_index": [0], "p": [0.3]}
    assert publish_forecast(tmp_path, payload) is True
    original = (tmp_path / "forecasts/2026-10-26.json").read_bytes()
    assert publish_forecast(tmp_path, payload) is False
    with pytest.raises(ValueError, match="immutable"):
        publish_forecast(tmp_path, {**payload, "p": [0.9]})
    assert (tmp_path / "forecasts/2026-10-26.json").read_bytes() == original
    assert read_json(tmp_path / "forecasts/2026-10-26.json") == payload


@pytest.mark.local_data
def test_sp_live_features_match_saved_panel_exactly_for_two_issues() -> None:
    from bolivia_fire.config import Paths, load_protocol
    from bolivia_fire.detections import resolve_detection_bundle
    from bolivia_fire.live.bootstrap import local_state
    from bolivia_fire.live.state import feature_rows
    from bolivia_fire.panel import fit_climatology

    paths = Paths()
    protocol = load_protocol(paths.root)
    source, days_source, _ = resolve_detection_bundle(paths, protocol)
    days = pd.read_parquet(days_source)
    cells = pd.read_parquet(paths.datasets / "processed/cells.parquet")
    fitted = fit_climatology(source, cells, days, protocol)
    names = [
        "count_last_7d",
        "count_last_14d",
        "count_last_28d",
        "count_last_91d",
        "count_last_365d",
        "any_last_7d",
        "days_since_last",
        "no_prior_detection",
        "frp_sum_28d",
        "same_week_last_year",
        "neighborhood_3x3_last_7d",
        "neighborhood_7x7_last_7d",
        "neighborhood_3x3_last_28d",
        "neighborhood_7x7_last_28d",
        "national_last_7d",
        "national_last_28d",
        "week_of_year_sin",
        "week_of_year_cos",
        "train_climatology_cell",
        "train_climatology_dept",
        "window_obs_complete_365d",
    ]
    for issue in pd.to_datetime(["2021-09-06", "2021-09-13"]):
        state = local_state(source, days, issue - pd.Timedelta(days=1))
        features = feature_rows(cells, state, issue)
        features["train_climatology_cell"] = fitted.predict(issue)
        features["train_climatology_dept"] = fitted.predict_department(issue)
        saved = pd.read_parquet(
            paths.datasets / "processed/panel/year=2021/part.parquet",
            columns=["cell_id", *names],
            filters=[("issue_date", "==", issue)],
        )
        pd.testing.assert_frame_equal(
            features[["cell_id", *names]].reset_index(drop=True),
            saved.reset_index(drop=True),
            check_exact=True,
        )
