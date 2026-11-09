from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from shapely.geometry import shape

from bolivia_fire.config import Paths, load_protocol
from bolivia_fire.detections import resolve_detection_bundle
from bolivia_fire.firms.storage import durable_write, sha256_file
from bolivia_fire.live.bootstrap import local_state
from bolivia_fire.live.coverage import apply_coverage
from bolivia_fire.live.files import publish_forecast, read_json, write_json
from bolivia_fire.live.model import FrozenModel
from bolivia_fire.live.pipeline import (
    fetch_interval,
    make_forecast,
    proxy_for_window,
    validate_issue,
)
from bolivia_fire.live.source import NRT, SP, StaticSourceMask
from bolivia_fire.live.state import LiveState, feature_rows, observation
from bolivia_fire.live.static import climatology_for_issue
from bolivia_fire.live.verify import TRACK_COLUMNS, read_track, verification_row
from bolivia_fire.outages import parse_outages


def encoded(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def reconstruct_state(paths: Paths, out: Path, cache: Path, last: pd.Timestamp) -> LiveState:
    protocol = load_protocol(paths.root)
    source, days_source, _ = resolve_detection_bundle(paths, protocol)
    state = local_state(source, pd.read_parquet(days_source), pd.Timestamp("2026-01-04"))
    country = shape(read_json(out / "static/boundaries_adm0_full.json"))
    mask = StaticSourceMask(pd.read_parquet(out / "static/static_source_mask.parquet"))
    bbox = tuple(protocol["study_area"]["acquisition_bbox"])
    for product, first, stop in (
        (SP, pd.Timestamp("2026-01-05"), pd.Timestamp("2026-07-31")),
        (NRT, pd.Timestamp("2026-08-01"), last),
    ):
        for path in sorted((cache / "raw" / product).glob("*.csv")):
            start, length_text = path.stem.rsplit("_", 1)
            start, length = pd.Timestamp(start), int(length_text.removesuffix("d"))
            end = start + pd.Timedelta(days=length - 1)
            if start <= stop and end >= first:
                fetch_interval(state, None, cache, product, start, end, bbox, country, mask)
    state.counts = state.counts[state.counts.date <= last].copy()
    state.days = state.days[state.days.date <= last].copy()
    body = (cache / "outages.html").read_bytes()
    meta = read_json(out / "static/meta.json")
    if hashlib.sha256(body).hexdigest() != meta["outage_sha256"]:
        raise ValueError("Cached MODAPS response differs from the archive's recorded digest")
    state.days = apply_coverage(state.days, parse_outages(body.decode("utf-8")))
    recorded = LiveState.load(out).days
    overlap = recorded.merge(state.days, on="date", suffixes=("_recorded", "_rebuilt"))
    regressed = ~overlap.observed_recorded & overlap.observed_rebuilt
    if "outage_hours_recorded" in overlap:
        regressed |= overlap.outage_hours_rebuilt.lt(overlap.outage_hours_recorded)
    if regressed.any():
        dates = overlap.loc[regressed, "date"].dt.strftime("%Y-%m-%d")
        raise ValueError(f"Backfill refuses coverage regression on: {', '.join(dates)}")
    return state


def as_of_state(state: LiveState, issue: pd.Timestamp) -> LiveState:
    last = issue - pd.Timedelta(days=1)
    result = LiveState(
        state.counts[state.counts.date < issue].copy(),
        state.days[state.days.date < issue].copy(),
        state.prior[state.prior.date < issue].copy(),
    ).roll(last)
    dates = pd.date_range(issue - pd.Timedelta(days=365), periods=365)
    if not result.days.set_index("date").acquired.reindex(dates, fill_value=False).all():
        raise ValueError(f"Issue {issue.date()} requires a complete acquired 365-day input window")
    return result


def parity(
    out: Path, timeline: LiveState, issues: pd.DatetimeIndex, generated: datetime
) -> dict[str, Any]:
    cells = pd.read_parquet(out / "static/cells.parquet")
    climate = pd.read_parquet(out / "static/train_climatology_cell.parquet")
    model = FrozenModel(out)
    mask = read_json(out / "static/meta.json")["static_source_mask"]
    published_state = LiveState.load(out)
    comparisons = []
    for issue in issues:
        current = as_of_state(timeline, issue)
        actual = feature_rows(cells, current, issue)
        reference = feature_rows(cells, published_state, issue)
        climate_features, _ = climatology_for_issue(cells, climate, issue)
        actual = pd.concat([actual, climate_features], axis=1)[model.card["features"]]
        reference = pd.concat([reference, climate_features], axis=1)[model.card["features"]]
        pd.testing.assert_frame_equal(actual, reference, check_exact=True)
        forecast = make_forecast(cells, current, climate, model, issue, generated, mask)
        published = read_json(out / f"forecasts/{issue.date()}.json")
        if forecast["p"] != published["p"]:
            raise ValueError(f"Forecast probabilities differ for {issue.date()}")
        comparisons.append(
            {
                "issue_date": str(issue.date()),
                "cells": len(cells),
                "features": len(model.card["features"]),
                "features_equal": True,
                "probabilities_equal": True,
                "features_sha256": hashlib.sha256(
                    actual.to_numpy(dtype=np.float64).tobytes()
                ).hexdigest(),
                "probabilities_sha256": hashlib.sha256(
                    np.asarray(forecast["p"], dtype=np.float32).tobytes()
                ).hexdigest(),
            }
        )
    return {"parity": comparisons}


def backfill(
    paths: Paths,
    out: Path,
    cache: Path,
    first: pd.Timestamp,
    last: pd.Timestamp,
    generated: datetime,
    *,
    check_parity: bool = False,
) -> dict[str, Any]:
    validate_issue(first, generated)
    validate_issue(last, generated)
    if last < first:
        raise ValueError("Backfill end precedes its start")
    index = read_json(out / "index.json")
    retirement = index.get("retirement", {})
    if retirement.get("status") != "ended":
        raise ValueError("Backfill requires a retired archive")
    if last > pd.Timestamp(retirement["final_issue"]):
        raise ValueError("Backfill cannot pass retirement.final_issue")
    issues = pd.date_range(first, last, freq="7D")
    if check_parity:
        timeline = reconstruct_state(paths, out, cache, last - pd.Timedelta(days=1))
        return parity(out, timeline, issues, generated)
    batch_key = f"{first.date()}/{last.date()}"
    batch = index.get("backfill_batches", {}).get(batch_key)
    if batch is not None:
        for name, digest in batch["files"].items():
            if sha256_file(out / name) != digest:
                raise ValueError(f"Existing backfill output changed: {name}")
        lines = (out / "verification/track_record.csv").read_bytes().splitlines(keepends=True)
        for issue, digest in batch["verification_rows"].items():
            matching = [line for line in lines[1:] if line.startswith(issue.encode() + b",")]
            if len(matching) != 1 or hashlib.sha256(matching[0]).hexdigest() != digest:
                raise ValueError(f"Existing backfill verification changed: {issue}")
        return {"added_issues": [], "batch": batch_key}
    existing = sorted((out / "forecasts").glob("*.json"))
    if not existing or last >= pd.Timestamp(existing[0].stem):
        raise ValueError(
            "Backfill refuses an existing forecast or an issue after the earliest forecast"
        )
    for issue in issues:
        if (out / f"observations/{issue.date()}.json").exists():
            raise ValueError(f"Existing observation for {issue.date()}")
    if set(read["issue_date"] for read in read_track(out)) & {
        str(issue.date()) for issue in issues
    }:
        raise ValueError("Existing verification row in requested backfill range")
    timeline = reconstruct_state(paths, out, cache, last + pd.Timedelta(days=6))
    cells = pd.read_parquet(out / "static/cells.parquet")
    climate = pd.read_parquet(out / "static/train_climatology_cell.parquet")
    model = FrozenModel(out)
    mask = read_json(out / "static/meta.json")["static_source_mask"]
    forecasts: dict[str, dict[str, Any]] = {}
    observations: dict[str, dict[str, Any]] = {}
    scores: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for issue in issues:
        name = str(issue.date())
        forecast = make_forecast(
            cells, as_of_state(timeline, issue), climate, model, issue, generated, mask
        )
        observed = observation(cells, timeline, issue)
        observed["nrt_type_proxy"] = proxy_for_window(timeline, issue, 7, mask)
        _, baseline = climatology_for_issue(cells, climate, issue)
        score = verification_row(cells, forecast, observed, baseline)
        forecasts[name], observations[name] = forecast, observed
        if score is not None:
            scores.append(score)
        summaries.append(
            {
                "issue_date": name,
                "complete": observed["complete"],
                "positives": score["positives"] if score else None,
                "ap": score["ap"] if score else None,
                "climatology_ap": score["climatology_ap"] if score else None,
                "recall_area_10": score["recall_area_10"] if score else None,
                "publication_lag_hours": forecast["publication_lag_hours"],
            }
        )
        index["issues"].append(
            {
                "issue_date": name,
                "forecast": True,
                "observation": True,
                "verified": score is not None,
                "prospective": False,
            }
        )
    track = out / "verification/track_record.csv"
    before_track = track.read_bytes()
    header, separator, old_rows = before_track.partition(b"\n")
    if tuple(next(csv.reader([header.decode()]))) != TRACK_COLUMNS:
        raise ValueError("Unexpected verification header")
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream, fieldnames=TRACK_COLUMNS, lineterminator="\r\n" if header.endswith(b"\r") else "\n"
    )
    writer.writerows(scores)
    new_track = header + separator + stream.getvalue().encode() + old_rows
    files = {
        f"{kind}/{name}.json": hashlib.sha256(encoded(value)).hexdigest()
        for kind, values in (("forecasts", forecasts), ("observations", observations))
        for name, value in values.items()
    }
    index.setdefault("backfill_batches", {})[batch_key] = {
        "files": files,
        "generated_utc": generated.isoformat(),
        "verification_rows": {
            row["issue_date"]: hashlib.sha256(line).hexdigest()
            for row, line in zip(
                scores, stream.getvalue().encode().splitlines(keepends=True), strict=True
            )
        },
    }
    index["issues"].sort(key=lambda item: item["issue_date"])
    index["last_updated_utc"] = generated.isoformat()
    total = sum(
        path.stat().st_size
        for path in out.rglob("*")
        if path.is_file() and ".git" not in path.parts
    )
    total += sum(
        len(encoded(value)) for values in (forecasts, observations) for value in values.values()
    )
    total += (
        len(new_track)
        - len(before_track)
        + len(encoded(index))
        - (out / "index.json").stat().st_size
    )
    if total >= 200_000_000:
        raise ValueError("Backfill exceeds the 200 MB publication limit")
    for name, forecast in forecasts.items():
        publish_forecast(out, forecast)
        write_json(out / f"observations/{name}.json", observations[name])
    if scores:
        durable_write(track, new_track)
    write_json(out / "index.json", index)
    return {"added_issues": list(forecasts), "issues": summaries, "output_bytes": total}
