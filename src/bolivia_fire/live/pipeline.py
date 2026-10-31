from __future__ import annotations

import hashlib
import io
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from bolivia_fire.config import Paths, load_protocol
from bolivia_fire.firms.storage import durable_write
from bolivia_fire.live.files import publish_forecast, read_json, write_json
from bolivia_fire.live.model import FrozenModel
from bolivia_fire.live.source import NRT, PROXY, LiveClient, StaticSourceMask, cached_download
from bolivia_fire.live.state import LiveState, aggregate_detections, feature_rows, observation
from bolivia_fire.live.state import unobserved_days as missing_days
from bolivia_fire.live.static import climatology_for_issue
from bolivia_fire.live.verify import read_track, record_verification, verification_row
from bolivia_fire.outages import OUTAGE_URL, observation_days, parse_outages

LOGGER = logging.getLogger("bolivia_fire.live")
LABEL = "Experimental weekly estimate (near-real-time data)"
LIMITATIONS = [
    "Satellite detections are not counts of fires, ignitions, or burned area.",
    "Clouds, smoke, overpass timing, small fires, and outages can hide detections.",
    "The model was trained on standard-processing detections and is applied to near-real-time "
    "detections; the track record measures how well it holds up.",
    PROXY,
    "Scheduled forecasts published within 24 hours of issue are marked prospective; publication "
    "is normally about six hours into the target week. Inputs exclude those six hours, and "
    "scores cover the full seven-day target. Manual or later publications are backfills. "
    "Live performance summaries must use prospective=true rows only.",
    "Incomplete target weeks have null labels and are not scored. Weekly estimates and scores "
    "can vary substantially; a short track record does not establish reliability.",
    "The strict model is also applied to geographically held-out cells; cell climatology is "
    "missing there, and the comparison baseline uses training department climatology.",
]


def validate_issue(issue: pd.Timestamp, now: datetime) -> None:
    if issue.weekday() != 0 or issue != issue.normalize():
        raise ValueError("Issue must be Monday 00:00 UTC")
    if issue > pd.Timestamp(now).tz_convert("UTC").tz_localize(None):
        raise ValueError("Cannot publish an issue in the future")


def default_issue(state: LiveState, now: datetime) -> pd.Timestamp:
    latest = min(
        pd.Timestamp(now).tz_convert("UTC").tz_localize(None).normalize(),
        state.days.date.max() + pd.Timedelta(days=1),
    )
    issue = latest - pd.Timedelta(days=latest.weekday())
    available = state.days.set_index("date").acquired
    while issue - pd.Timedelta(days=365) >= state.days.date.min():
        if available.reindex(
            pd.date_range(issue - pd.Timedelta(days=365), periods=365), fill_value=False
        ).all():
            return issue
        issue -= pd.Timedelta(days=7)
    raise ValueError("No Monday has a complete acquired 365-day input window")


def proxy_for_window(
    state: LiveState, first: pd.Timestamp, length: int, mask_stats: dict[str, Any]
) -> dict[str, Any]:
    window = state.days[state.days.date.between(first, first + pd.Timedelta(days=length - 1))]
    removed = int(window.nrt_rows_removed.sum()) if "nrt_rows_removed" in window else 0
    return {
        "description": PROXY,
        "mask_locations": mask_stats["mask_locations"],
        "nrt_rows_removed": removed,
        "nrt_rows_removed_by_week": weekly_removals(window),
        "removal_count_scope": "nominal/high rows in request bbox",
    }


def weekly_removals(days: pd.DataFrame) -> list[dict[str, Any]]:
    if "nrt_rows_removed" not in days:
        return []
    nrt = days[days.source.eq("NRT")].copy()
    nrt["week"] = nrt.date - pd.to_timedelta(nrt.date.dt.day_of_week, unit="D")
    grouped = nrt.groupby("week").agg(
        nrt_rows_removed=("nrt_rows_removed", "sum"), nrt_days=("date", "size")
    )
    return [
        {
            "issue_date": str(day.date()),
            "nrt_rows_removed": int(row.nrt_rows_removed),
            "nrt_days": int(row.nrt_days),
        }
        for day, row in grouped.iterrows()
    ]


def update_metadata(out: Path, state: LiveState, generated: datetime, outage_hash: str) -> None:
    weekly = weekly_removals(state.days)
    meta = read_json(out / "static/meta.json")
    meta.update(
        state_first_day=str(state.days.date.min().date()),
        state_last_day=str(state.days.date.max().date()),
        last_updated_utc=generated.isoformat(),
        outage_sha256=outage_hash,
        nrt_rows_removed_by_week=weekly,
    )
    write_json(out / "static/meta.json", meta)
    card = read_json(out / "model/model_card.json")
    card["nrt_rows_removed_by_week"] = weekly
    write_json(out / "model/model_card.json", card)


def make_forecast(
    cells: pd.DataFrame,
    state: LiveState,
    climate: pd.DataFrame,
    model: FrozenModel,
    issue: pd.Timestamp,
    generated: datetime,
    mask_stats: dict[str, Any],
    *,
    scheduled: bool = False,
) -> dict[str, Any]:
    validate_issue(issue, generated)
    features = feature_rows(cells, state, issue)
    climatology, _ = climatology_for_issue(cells, climate, issue)
    features = pd.concat([features, climatology], axis=1)
    probability = model.predict(features[model.card["features"]].to_numpy(dtype="float32"))
    first = issue - pd.Timedelta(days=365)
    lag = (generated - issue.tz_localize("UTC").to_pydatetime()).total_seconds() / 3600
    prospective = scheduled and 0 <= lag <= 24
    return {
        "issue_date": issue.date().isoformat(),
        "target_start": issue.date().isoformat(),
        "target_end": (issue + pd.Timedelta(days=7)).date().isoformat(),
        "model_id": model.card["model_id"],
        "variant": model.card["variant"],
        "generated_utc": generated.isoformat(),
        "prospective": prospective,
        "publication_lag_hours": lag,
        "publication_note": ""
        if prospective
        else "backfill: generated after issue time using only inputs acquired before the issue",
        "input_window": {
            "first_day": first.date().isoformat(),
            "last_day": (issue - pd.Timedelta(days=1)).date().isoformat(),
            "unobserved_days": missing_days(state, first, 365),
        },
        "cell_index": list(range(len(cells))),
        "p": probability.tolist(),
        "nrt_type_proxy": proxy_for_window(state, first, 365, mask_stats),
    }


def refresh_outages(client: LiveClient, cache: Path) -> tuple[pd.DataFrame, str]:
    with client.http.stream("GET", OUTAGE_URL) as response:
        response.raise_for_status()
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > 4 * 1024 * 1024:
                raise ValueError("MODAPS outage response exceeds 4 MiB")
    outages = parse_outages(body.decode("utf-8"))
    durable_write(cache / "outages.html", bytes(body))
    return outages, hashlib.sha256(body).hexdigest()


def availability(client: LiveClient, now: datetime) -> tuple[pd.Timestamp, pd.Timestamp]:
    table = pd.read_csv(io.BytesIO(client.availability())).set_index("data_id")
    first, last = map(pd.Timestamp, table.loc[NRT, ["min_date", "max_date"]])
    # A date advertised during the current UTC day is only partially acquired.
    closed = pd.Timestamp(now).tz_convert("UTC").tz_localize(None).normalize() - pd.Timedelta(
        days=1
    )
    return first, min(last, closed)


def fetch_interval(
    state: LiveState,
    client: LiveClient,
    cache: Path,
    product: str,
    first: pd.Timestamp,
    last: pd.Timestamp,
    bbox: tuple[float, float, float, float],
    country: Any,
    mask: StaticSourceMask | None = None,
) -> None:
    from bolivia_fire.live.source import parse_download

    day = first
    while day <= last:
        length = min(5, (last - day).days + 1)
        body, metadata = cached_download(client, cache, product, day.date(), length, bbox)
        raw = pd.read_csv(io.BytesIO(body), dtype="string", keep_default_na=False).drop_duplicates()
        parsed, _ = parse_download(body, product, country, mask)
        counts = aggregate_detections(parsed)
        dates = pd.date_range(day, periods=length)
        bbox_count = raw.acq_date.value_counts()
        removed = pd.Series(dtype="int64")
        if product == NRT and mask is not None:
            selected = raw[raw.confidence.isin(["n", "h"])]
            removed = selected[mask.excluded(selected)].acq_date.value_counts()
        days = pd.DataFrame(
            {
                "date": dates,
                "acquired": True,
                "observed": True,
                "source": "NRT" if product == NRT else "SP",
                "bbox_count": [int(bbox_count.get(str(value.date()), 0)) for value in dates],
                "nrt_rows_removed": [int(removed.get(str(value.date()), 0)) for value in dates],
            }
        )
        state.merge(counts, days)
        LOGGER.info(
            "product=%s start=%s days=%d rows=%d versions=%s",
            product,
            day.date(),
            length,
            metadata["rows"],
            ",".join(metadata["versions"]),
        )
        day += pd.Timedelta(days=length)


def rebuild_index(out: Path, generated: datetime) -> None:
    meta = read_json(out / "static/meta.json")
    verified = {row["issue_date"] for row in read_track(out)}
    issues = []
    for path in sorted((out / "forecasts").glob("*.json")):
        forecast = read_json(path)
        issue = path.stem
        issues.append(
            {
                "issue_date": issue,
                "forecast": True,
                "observation": (out / "observations" / path.name).is_file(),
                "verified": issue in verified,
                "prospective": forecast["prospective"],
            }
        )
    write_json(
        out / "index.json",
        {
            "label": LABEL,
            "issues": issues,
            "latest_issue": issues[-1]["issue_date"] if issues else None,
            "last_updated_utc": generated.isoformat(),
            "sources": meta["sources"],
            "attribution": meta["attribution"],
            "limitations": LIMITATIONS,
            "nrt_type_proxy": {"description": PROXY, **meta["static_source_mask"]},
            "nrt_rows_removed_by_week": meta.get("nrt_rows_removed_by_week", []),
        },
    )
    total = sum(
        path.stat().st_size
        for path in out.rglob("*")
        if path.is_file() and ".git" not in path.parts
    )
    if total >= 200_000_000:
        raise ValueError("Live-data exceeds the 200 MB publication limit; archive older outputs")


def publish_cycle(
    out: Path,
    state: LiveState,
    issue: pd.Timestamp,
    generated: datetime,
    *,
    initialize: bool = False,
    scheduled: bool = False,
) -> dict[str, Any]:
    model = FrozenModel(out)
    cells = pd.read_parquet(out / "static/cells.parquet")
    climate = pd.read_parquet(out / "static/train_climatology_cell.parquet")
    mask_stats = read_json(out / "static/meta.json")["static_source_mask"]
    previous = issue - pd.Timedelta(days=7)
    if previous < state.days.date.min():
        raise ValueError("Previous target week is older than the retained daily state")
    for current in [previous, issue] if initialize else [issue]:
        destination = out / f"forecasts/{current.date()}.json"
        if not destination.exists():
            publish_forecast(
                out,
                make_forecast(
                    cells,
                    state,
                    climate,
                    model,
                    current,
                    generated,
                    mask_stats,
                    scheduled=scheduled,
                ),
            )
    row = None
    verified = {item["issue_date"] for item in read_track(out)}
    scored = []
    for forecast_path in sorted((out / "forecasts").glob("*.json")):
        current = pd.Timestamp(forecast_path.stem)
        observed_path = out / "observations" / forecast_path.name
        if current < state.days.date.min():
            if forecast_path.stem in verified or not observed_path.exists():
                continue
            observed = read_json(observed_path)
        else:
            observed = observation(cells, state, current)
            observed["nrt_type_proxy"] = proxy_for_window(state, current, 7, mask_stats)
            write_json(observed_path, observed)
        _, baseline = climatology_for_issue(cells, climate, current)
        current_row = verification_row(cells, read_json(forecast_path), observed, baseline)
        record_verification(out, forecast_path.stem, current_row)
        if current == previous:
            row = current_row
        if current_row is not None:
            scored.append(current_row["issue_date"])
    rebuild_index(out, generated)
    return {
        "issue_date": str(issue.date()),
        "previous_issue": str(previous.date()),
        "verification": row,
        "verified_issues": scored,
    }


def update(
    paths: Paths,
    out: Path,
    cache: Path,
    client: LiveClient,
    now: datetime,
    issue: pd.Timestamp | None = None,
    *,
    scheduled: bool = False,
) -> dict[str, Any]:
    from shapely.geometry import shape

    state = LiveState.load(out)
    first, last = availability(client, now)
    if last < state.days.date.max():
        raise ValueError("FIRMS availability predates stored state")
    grid_country = read_json(out / "static/boundaries_adm0_full.json")
    country = shape(grid_country)
    mask = StaticSourceMask(pd.read_parquet(out / "static/static_source_mask.parquet"))
    available = state.days.set_index("date").acquired
    requested = pd.date_range(max(first, state.days.date.min()), last)
    missing = requested[~available.reindex(requested, fill_value=False).to_numpy(dtype=bool)]
    bbox = tuple(load_protocol(paths.root)["study_area"]["acquisition_bbox"])
    for day in missing:
        if day in set(state.days.loc[state.days.acquired, "date"]):
            continue
        end = min(day + pd.Timedelta(days=4), last)
        fetch_interval(state, client, cache, NRT, day, end, bbox, country, mask)
        state.save(out)
    outages, outage_hash = refresh_outages(client, cache)
    state.days = observation_days(state.days.drop(columns="outage_hours", errors="ignore"), outages)
    state.roll(last).save(out)
    update_metadata(out, state, now, outage_hash)
    selected_issue = issue if issue is not None else default_issue(state, now)
    validate_issue(selected_issue, now)
    result = publish_cycle(out, state, selected_issue, now, scheduled=scheduled)
    result.update(
        nrt_days=int(state.days.source.eq("NRT").sum()),
        unobserved_days=[
            str(value.date()) for value in state.days.loc[~state.days.observed, "date"]
        ],
        outage_sha256=outage_hash,
        data_requests=client.data_requests,
    )
    return result
