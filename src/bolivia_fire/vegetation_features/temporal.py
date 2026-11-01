from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd
from numpy.typing import NDArray

FEATURES = [
    "veg_ndvi_latest",
    "veg_evi_latest",
    "veg_ndvi_change_previous",
    "veg_evi_change_previous",
    "veg_ndvi_change_48d",
    "veg_evi_change_48d",
    "veg_ndvi_seasonal_anomaly",
    "veg_evi_seasonal_anomaly",
    "veg_valid_pixel_fraction",
    "veg_observation_age_days",
]


def join_features(panel: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
    keys = ["cell_id", "issue_date"]
    if panel.duplicated(keys).any() or features.duplicated(keys).any():
        raise ValueError("Duplicate cell-issue keys in vegetation join")
    return panel.merge(features, on=keys, how="left", validate="one_to_one", sort=False)


def seasonal_reference(
    starts: pd.DatetimeIndex,
    values: NDArray,
    cells: pd.DataFrame,
    issue_year: int,
    slot: int,
    *,
    geography: Literal["temporal", "geographic"] = "temporal",
) -> tuple[NDArray, NDArray]:
    slots = np.minimum((starts.day_of_year - 1) // 16, 22)
    reference_years = [year for year in range(2013, 2021) if year != issue_year]
    annual = []
    holdout = cells.geo_holdout.to_numpy(dtype=bool)
    departments = cells.department.to_numpy()
    for year in reference_years:
        selected = (starts.year == year) & (slots == slot)
        if not selected.any():
            continue
        if selected.sum() != 1:
            raise ValueError("Duplicate annual composite slot in vegetation climatology")
        row = values[selected][0].astype(np.float64).copy()
        row[holdout] = np.nan
        if geography == "geographic":
            for department in np.unique(departments):
                members = departments == department
                valid = members & np.isfinite(row) & ~holdout
                row[members] = row[valid].mean() if valid.any() else np.nan
        annual.append(row)
    if not annual:
        return np.full(len(cells), np.nan), np.zeros(len(cells), dtype=np.int8)
    matrix = np.stack(annual)
    counts = np.isfinite(matrix).sum(axis=0)
    means = np.divide(
        np.nansum(matrix, axis=0), counts, out=np.full(len(cells), np.nan), where=counts >= 4
    )
    return means, counts


@dataclass
class CompositeSeries:
    starts: pd.DatetimeIndex
    ends: pd.DatetimeIndex
    ndvi: NDArray
    evi: NDArray
    coverage: NDArray
    oldest: NDArray
    latest: NDArray
    confirmation_ok: NDArray | None = None

    def __post_init__(self) -> None:
        if not self.starts.is_unique or not self.starts.is_monotonic_increasing:
            raise ValueError("Composite starts must be unique and sorted")
        expected = (len(self.starts), self.ndvi.shape[1])
        if any(
            values.shape != expected
            for values in (self.ndvi, self.evi, self.coverage, self.oldest, self.latest)
        ):
            raise ValueError("Composite array dimensions disagree")

    def for_issue(self, issue: str | pd.Timestamp, lag: int) -> pd.DataFrame:
        if lag not in (16, 32):
            raise ValueError("Only registered vegetation lags 16 and 32 are allowed")
        issue = pd.Timestamp(issue)
        if issue.tzinfo is not None:
            issue = issue.tz_convert("UTC").tz_localize(None)
        issue_day = issue.value / 86_400_000_000_000
        cells = self.ndvi.shape[1]
        result = pd.DataFrame(np.nan, index=range(cells), columns=FEATURES, dtype=np.float32)
        current = np.full(cells, -1, dtype=int)
        previous = np.full(cells, -1, dtype=int)
        reference = np.full(cells, -1, dtype=int)
        available = self.ends + pd.Timedelta(days=lag) <= issue
        if issue.year == 2026:
            available &= (
                self.confirmation_ok
                if self.confirmation_ok is not None
                else np.zeros(len(available), dtype=bool)
            )
        valid = (
            np.isfinite(self.ndvi)
            & np.isfinite(self.evi)
            & (self.coverage >= 0.5)
            & (self.oldest >= 0)
            & (self.latest >= self.oldest)
        )
        current_valid = valid & (issue_day - self.oldest <= 64)
        ref_valid = valid & (issue_day - self.oldest <= 128)
        start_days = self.starts.to_numpy().astype("datetime64[D]").astype(int)
        for index in np.flatnonzero(available):
            current[current_valid[index]] = index
        for index in np.unique(current[current >= 0]):
            chosen = current == index
            candidates = np.flatnonzero(available & (self.starts < self.starts[index]))
            for prior in candidates:
                acceptable = chosen & ref_valid[prior]
                previous[acceptable] = prior
            previous[
                chosen
                & (previous >= 0)
                & (start_days[index] - start_days[np.maximum(previous, 0)] > 32)
            ] = -1
            target = start_days[index] - 48
            candidates = sorted(
                (p for p in candidates if abs(start_days[p] - target) <= 8),
                key=lambda p: (abs(start_days[p] - target), start_days[p]),
            )
            for prior in candidates:
                acceptable = chosen & ref_valid[prior] & (reference < 0)
                reference[acceptable] = prior
        positions = np.arange(cells)
        safe_current = np.maximum(current, 0)
        present = current >= 0
        for name, values in (("ndvi", self.ndvi), ("evi", self.evi)):
            latest_value = values[safe_current, positions]
            result.loc[present, f"veg_{name}_latest"] = latest_value[present]
            for suffix, refs in (("previous", previous), ("48d", reference)):
                usable = present & (refs >= 0)
                difference = latest_value - values[np.maximum(refs, 0), positions]
                result.loc[usable, f"veg_{name}_change_{suffix}"] = difference[usable]
        result.loc[present, "veg_valid_pixel_fraction"] = self.coverage[safe_current, positions][
            present
        ]
        result.loc[present, "veg_observation_age_days"] = (
            issue_day - self.latest[safe_current, positions]
        )[present]
        result["composite_index"] = current.astype(np.int32)
        for name, dates in (
            ("oldest_observation", self.oldest),
            ("latest_observation", self.latest),
        ):
            result[name] = pd.to_datetime(
                np.where(present, dates[safe_current, positions], np.nan), unit="D", origin="unix"
            )
        result["composite_start"] = self.starts.to_numpy()[safe_current]
        result["composite_end_boundary"] = self.ends.to_numpy()[safe_current]
        result.loc[~present, ["composite_start", "composite_end_boundary"]] = pd.NaT
        return result
