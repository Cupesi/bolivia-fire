from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

import pandas as pd

from bolivia_fire.outages import observation_days


@dataclass(frozen=True)
class DeliveryEnd:
    """Permanent S-NPP delivery outage documented by NASA Earthdata."""

    ended_utc: datetime
    source_url: str


SNPP_DELIVERY_END: Final[DeliveryEnd] = DeliveryEnd(
    datetime(2026, 11, 2, 15, 40, tzinfo=UTC),
    "https://www.earthdata.nasa.gov/data/alerts-outages/"
    "reminder-new-suomi-npp-observations-cease-november-2-2026",
)


def apply_coverage(days: pd.DataFrame, outages: pd.DataFrame) -> pd.DataFrame:
    result = days.drop(columns="outage_hours", errors="ignore").copy()
    dates = pd.to_datetime(result.date, utc=True)
    end = pd.Timestamp(SNPP_DELIVERY_END.ended_utc)
    result.loc[dates > end.normalize(), "acquired"] = False
    if len(dates) and dates.max() + pd.Timedelta(days=1) > end:
        # Clipping infinity to the last requested midnight preserves every overlap
        # while letting the protocol's existing interval-union routine remain bounded.
        permanent = pd.DataFrame(
            {"start_utc": [end], "end_utc": [dates.max() + pd.Timedelta(days=1)]}
        )
        outages = pd.concat([outages, permanent], ignore_index=True)
    return observation_days(result, outages)
