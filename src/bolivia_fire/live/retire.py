from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from bolivia_fire.live.coverage import SNPP_DELIVERY_END, apply_coverage
from bolivia_fire.live.files import read_json, write_json, write_parquet
from bolivia_fire.live.outages import fetch_outages
from bolivia_fire.live.state import LiveState
from bolivia_fire.live.verify import read_track, record_verification


def refuse_retired(out: Path) -> None:
    path = out / "index.json"
    if path.is_file() and read_json(path).get("retirement", {}).get("status") == "ended":
        raise ValueError(
            "Weekly estimates ended; this retired archive cannot be updated or bootstrapped"
        )


def retire(out: Path, cache: Path) -> dict[str, Any]:
    """Refresh public MODAPS coverage and end publication without credentials or inference."""
    with httpx.Client(timeout=30, follow_redirects=True, trust_env=False) as http:
        outages, outage_hash = fetch_outages(http, cache)
    state = LiveState.load(out)
    days = apply_coverage(state.days, outages)
    regressed = ~state.days.observed & days.observed
    if "outage_hours" in state.days:
        regressed |= days.outage_hours.lt(state.days.outage_hours)
    if regressed.any():
        dates = sorted(state.days.loc[regressed, "date"].dt.strftime("%Y-%m-%d"))
        raise ValueError(f"Retirement refuses coverage regression on: {', '.join(dates)}")
    index = read_json(out / "index.json")
    meta = read_json(out / "static/meta.json")
    forecasts = sorted((out / "forecasts").glob("*.json"))
    if not forecasts:
        raise ValueError("Retirement requires an existing forecast archive")
    end = SNPP_DELIVERY_END.ended_utc
    final_issue = forecasts[-1].stem
    if pd.Timestamp(final_issue).date() > end.date():
        raise ValueError("Archive contains a forecast after S-NPP delivery ended")
    retirement = {
        "status": "ended",
        "ended_utc": end.isoformat().replace("+00:00", "Z"),
        "final_issue": final_issue,
        "reason": "NASA stopped delivering Suomi NPP science data.",
        "source_url": SNPP_DELIVERY_END.source_url,
    }
    coverage = days.set_index("date").observed
    observations: dict[Path, dict[str, Any]] = {}
    for path in sorted((out / "observations").glob("*.json")):
        observed = read_json(path)
        missing = set(observed["unobserved_days"])
        for day in pd.date_range(observed["issue_date"], periods=7):
            value = str(day.date())
            if day in coverage.index:
                if coverage.loc[day]:
                    missing.discard(value)
                else:
                    missing.add(value)
            elif day.date() >= end.date():
                missing.add(value)
        observed["unobserved_days"] = sorted(missing)
        observed["complete"] = not missing
        observed["y"] = (
            [None] * len(observed["n"]) if missing else [int(n > 0) for n in observed["n"]]
        )
        observations[path] = observed
    verified = {row["issue_date"] for row in read_track(out)}
    withdrawn = sorted(
        path.stem
        for path, observed in observations.items()
        if not observed["complete"] and path.stem in verified
    )
    for item in index["issues"]:
        if item["issue_date"] in withdrawn:
            item["verified"] = False
    index["retirement"] = retirement
    meta["retirement"] = retirement
    meta["outage_sha256"] = outage_hash
    changed: list[str] = []
    if not state.days.equals(days):
        write_parquet(out / "state/acquisition_days.parquet", days)
        changed.append("state/acquisition_days.parquet")
    for path, payload in observations.items():
        if read_json(path) != payload:
            write_json(path, payload)
            changed.append(path.relative_to(out).as_posix())
    for issue in withdrawn:
        record_verification(out, issue, None)
    if withdrawn:
        changed.append("verification/track_record.csv")
    for name, payload in (("index.json", index), ("static/meta.json", meta)):
        if read_json(out / name) != payload:
            write_json(out / name, payload)
            changed.append(name)
    return {
        "retirement": retirement,
        "outage_sha256": outage_hash,
        "changed_files": changed,
        "withdrawn_issues": withdrawn,
    }
