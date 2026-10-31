from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.dataset as ds
import yaml
from shapely.geometry import mapping

from bolivia_fire.config import Paths, load_protocol
from bolivia_fire.detections import resolve_detection_bundle
from bolivia_fire.firms.storage import sha256_file
from bolivia_fire.grid import load_boundary_geometries
from bolivia_fire.live.files import write_json, write_parquet
from bolivia_fire.live.model import export_model
from bolivia_fire.live.pipeline import (
    LABEL,
    LIMITATIONS,
    availability,
    default_issue,
    fetch_interval,
    publish_cycle,
    refresh_outages,
    update_metadata,
)
from bolivia_fire.live.source import NRT, PROXY, SP, LiveClient, StaticSourceMask
from bolivia_fire.live.state import LiveState, aggregate_detections
from bolivia_fire.live.static import export_static, refresh_static
from bolivia_fire.outages import observation_days


def local_state(source: Path, days: pd.DataFrame, last: pd.Timestamp) -> LiveState:
    first = last - pd.Timedelta(days=399)
    dataset = ds.dataset(source, format="parquet")
    primary = (
        ds.field("in_bolivia") & (ds.field("type") == 0) & ds.field("confidence").isin(["n", "h"])
    )
    older = primary & (ds.field("acq_datetime") < first.tz_localize("UTC"))
    prior = []
    for batch in dataset.to_batches(
        columns=["cell_id", "acq_datetime"], filter=older, batch_size=100_000
    ):
        frame = batch.to_pandas()
        frame["date"] = frame.acq_datetime.dt.tz_localize(None).dt.normalize()
        prior.append(frame.groupby("cell_id", as_index=False).date.max())
    earlier = (
        pd.concat(prior).groupby("cell_id", as_index=False).date.max()
        if prior
        else pd.DataFrame(
            {"cell_id": pd.Series(dtype="string"), "date": pd.Series(dtype="datetime64[ns]")}
        )
    )
    detections = dataset.to_table(
        columns=["cell_id", "acq_datetime", "in_bolivia", "type", "confidence", "frp"],
        filter=(ds.field("acq_datetime") >= first.tz_localize("UTC"))
        & (ds.field("acq_datetime") < (last + pd.Timedelta(days=1)).tz_localize("UTC")),
    ).to_pandas()
    acquired = (
        days[days.date.between(first, last)]
        .copy()
        .assign(source="SP", nrt_rows_removed=0)
        .rename(columns={"bbox_detection_count": "bbox_count"})
    )
    return LiveState(aggregate_detections(detections), acquired, earlier)


def sp_batches(source: Path, cache: Path) -> Iterator[pd.DataFrame]:
    columns = ["latitude", "longitude", "type", "confidence"]
    dataset = ds.dataset(source, format="parquet")
    for batch in dataset.to_batches(columns=columns, batch_size=100_000):
        yield batch.to_pandas()
    for path in sorted((cache / "raw" / SP).glob("*.csv")):
        frame = pd.read_csv(path, dtype={"type": "int8"}).drop_duplicates()
        yield frame[columns]


def build_static_source_mask(source: Path, cache: Path) -> tuple[StaticSourceMask, dict[str, Any]]:
    locations = [
        frame.loc[frame["type"].isin([1, 2]), ["latitude", "longitude"]]
        for frame in sp_batches(source, cache)
    ]
    mask = StaticSourceMask(pd.concat(locations, ignore_index=True))
    total = removed = 0
    for frame in sp_batches(source, cache):
        primary = frame[frame["type"].eq(0) & frame.confidence.isin(["n", "h"])]
        total += len(primary)
        removed += int(mask.excluded(primary).sum())
    return mask, {
        "mask_locations": len(mask.locations),
        "radius_m": 375,
        "sp_first_day": "2012-01-20",
        "sp_last_day": "2026-07-31",
        "sp_primary_rows": total,
        "sp_primary_rows_masked": removed,
        "sp_primary_masked_share": removed / total if total else None,
        "sanity_check_scope": "nominal/high SP type=0 rows in acquisition bbox",
    }


def bootstrap(
    paths: Paths, out: Path, cache: Path, client: LiveClient, now: datetime
) -> dict[str, Any]:
    protocol = load_protocol(paths.root)
    source, days_source, _ = resolve_detection_bundle(paths, protocol)
    original_days = pd.read_parquet(days_source)
    local_end = pd.Timestamp("2026-01-04")
    if pd.Timestamp(protocol["time"]["acquisition_end"]) != local_end:
        raise ValueError("Bootstrap requires the frozen SP bundle through 2026-01-04")
    out.mkdir(parents=True, exist_ok=True)
    if (out / "forecasts").exists() and any((out / "forecasts").glob("*.json")):
        raise ValueError("Bootstrap refuses an initialized forecast archive; use live update")
    card = export_model(paths, out)
    export_static(paths, out, source, original_days, protocol)
    country, _ = load_boundary_geometries(paths)
    write_json(out / "static/boundaries_adm0_full.json", mapping(country))
    state = local_state(source, original_days, local_end)
    bbox = tuple(protocol["study_area"]["acquisition_bbox"])
    fetch_interval(
        state,
        client,
        cache,
        SP,
        pd.Timestamp("2026-01-05"),
        pd.Timestamp("2026-07-31"),
        bbox,
        country,
    )
    mask, mask_stats = build_static_source_mask(source, cache)
    write_parquet(out / "static/static_source_mask.parquet", mask.locations)
    card["static_source_mask"] = mask_stats
    write_json(out / "model/model_card.json", card)
    first_nrt, last = availability(client, now)
    if first_nrt > pd.Timestamp("2026-08-01"):
        raise ValueError("NRT archive no longer covers the required 2026-08-01 bootstrap start")
    fetch_interval(state, client, cache, NRT, pd.Timestamp("2026-08-01"), last, bbox, country, mask)
    outages, outage_hash = refresh_outages(client, cache)
    state.days = observation_days(state.days.drop(columns="outage_hours", errors="ignore"), outages)
    state.roll(last).save(out)
    source_pins = yaml.safe_load((paths.datasets / "sources.yaml").read_text())
    meta = {
        "label": LABEL,
        "model_id": card["model_id"],
        "protocol_sha256": card["protocol_sha256"],
        "generated_utc": now.isoformat(),
        "state_first_day": str(state.days.date.min().date()),
        "state_last_day": str(last.date()),
        "outage_sha256": outage_hash,
        "nrt_version": "2.0NRT",
        "nrt_type_proxy": PROXY,
        "static_source_mask": mask_stats,
        "limitations": LIMITATIONS,
        "sources": [
            {
                "name": "NASA FIRMS",
                "product": SP,
                "through": "2026-07-31",
                "url": "https://firms.modaps.eosdis.nasa.gov/api/area/",
            },
            {
                "name": "NASA FIRMS",
                "product": NRT,
                "from": "2026-08-01",
                "url": "https://firms.modaps.eosdis.nasa.gov/api/area/",
            },
            {
                "name": "MODAPS S-NPP outages",
                "url": "https://modaps.modaps.eosdis.nasa.gov/services/production/outages_suomi_npp.html",
            },
        ],
        "attribution": [
            {
                "name": "NASA FIRMS",
                "license": "NASA open-data reuse with source attribution",
                "url": "https://firms.modaps.eosdis.nasa.gov/",
            },
            {
                "name": "geoBoundaries",
                "license": "Public Domain",
                "product": "gbOpen BOL ADM0/ADM1",
                "url": "https://www.geoboundaries.org/",
                "pins": {
                    level: source_pins["boundaries"][level]["sha256"] for level in ("ADM0", "ADM1")
                },
            },
        ],
        "frozen_sp_bundle_sha256": sha256_file(source),
    }
    write_json(out / "static/meta.json", meta)
    refresh_static(paths, out)
    update_metadata(out, state, now, outage_hash)
    result = publish_cycle(out, state, default_issue(state, now), now, initialize=True)
    result.update(
        nrt_days=int(state.days.source.eq("NRT").sum()),
        unobserved_days=[
            str(value.date()) for value in state.days.loc[~state.days.observed, "date"]
        ],
        data_requests=client.data_requests,
        static_source_mask=mask_stats,
    )
    return result
