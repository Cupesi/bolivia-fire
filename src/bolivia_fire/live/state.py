from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from bolivia_fire.detections import selection_mask
from bolivia_fire.features import DailyHistory
from bolivia_fire.live.files import write_parquet


def empty_counts() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.Series(dtype="datetime64[ns]"),
            "cell_id": pd.Series(dtype="string"),
            "n": pd.Series(dtype="int64"),
            "frp_sum": pd.Series(dtype="float64"),
        }
    )


def empty_prior() -> pd.DataFrame:
    return pd.DataFrame(
        {"cell_id": pd.Series(dtype="string"), "date": pd.Series(dtype="datetime64[ns]")}
    )


def aggregate_detections(detections: pd.DataFrame) -> pd.DataFrame:
    selected = detections[detections.in_bolivia & selection_mask(detections)].copy()
    selected["date"] = selected.acq_datetime.dt.tz_localize(None).dt.normalize()
    return (
        selected.groupby(["date", "cell_id"], sort=False)
        .agg(n=("cell_id", "size"), frp_sum=("frp", "sum"))
        .reset_index()
    )


@dataclass
class LiveState:
    counts: pd.DataFrame
    days: pd.DataFrame
    prior: pd.DataFrame = field(default_factory=empty_prior)

    @classmethod
    def load(cls, out: Path) -> LiveState:
        state = out / "state"
        return cls(
            pd.read_parquet(state / "daily_counts.parquet"),
            pd.read_parquet(state / "acquisition_days.parquet"),
            pd.read_parquet(state / "prior_detections.parquet"),
        )

    def merge(self, counts: pd.DataFrame, days: pd.DataFrame) -> None:
        replaced = days.date
        self.counts = pd.concat(
            [self.counts[~self.counts.date.isin(replaced)], counts], ignore_index=True
        )
        self.days = (
            pd.concat([self.days[~self.days.date.isin(replaced)], days], ignore_index=True)
            .sort_values("date")
            .reset_index(drop=True)
        )

    def roll(self, last_day: pd.Timestamp) -> LiveState:
        first = last_day - pd.Timedelta(days=399)
        expired = self.counts[self.counts.date < first][["cell_id", "date"]]
        self.prior = (
            pd.concat([self.prior, expired], ignore_index=True)
            .groupby("cell_id", as_index=False, sort=False)
            .date.max()
        )
        self.counts = self.counts[self.counts.date.between(first, last_day)].reset_index(drop=True)
        self.days = self.days[self.days.date.between(first, last_day)].reset_index(drop=True)
        return self

    def save(self, out: Path) -> None:
        # Persist older recency first; replay replaces fetched days instead of adding them.
        write_parquet(out / "state/prior_detections.parquet", self.prior)
        write_parquet(out / "state/daily_counts.parquet", self.counts)
        write_parquet(out / "state/acquisition_days.parquet", self.days)


def feature_rows(cells: pd.DataFrame, state: LiveState, issue: pd.Timestamp) -> pd.DataFrame:
    if len(state.days) and issue - pd.Timedelta(days=365) < state.days.date.min():
        raise ValueError("Issue requires history older than the retained daily state")
    prior = state.prior[state.prior.date < issue]
    history = DailyHistory.from_daily_counts(
        cells, state.counts, state.days, issue - pd.Timedelta(days=730), issue, prior
    )
    return history.features(issue)


def unobserved_days(state: LiveState, first: pd.Timestamp, length: int) -> list[str]:
    dates = pd.date_range(first, periods=length)
    coverage = state.days.set_index("date")
    observed = (
        (coverage.acquired & coverage.observed).reindex(dates, fill_value=False).fillna(False)
    )
    return [day.date().isoformat() for day in dates[~observed.to_numpy(dtype=bool)]]


def observation(cells: pd.DataFrame, state: LiveState, issue: pd.Timestamp) -> dict[str, Any]:
    missing = unobserved_days(state, issue, 7)
    counts = state.counts[state.counts.date.between(issue, issue + pd.Timedelta(days=6))]
    totals = counts.groupby("cell_id").n.sum().reindex(cells.cell_id, fill_value=0)
    return {
        "issue_date": issue.date().isoformat(),
        "complete": not missing,
        "unobserved_days": missing,
        "cell_index": list(range(len(cells))),
        "y": [None] * len(cells) if missing else (totals.to_numpy() > 0).astype(int).tolist(),
        "n": totals.to_numpy(dtype=np.int64).tolist(),
    }
