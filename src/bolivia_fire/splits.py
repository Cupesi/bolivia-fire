from __future__ import annotations

from typing import Any

import pandas as pd

TEMPORAL_PARTITIONS = ("train", "val_select", "val_calib", "test")


def issue_dates(
    protocol: dict[str, Any],
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = None,
) -> pd.DatetimeIndex:
    first, last = (
        pd.Timestamp(protocol["time"]["first_issue"]),
        pd.Timestamp(protocol["time"]["last_issue"]),
    )
    first = max(first, pd.Timestamp(start)) if start is not None else first
    last = min(last, pd.Timestamp(end)) if end is not None else last
    if first > last:
        raise ValueError("Issue date range is empty or outside the protocol")
    return pd.date_range(first, last, freq="W-MON")


def partition_for_issue(issue: str | pd.Timestamp, protocol: dict[str, Any]) -> str | None:
    day = pd.Timestamp(issue).tz_localize(None)
    boundaries = protocol["partitions"]
    for index, name in enumerate(TEMPORAL_PARTITIONS):
        start, end = map(pd.Timestamp, boundaries[name])
        if start <= day < end + pd.Timedelta(days=1):
            if index + 1 < len(TEMPORAL_PARTITIONS):
                next_start = pd.Timestamp(boundaries[TEMPORAL_PARTITIONS[index + 1]][0])
                if day + pd.Timedelta(days=protocol["target"]["horizon_days"]) > next_start:
                    return None
            return name
    return None
