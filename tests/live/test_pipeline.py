from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from bolivia_fire.live.state import LiveState, empty_counts


def test_synthetic_forecast_excludes_future_counts_and_marks_backfill() -> None:
    from bolivia_fire.live.pipeline import make_forecast

    cells = pd.DataFrame({"cell_id": ["a"], "row": [1], "col": [1]})
    days = pd.DataFrame(
        {
            "date": pd.date_range("2025-09-01", "2026-10-30"),
            "acquired": True,
            "observed": True,
            "source": "NRT",
            "bbox_count": 1,
            "nrt_rows_removed": 0,
        }
    )
    counts = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-10-18", "2026-10-19"]),
            "cell_id": "a",
            "n": [2, 999],
            "frp_sum": 1.0,
        }
    )
    climate = pd.DataFrame(
        {
            "week_index": [41],
            "cell_id": ["a"],
            "train_climatology_cell": [0.2],
            "train_climatology_dept": [0.2],
        }
    )

    class SyntheticModel:
        card = {"features": ["count_last_7d"], "model_id": "synthetic", "variant": "strict"}

        def predict(self, x):
            return x[:, 0] / 10

    forecast = make_forecast(
        cells,
        LiveState(counts, days),
        climate,
        SyntheticModel(),
        pd.Timestamp("2026-10-19"),
        datetime(2026, 10, 31, tzinfo=UTC),
        {"mask_locations": 1},
    )
    assert np.isclose(forecast["p"][0], 0.2)
    assert forecast["input_window"]["last_day"] == "2026-10-18"
    assert forecast["target_end"] == "2026-10-26"
    assert forecast["prospective"] is False
    assert forecast["publication_lag_hours"] == 288
    assert forecast["input_window"]["unobserved_days"] == []
    assert forecast["nrt_type_proxy"]["mask_locations"] == 1

    scheduled = make_forecast(
        cells,
        LiveState(counts, days),
        climate,
        SyntheticModel(),
        pd.Timestamp("2026-10-19"),
        datetime(2026, 10, 19, 6, tzinfo=UTC),
        {"mask_locations": 1},
        scheduled=True,
    )
    assert scheduled["prospective"] is True
    assert scheduled["publication_lag_hours"] == 6
    late = make_forecast(
        cells,
        LiveState(counts, days),
        climate,
        SyntheticModel(),
        pd.Timestamp("2026-10-19"),
        datetime(2026, 10, 20, 1, tzinfo=UTC),
        {"mask_locations": 1},
        scheduled=True,
    )
    assert late["prospective"] is False


def test_synthetic_default_issue_waits_for_previous_sunday_acquisition() -> None:
    from bolivia_fire.live.pipeline import default_issue

    days = pd.DataFrame(
        {
            "date": pd.date_range("2025-09-01", "2026-10-24"),
            "acquired": True,
            "observed": True,
            "source": "NRT",
            "bbox_count": 1,
        }
    )
    state = LiveState(empty_counts(), days)
    assert default_issue(state, datetime(2026, 10, 26, 6, tzinfo=UTC)) == pd.Timestamp("2026-10-19")
    with pytest.raises(ValueError, match="Monday"):
        from bolivia_fire.live.pipeline import validate_issue

        validate_issue(pd.Timestamp("2026-10-20"), datetime(2026, 10, 31, tzinfo=UTC))


def test_synthetic_cli_registers_live_update_without_reading_credentials() -> None:
    from bolivia_fire.cli import _parser

    args = _parser().parse_args(
        ["live", "update", "--out", "synthetic-output", "--issue", "2026-10-26"]
    )
    assert args.issue.isoformat() == "2026-10-26"
    assert args.out.name == "synthetic-output"


@pytest.mark.parametrize("target_outage", [False, True])
@pytest.mark.parametrize("missed_weeks", [0, 1, 2])
def test_synthetic_update_resumes_without_replacing_forecast(
    tmp_path,
    monkeypatch,
    synthetic_csv,
    synthetic_key,
    target_outage,
    missed_weeks,
) -> None:
    import io

    import httpx
    from shapely.geometry import box, mapping

    from bolivia_fire.config import Paths
    from bolivia_fire.live.files import publish_forecast, read_json, write_json, write_parquet
    from bolivia_fire.live.pipeline import update
    from bolivia_fire.live.source import LiveClient
    from bolivia_fire.live.verify import read_track

    class SyntheticModel:
        card = {"features": ["count_last_7d"], "model_id": "synthetic", "variant": "strict"}

        def predict(self, x):
            return x[:, 0] / 10

    monkeypatch.setattr("bolivia_fire.live.pipeline.FrozenModel", lambda out: SyntheticModel())
    cells = pd.DataFrame(
        {
            "cell_id": ["r1450c2336", "r1451c2336"],
            "row": [1450, 1451],
            "col": [2336, 2336],
            "national_area_km2": [1.0, 1.0],
        }
    )
    write_parquet(tmp_path / "static/cells.parquet", cells)
    write_parquet(
        tmp_path / "static/train_climatology_cell.parquet",
        pd.DataFrame(
            {
                "week_index": [41, 41, 42, 42],
                "cell_id": cells.cell_id.tolist() * 2,
                "train_climatology_cell": 0.2,
                "train_climatology_dept": 0.2,
            }
        ),
    )
    write_parquet(
        tmp_path / "static/static_source_mask.parquet",
        pd.DataFrame({"latitude": pd.Series(dtype=float), "longitude": pd.Series(dtype=float)}),
    )
    write_json(tmp_path / "static/boundaries_adm0_full.json", mapping(box(-64, -18, -63, -17)))
    write_json(
        tmp_path / "static/meta.json",
        {"sources": [], "attribution": [], "static_source_mask": {"mask_locations": 0}},
    )
    write_json(tmp_path / "model/model_card.json", {"model_id": "synthetic"})
    dates = pd.date_range(end="2026-10-24", periods=400)
    state = LiveState(
        empty_counts(),
        pd.DataFrame(
            {
                "date": dates,
                "acquired": True,
                "observed": True,
                "source": "NRT",
                "bbox_count": 1,
                "nrt_rows_removed": 0,
            }
        ),
    )
    forecast_issue = pd.Timestamp("2026-10-19") - pd.Timedelta(weeks=missed_weeks)
    state.counts = pd.DataFrame(
        {
            "date": [forecast_issue + pd.Timedelta(days=1)],
            "cell_id": ["r1450c2336"],
            "n": [1],
            "frp_sum": [1.0],
        }
    )
    extra_issue = forecast_issue + pd.Timedelta(days=7) if missed_weeks == 2 else None
    if extra_issue is not None:
        state.counts = pd.concat(
            [state.counts, state.counts.assign(date=extra_issue + pd.Timedelta(days=1))],
            ignore_index=True,
        )
    state.save(tmp_path)
    previous = {
        "issue_date": str(forecast_issue.date()),
        "cell_index": [0, 1],
        "p": [0.3, 0.2],
        "generated_utc": f"{forecast_issue.date()}T06:00:00+00:00",
        "prospective": True,
        "publication_lag_hours": 6,
    }
    publish_forecast(tmp_path, previous)
    if extra_issue is not None:
        publish_forecast(
            tmp_path,
            {
                **previous,
                "issue_date": str(extra_issue.date()),
                "generated_utc": f"{extra_issue.date()}T06:00:00+00:00",
            },
        )
    forecast_path = tmp_path / f"forecasts/{forecast_issue.date()}.json"
    before = forecast_path.read_bytes()

    def response(request):
        if "/data_availability/" in request.url.path:
            return httpx.Response(
                200, text="data_id,min_date,max_date\nVIIRS_SNPP_NRT,2026-08-01,2026-10-31\n"
            )
        if "outages_suomi_npp" in request.url.path:
            day = (
                (forecast_issue + pd.Timedelta(days=6)).strftime("%Y-%j")
                if target_outage
                else "2012-020"
            )
            return httpx.Response(
                200,
                text=f"<table><tr><td>{day}</td><td>synthetic</td>"
                "<td>00:00</td><td>12:00</td><td>synthetic</td></tr></table>",
            )
        start = request.url.path.rsplit("/", 1)[1]
        frame = pd.read_csv(
            io.BytesIO(synthetic_csv(acq_date=start, version="2.0NRT")), dtype="string"
        ).drop(columns="type")
        return httpx.Response(200, content=frame.to_csv(index=False).encode())

    now = datetime(2026, 10, 31, tzinfo=UTC)
    with LiveClient(
        synthetic_key, tmp_path / "cache/quarantine", transport=httpx.MockTransport(response)
    ) as client:
        update(Paths(), tmp_path, tmp_path / "cache", client, now, pd.Timestamp("2026-10-26"))
        first_counts = LiveState.load(tmp_path).counts.copy()
        current = (tmp_path / "forecasts/2026-10-26.json").read_bytes()
        update(Paths(), tmp_path, tmp_path / "cache", client, now, pd.Timestamp("2026-10-26"))
    assert forecast_path.read_bytes() == before
    assert (tmp_path / "forecasts/2026-10-26.json").read_bytes() == current
    pd.testing.assert_frame_equal(first_counts, LiveState.load(tmp_path).counts)
    observed = read_json(tmp_path / f"observations/{forecast_issue.date()}.json")
    assert observed["complete"] is (not target_outage)
    assert observed["y"] == ([None, None] if target_outage else [1, 0])
    rows = read_track(tmp_path)
    assert "nrt_rows_removed_by_week" in read_json(tmp_path / "static/meta.json")
    extra_issues = {str(extra_issue.date())} if extra_issue is not None else set()
    expected = extra_issues | ({str(forecast_issue.date())} if not target_outage else set())
    assert {row["issue_date"] for row in rows} == expected
    assert all(row["prospective"] == "True" and float(row["ap"]) == 1.0 for row in rows)
    if not target_outage:
        target_outage = True
        with LiveClient(
            synthetic_key, tmp_path / "cache/quarantine", transport=httpx.MockTransport(response)
        ) as client:
            update(Paths(), tmp_path, tmp_path / "cache", client, now, pd.Timestamp("2026-10-26"))
        assert {row["issue_date"] for row in read_track(tmp_path)} == extra_issues
