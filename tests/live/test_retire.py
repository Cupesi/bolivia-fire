from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pandas as pd
import pytest

from bolivia_fire.live.files import read_json, write_json
from bolivia_fire.live.state import LiveState, empty_counts


def test_synthetic_delivery_end_uses_outage_union_and_blocks_later_acquisition() -> None:
    from bolivia_fire.live.coverage import apply_coverage

    days = pd.DataFrame({"date": pd.date_range("2026-11-01", "2026-11-04"), "acquired": True})
    outages = pd.DataFrame(
        {
            "start_utc": pd.to_datetime(["2026-11-02T14:00Z"], utc=True),
            "end_utc": pd.to_datetime(["2026-11-02T18:00Z"], utc=True),
        }
    )
    result = apply_coverage(days, outages)
    assert result.outage_hours.tolist() == pytest.approx([0, 10, 24, 24])
    assert result.acquired.tolist() == [True, True, False, False]
    assert result.observed.tolist() == [True, False, False, False]

    empty = outages.iloc[:0]
    assert apply_coverage(days, empty).outage_hours.tolist() == pytest.approx([0, 25 / 3, 24, 24])


def test_synthetic_fetch_never_acquires_days_after_delivery_end(tmp_path, monkeypatch):
    from bolivia_fire.live.pipeline import fetch_interval
    from bolivia_fire.live.source import NRT

    requested = []

    def cached(client, cache, product, first, length, bbox):
        requested.append((str(first), length))
        return b"acq_date,confidence\n", {"rows": 0, "versions": []}

    monkeypatch.setattr("bolivia_fire.live.pipeline.cached_download", cached)
    monkeypatch.setattr("bolivia_fire.live.source.parse_download", lambda *args: (None, {}))
    monkeypatch.setattr(
        "bolivia_fire.live.pipeline.aggregate_detections", lambda raw: empty_counts()
    )
    days = pd.DataFrame({"date": pd.to_datetime([]), "acquired": pd.Series(dtype=bool)})
    state = LiveState(empty_counts(), days)
    for first in ("2026-11-02", "2026-11-03"):
        fetch_interval(
            state,
            None,
            tmp_path,
            NRT,
            pd.Timestamp(first),
            pd.Timestamp("2026-11-09"),
            (0, 0, 1, 1),
            None,
        )
    assert requested == [("2026-11-02", 1)]
    assert state.days.date.tolist() == [pd.Timestamp("2026-11-02")]


@pytest.fixture
def synthetic_modaps(monkeypatch):
    from bolivia_fire.outages import OUTAGE_URL

    response = {
        "body": b"<table><tr><td>2012-020</td><td>synthetic</td>"
        b"<td>00:00</td><td>12:00</td><td>synthetic</td></tr></table>",
        "status": 200,
        "requests": [],
    }
    original_client = httpx.Client

    def handler(request):
        assert str(request.url) == OUTAGE_URL
        assert request.method == "GET"
        assert "authorization" not in request.headers
        response["requests"].append(request)
        return httpx.Response(response["status"], content=response["body"])

    def client(*args, **kwargs):
        return original_client(*args, **kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr("bolivia_fire.live.retire.httpx.Client", client)
    return response


@pytest.fixture
def synthetic_archive(tmp_path: Path, synthetic_modaps) -> tuple[Path, Path]:
    out, cache = tmp_path / "archive", tmp_path / "cache"
    cache.mkdir()
    (cache / "outages.html").write_text("synthetic stale cache", encoding="utf-8")
    state = LiveState(
        empty_counts(),
        pd.DataFrame(
            {
                "date": pd.date_range("2026-10-19", "2026-11-09"),
                "acquired": True,
                "observed": True,
                "outage_hours": 0.0,
                "bbox_count": 0,
            }
        ),
    )
    state.save(out)
    write_json(out / "index.json", {"latest_issue": "2026-11-02", "issues": []})
    write_json(out / "static/meta.json", {"outage_sha256": "synthetic"})
    write_json(out / "forecasts/2026-11-02.json", {"issue_date": "2026-11-02"})
    write_json(
        out / "observations/2026-11-02.json",
        {
            "issue_date": "2026-11-02",
            "complete": True,
            "unobserved_days": [],
            "cell_index": [0],
            "y": [0],
            "n": [0],
        },
    )
    (out / "verification").mkdir()
    (out / "verification/track_record.csv").write_text(
        "issue_date,ap\n2026-10-19,0.2\n", encoding="utf-8"
    )
    return out, cache


def snapshot(out: Path) -> dict[str, bytes]:
    return {
        path.relative_to(out).as_posix(): path.read_bytes()
        for path in out.rglob("*")
        if path.is_file()
    }


def test_synthetic_retirement_fetches_only_modaps_and_is_idempotent(
    synthetic_archive, synthetic_modaps, monkeypatch
):
    from bolivia_fire.cli import _parser
    from bolivia_fire.config import Paths

    out, cache = synthetic_archive
    before = snapshot(out)

    def forbidden(*args, **kwargs):
        pytest.fail("Retirement must not load credentials, request FIRMS data or run inference")

    monkeypatch.setattr("bolivia_fire.live_cli.load_key", forbidden)
    monkeypatch.setattr("bolivia_fire.live_cli.LiveClient", forbidden)
    monkeypatch.setattr("bolivia_fire.live.pipeline.FrozenModel", forbidden)
    args = _parser().parse_args(["live", "retire", "--out", str(out), "--cache", str(cache)])
    assert args.pipeline_handler(args, Paths()) == 0
    assert (cache / "outages.html").read_bytes() == synthetic_modaps["body"]
    assert len(synthetic_modaps["requests"]) == 1
    after = snapshot(out)
    assert after["forecasts/2026-11-02.json"] == before["forecasts/2026-11-02.json"]
    assert after["verification/track_record.csv"] == before["verification/track_record.csv"]
    observed = read_json(out / "observations/2026-11-02.json")
    assert observed["unobserved_days"] == [f"2026-11-{day:02}" for day in range(2, 9)]
    assert observed["complete"] is False
    assert observed["y"] == [None]
    assert observed["n"] == [0]
    retirement = read_json(out / "index.json")["retirement"]
    assert retirement["status"] == "ended"
    assert retirement["ended_utc"] == "2026-11-02T15:40:00Z"
    assert retirement["final_issue"] == "2026-11-02"
    assert retirement == read_json(out / "static/meta.json")["retirement"]
    assert args.pipeline_handler(args, Paths()) == 0
    assert len(synthetic_modaps["requests"]) == 2
    assert snapshot(out) == after
    for operation in ("update", "bootstrap"):
        args = _parser().parse_args(["live", operation, "--out", str(out)])
        assert args.pipeline_handler(args, Paths()) == 1
        assert snapshot(out) == after


def test_synthetic_retirement_withdraws_only_incomplete_score(synthetic_archive):
    from bolivia_fire.live.retire import retire
    from bolivia_fire.live.verify import TRACK_COLUMNS, read_track, record_verification

    out, cache = synthetic_archive
    for issue in ("2026-10-19", "2026-11-02"):
        record_verification(out, issue, dict.fromkeys(TRACK_COLUMNS, "") | {"issue_date": issue})
    result = retire(out, cache)
    assert result["withdrawn_issues"] == ["2026-11-02"]
    assert [row["issue_date"] for row in read_track(out)] == ["2026-10-19"]


@pytest.mark.parametrize("status", [503, 401])
def test_synthetic_retirement_http_failure_changes_nothing(
    synthetic_archive, synthetic_modaps, status
):
    from bolivia_fire.live.retire import retire

    out, _ = synthetic_archive
    synthetic_modaps["status"] = status
    before = snapshot(out)
    with pytest.raises(httpx.HTTPStatusError):
        retire(out, out / "unused-cache")
    assert snapshot(out) == before


def test_synthetic_retirement_rejects_oversize_response(synthetic_archive, synthetic_modaps):
    from bolivia_fire.live.retire import retire

    out, cache = synthetic_archive
    synthetic_modaps["body"] = b"x" * (4 * 1024 * 1024 + 1)
    before = snapshot(out)
    before_cache = (cache / "outages.html").read_bytes()
    with pytest.raises(ValueError, match="exceeds 4 MiB"):
        retire(out, cache)
    assert snapshot(out) == before
    assert (cache / "outages.html").read_bytes() == before_cache


def test_synthetic_pipeline_refuses_retired_archive_before_work(synthetic_archive):
    from bolivia_fire.config import Paths
    from bolivia_fire.live.bootstrap import bootstrap
    from bolivia_fire.live.pipeline import update
    from bolivia_fire.live.retire import retire

    out, cache = synthetic_archive
    retire(out, cache)
    before = snapshot(out)
    for operation in (bootstrap, update):
        with pytest.raises(ValueError, match="ended"):
            operation(Paths(), out, cache, None, datetime(2026, 11, 9, tzinfo=UTC))
    assert snapshot(out) == before


def test_synthetic_retirement_refuses_coverage_regressions_and_names_days(synthetic_archive):
    from bolivia_fire.live.retire import retire

    out, cache = synthetic_archive
    state = LiveState.load(out)
    state.days["outage_hours"] = 0.0
    state.days.loc[state.days.date.eq("2026-10-27"), "outage_hours"] = 1.5
    state.days.loc[state.days.date.eq("2026-10-31"), "outage_hours"] = 0.4
    state.days.loc[state.days.date.eq("2026-11-01"), "observed"] = False
    state.save(out)
    before = snapshot(out)
    with pytest.raises(ValueError) as error:
        retire(out, cache)
    assert str(error.value) == (
        "Retirement refuses coverage regression on: 2026-10-27, 2026-10-31, 2026-11-01"
    )
    assert snapshot(out) == before


def test_synthetic_retirement_preserves_refreshed_short_outages(
    synthetic_archive, synthetic_modaps
):
    from bolivia_fire.live.retire import retire

    out, cache = synthetic_archive
    synthetic_modaps["body"] = (
        b"<table><tr><td>2026-300</td><td>synthetic</td>"
        b"<td>19:50</td><td>21:20</td><td>synthetic</td></tr>"
        b"<tr><td>2026-304</td><td>synthetic</td>"
        b"<td>23:36</td><td>00:00</td><td>synthetic</td></tr></table>"
    )
    state = LiveState.load(out)
    state.days.loc[state.days.date.eq("2026-10-27"), "outage_hours"] = 1.5
    state.days.loc[state.days.date.eq("2026-10-31"), "outage_hours"] = 0.4
    state.save(out)
    retire(out, cache)
    days = LiveState.load(out).days.set_index("date")
    assert days.loc[pd.Timestamp("2026-10-27"), "outage_hours"] == 1.5
    assert days.loc[pd.Timestamp("2026-10-31"), "outage_hours"] == 0.4
    assert days.loc[pd.Timestamp("2026-10-27"), "observed"]
    assert days.loc[pd.Timestamp("2026-10-31"), "observed"]
