from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from bolivia_fire.live.state import LiveState, empty_prior, feature_rows


def test_synthetic_as_of_state_excludes_future_and_preserves_old_recency():
    from bolivia_fire.live.backfill import as_of_state

    cells = pd.DataFrame({"cell_id": ["a", "b"], "row": [1, 1], "col": [1, 2]})
    state = LiveState(
        pd.DataFrame(
            {
                "date": pd.to_datetime(["2025-02-01", "2026-10-04", "2026-10-05"]),
                "cell_id": ["a", "b", "b"],
                "n": [1, 2, 999],
                "frp_sum": [1.0, 2.0, 999.0],
            }
        ),
        pd.DataFrame(
            {"date": pd.date_range("2024-01-01", "2026-11-01"), "acquired": True, "observed": True}
        ),
        empty_prior(),
    )
    selected = as_of_state(state, pd.Timestamp("2026-10-05"))
    features = feature_rows(cells, selected, pd.Timestamp("2026-10-05"))
    assert features.count_last_7d.tolist() == [0, 2]
    assert features.days_since_last.tolist() == [611, 1]
    assert features.no_prior_detection.tolist() == [False, False]
    assert len(selected.days) == 400
    assert state.counts.n.tolist() == [1, 2, 999]


@pytest.fixture
def synthetic_archive(tmp_path, monkeypatch):
    from bolivia_fire.live.files import publish_forecast, write_json, write_parquet

    cells = pd.DataFrame(
        {"cell_id": ["a", "b"], "row": [1, 1], "col": [1, 2], "national_area_km2": [1.0, 1.0]}
    )
    counts = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-10-06", "2026-10-13"]),
            "cell_id": ["a", "b"],
            "n": [1, 1],
            "frp_sum": [1.0, 1.0],
        }
    )
    days = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", "2026-11-01"),
            "acquired": True,
            "observed": True,
            "source": "NRT",
            "nrt_rows_removed": 0,
        }
    )
    days.loc[days.date.eq("2026-10-16"), "observed"] = False
    timeline = LiveState(counts, days)
    out = tmp_path / "archive"
    timeline.roll(pd.Timestamp("2026-11-01")).save(out)
    write_parquet(out / "static/cells.parquet", cells)
    climate = pd.DataFrame(
        [
            {
                "week_index": week,
                "cell_id": cell,
                "train_climatology_cell": 0.2,
                "train_climatology_dept": 0.2,
            }
            for week in range(52)
            for cell in cells.cell_id
        ]
    )
    write_parquet(out / "static/train_climatology_cell.parquet", climate)
    write_json(out / "static/meta.json", {"static_source_mask": {"mask_locations": 1}})
    retirement = {
        "status": "ended",
        "final_issue": "2026-11-02",
        "ended_utc": "2026-11-02T15:40:00Z",
    }
    write_json(
        out / "index.json",
        {
            "retirement": retirement,
            "latest_issue": "2026-11-02",
            "last_updated_utc": "2026-11-09T00:00:00Z",
            "issues": [
                {
                    "issue_date": "2026-10-19",
                    "forecast": True,
                    "observation": False,
                    "verified": True,
                }
            ],
        },
    )
    publish_forecast(out, {"issue_date": "2026-10-19", "p": [0.2, 0.3], "prospective": False})
    (out / "verification").mkdir()
    (out / "verification/track_record.csv").write_bytes(
        b"issue_date,n_cells,positives,ap,brier,roc_auc,recall_area_5,recall_area_10,"
        b"ece,climatology_ap,prospective,generated_utc,publication_lag_hours,notes\n"
        b"2026-10-19,2,1,0.5,0.2,0.5,0.1,0.2,0.1,0.2,False,"
        b"2026-10-31T00:00:00Z,288,synthetic existing row\n"
    )

    class SyntheticModel:
        card = {"features": ["count_last_7d"], "model_id": "synthetic", "variant": "strict"}

        def predict(self, features):
            return np.full(len(features), 0.3, dtype=np.float32)

    monkeypatch.setattr("bolivia_fire.live.backfill.reconstruct_state", lambda *args: timeline)
    monkeypatch.setattr("bolivia_fire.live.backfill.FrozenModel", lambda *args: SyntheticModel())
    return out, tmp_path / "cache", retirement


def snapshot(out):
    return {p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*") if p.is_file()}


def test_synthetic_backfill_preserves_existing_files_scores_and_retirement(synthetic_archive):
    from bolivia_fire.config import Paths
    from bolivia_fire.live.backfill import backfill
    from bolivia_fire.live.files import read_json
    from bolivia_fire.live.verify import read_track

    out, cache, retirement = synthetic_archive
    before = snapshot(out)
    result = backfill(
        Paths(),
        out,
        cache,
        pd.Timestamp("2026-10-05"),
        pd.Timestamp("2026-10-12"),
        datetime(2026, 11, 9, tzinfo=UTC),
    )
    assert result["added_issues"] == ["2026-10-05", "2026-10-12"]
    assert [row["issue_date"] for row in read_track(out)] == ["2026-10-05", "2026-10-19"]
    assert (
        (out / "verification/track_record.csv")
        .read_bytes()
        .endswith(before["verification/track_record.csv"].split(b"\n", 1)[1])
    )
    assert (out / "forecasts/2026-10-19.json").read_bytes() == before["forecasts/2026-10-19.json"]
    for name in before:
        if name.startswith("state/"):
            assert (out / name).read_bytes() == before[name]
    assert read_json(out / "observations/2026-10-12.json")["y"] == [None, None]
    assert read_json(out / "forecasts/2026-10-05.json")["prospective"] is False
    assert read_json(out / "index.json")["retirement"] == retirement
    after = snapshot(out)
    assert (
        backfill(
            Paths(),
            out,
            cache,
            pd.Timestamp("2026-10-05"),
            pd.Timestamp("2026-10-12"),
            datetime(2026, 11, 10, tzinfo=UTC),
        )["added_issues"]
        == []
    )
    assert snapshot(out) == after


def test_synthetic_backfill_refuses_existing_forecast(synthetic_archive):
    from bolivia_fire.config import Paths
    from bolivia_fire.live.backfill import backfill

    out, cache, _ = synthetic_archive
    before = snapshot(out)
    with pytest.raises(ValueError, match="existing forecast"):
        backfill(
            Paths(),
            out,
            cache,
            pd.Timestamp("2026-10-19"),
            pd.Timestamp("2026-10-19"),
            datetime(2026, 11, 9, tzinfo=UTC),
        )
    assert snapshot(out) == before


def test_synthetic_cache_only_missing_response_cannot_request_data(tmp_path):
    from datetime import date

    from bolivia_fire.live.source import NRT, cached_download

    with pytest.raises(ValueError, match="Missing cached"):
        cached_download(None, tmp_path, NRT, date(2026, 8, 1), 5, (-70, -23, -57, -9))
    assert list(tmp_path.iterdir()) == []


def test_synthetic_missing_input_day_refuses_before_writes(synthetic_archive, monkeypatch):
    from bolivia_fire.config import Paths
    from bolivia_fire.live.backfill import backfill

    out, cache, _ = synthetic_archive
    timeline = LiveState.load(out)
    timeline.days.loc[timeline.days.date.eq("2026-10-04"), "acquired"] = False
    monkeypatch.setattr("bolivia_fire.live.backfill.reconstruct_state", lambda *args: timeline)
    before = snapshot(out)
    with pytest.raises(ValueError, match="365-day"):
        backfill(
            Paths(),
            out,
            cache,
            pd.Timestamp("2026-10-05"),
            pd.Timestamp("2026-10-12"),
            datetime(2026, 11, 9, tzinfo=UTC),
        )
    assert snapshot(out) == before


def test_synthetic_completed_batch_refuses_a_changed_forecast(synthetic_archive):
    from bolivia_fire.config import Paths
    from bolivia_fire.live.backfill import backfill

    out, cache, _ = synthetic_archive
    args = (
        Paths(),
        out,
        cache,
        pd.Timestamp("2026-10-05"),
        pd.Timestamp("2026-10-12"),
        datetime(2026, 11, 9, tzinfo=UTC),
    )
    backfill(*args)
    (out / "forecasts/2026-10-05.json").write_bytes(b"{}")
    before = snapshot(out)
    with pytest.raises(ValueError, match="Existing backfill output changed"):
        backfill(*args)
    assert snapshot(out) == before


@pytest.mark.local_data
def test_recorded_backfill_matches_published_probabilities_and_features():
    import os
    from pathlib import Path

    from bolivia_fire.config import Paths
    from bolivia_fire.live.backfill import backfill

    if not os.environ.get("LIVE_DATA_DIR") or not os.environ.get("LIVE_CACHE_DIR"):
        pytest.skip("Set LIVE_DATA_DIR and LIVE_CACHE_DIR for the recorded parity fixture")
    result = backfill(
        Paths(),
        Path(os.environ["LIVE_DATA_DIR"]),
        Path(os.environ["LIVE_CACHE_DIR"]),
        pd.Timestamp("2026-10-19"),
        pd.Timestamp("2026-10-26"),
        datetime.now(UTC),
        check_parity=True,
    )
    assert [(item["cells"], item["features"]) for item in result["parity"]] == [
        (37146, 21),
        (37146, 21),
    ]
    assert all(item["features_equal"] and item["probabilities_equal"] for item in result["parity"])
