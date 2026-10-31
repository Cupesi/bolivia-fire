from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from bolivia_fire.detections import selection_mask

WINDOWS = (7, 14, 28, 91, 365)


def week_index(issue: pd.Timestamp) -> int:
    return min((issue.day_of_year - 1) // 7, 51)


def neighborhood_counts(cells: pd.DataFrame, values: NDArray, radius: int) -> NDArray:
    row = cells.row.to_numpy() - cells.row.min()
    col = cells.col.to_numpy() - cells.col.min()
    raster = np.zeros((int(row.max()) + 1, int(col.max()) + 1), dtype=np.float64)
    raster[row, col] = values
    integral = np.pad(raster, ((1, 0), (1, 0))).cumsum(axis=0).cumsum(axis=1)
    lo_row, hi_row = np.maximum(row - radius, 0), np.minimum(row + radius + 1, raster.shape[0])
    lo_col, hi_col = np.maximum(col - radius, 0), np.minimum(col + radius + 1, raster.shape[1])
    return (
        integral[hi_row, hi_col]
        - integral[lo_row, hi_col]
        - integral[hi_row, lo_col]
        + integral[lo_row, lo_col]
    )


class DailyHistory:
    def __init__(
        self,
        cells: pd.DataFrame,
        detections: pd.DataFrame,
        acquisition_days: pd.DataFrame,
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
        prior_cells: Iterable[str] = (),
    ) -> None:
        self.cells = cells.reset_index(drop=True)
        self.start, self.end = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
        dates = pd.date_range(self.start, self.end)
        self.acquired = acquisition_days.set_index("date").acquired.reindex(dates, fill_value=False)
        selected = detections[detections.in_bolivia & selection_mask(detections)].copy()
        selected["day"] = selected.acq_datetime.dt.tz_localize(None).dt.normalize()
        prior = set(prior_cells) | set(selected.loc[selected.day < self.start, "cell_id"])
        self.prior_seen = self.cells.cell_id.isin(prior).to_numpy()
        selected = selected[selected.day.between(self.start, self.end)]
        national = selected.groupby("day").size().reindex(dates, fill_value=0).to_numpy()
        self.national_counts = np.concatenate(([0], np.cumsum(national, dtype=np.int64)))
        selected["cell_index"] = pd.Index(self.cells.cell_id).get_indexer(selected.cell_id)
        selected = selected[selected.cell_index >= 0]
        grouped = (
            selected.groupby(["day", "cell_index"], sort=False)
            .agg(count=("cell_id", "size"), frp=("frp", "sum"))
            .reset_index()
        )
        daily_dtype = np.int16 if grouped["count"].max() <= np.iinfo(np.int16).max else np.int32
        daily = np.zeros((len(dates), len(cells)), dtype=daily_dtype)
        day_indices = (grouped.day - self.start).dt.days.to_numpy()
        cell_indices = grouped.cell_index.to_numpy(dtype=np.int64)
        daily[day_indices, cell_indices] = grouped["count"].to_numpy()
        total_max = daily.sum(axis=0, dtype=np.int64).max(initial=0)
        cumulative_dtype = np.int32 if total_max <= np.iinfo(np.int32).max else np.int64
        self.counts = np.zeros((len(dates) + 1, len(cells)), dtype=cumulative_dtype)
        np.cumsum(daily, axis=0, dtype=cumulative_dtype, out=self.counts[1:])
        last_dtype = np.int16 if len(dates) < np.iinfo(np.int16).max else np.int32
        self.last_seen = np.where(daily > 0, np.arange(1, len(dates) + 1)[:, None], 0).astype(
            last_dtype
        )
        np.maximum.accumulate(self.last_seen, axis=0, out=self.last_seen)
        del daily
        # Float64 accumulation avoids cancellation in 28-day FRP differences.
        self.frp = np.zeros((len(dates) + 1, len(cells)), dtype=np.float64)
        self.frp[day_indices + 1, cell_indices] = grouped.frp.to_numpy(dtype=np.float64)
        np.cumsum(self.frp, axis=0, out=self.frp)

    def _offset(self, day: pd.Timestamp) -> int:
        return int(np.clip((day - self.start).days, 0, len(self.counts) - 1))

    def _complete(self, issue: pd.Timestamp, length: int) -> bool:
        days = pd.date_range(issue - pd.Timedelta(days=length), periods=length)
        return bool(self.acquired.reindex(days, fill_value=False).all())

    def features(self, issue: str | pd.Timestamp) -> pd.DataFrame:
        issue = pd.Timestamp(issue).tz_localize(None)
        if issue != issue.normalize() or not self.start <= issue <= self.end:
            raise ValueError("Issue must be midnight inside the daily history range")
        stop = self._offset(issue)
        result = self.cells[["cell_id"]].copy()
        windows = {}
        for length in WINDOWS:
            start = self._offset(issue - pd.Timedelta(days=length))
            windows[length] = self.counts[stop] - self.counts[start]
            result[f"count_last_{length}d"] = windows[length].astype(np.float32)
        result["any_last_7d"] = windows[7] > 0
        last = self.last_seen[stop - 1] if stop else np.zeros(len(self.cells), dtype=np.int32)
        age = (issue - self.start).days - last.astype(np.int32) + 1
        no_prior = (last == 0) & ~self.prior_seen
        result["days_since_last"] = np.where(last == 0, 730, np.minimum(age, 730)).astype(
            np.float32
        )
        result["no_prior_detection"] = no_prior
        result["frp_sum_28d"] = (
            self.frp[stop] - self.frp[self._offset(issue - pd.Timedelta(days=28))]
        ).astype(np.float32)
        result["same_week_last_year"] = (
            self.counts[self._offset(issue - pd.Timedelta(days=357))]
            - self.counts[self._offset(issue - pd.Timedelta(days=364))]
        ).astype(np.float32)
        for length in (7, 28):
            for width in (3, 7):
                result[f"neighborhood_{width}x{width}_last_{length}d"] = neighborhood_counts(
                    self.cells, windows[length], width // 2
                ).astype(np.float32)
            result[f"national_last_{length}d"] = np.float32(
                self.national_counts[stop]
                - self.national_counts[self._offset(issue - pd.Timedelta(days=length))]
            )
        angle = 2 * np.pi * (issue.isocalendar().week - 1) / 52
        result["week_of_year_sin"], result["week_of_year_cos"] = (
            np.float32(np.sin(angle)),
            np.float32(np.cos(angle)),
        )
        result["feature_window_complete"] = self._complete(issue, 365)
        result["window_complete_28d"] = self._complete(issue, 28)
        return result


class TrainingClimatology:
    def __init__(self, cells: pd.DataFrame, train_years: Iterable[int]) -> None:
        self.cell_ids = pd.Index(cells.cell_id)
        self.held = cells.geo_holdout.to_numpy(dtype=bool)
        self.years = list(train_years)
        self.sums = np.zeros((len(self.years), 52, len(cells)), dtype=np.int32)
        self.counts = np.zeros_like(self.sums)

    def add(self, issue: pd.Timestamp, labels: pd.DataFrame) -> None:
        if issue.year not in self.years:
            return
        positions = self.cell_ids.get_indexer(labels.cell_id)
        if (positions < 0).any():
            raise ValueError("Unknown climatology cell")
        valid = labels.y_primary.notna().to_numpy() & ~self.held[positions]
        positions = positions[valid]
        year, week = self.years.index(issue.year), week_index(issue)
        self.sums[year, week, positions] += labels.y_primary[valid].to_numpy(dtype=np.int32)
        self.counts[year, week, positions] += 1

    def predict(self, issue: pd.Timestamp) -> NDArray[np.float32]:
        years = [index for index, year in enumerate(self.years) if year != issue.year]
        weeks = [(week_index(issue) + delta) % 52 for delta in range(-2, 3)]
        sums = self.sums[np.ix_(years, weeks)].sum(axis=(0, 1))
        counts = self.counts[np.ix_(years, weeks)].sum(axis=(0, 1))
        result = np.full(len(self.cell_ids), np.nan, dtype=np.float32)
        np.divide(sums, counts, out=result, where=counts > 0)
        return result


def train_climatology(
    labels: pd.DataFrame, queries: pd.DataFrame, *, train_years: Iterable[int]
) -> NDArray[np.float32]:
    cells = labels[["cell_id", "geo_holdout"]].drop_duplicates("cell_id")
    estimator = TrainingClimatology(cells, train_years)
    for issue, subset in labels[labels.partition.eq("train")].groupby("issue_date", sort=False):
        estimator.add(pd.Timestamp(issue), subset)
    result = np.full(len(queries), np.nan, dtype=np.float32)
    for issue, indices in queries.groupby("issue_date", sort=False).indices.items():
        positions = estimator.cell_ids.get_indexer(queries.iloc[indices].cell_id)
        known = positions >= 0
        values = estimator.predict(pd.Timestamp(issue))
        result[indices[known]] = values[positions[known]]
    return result
