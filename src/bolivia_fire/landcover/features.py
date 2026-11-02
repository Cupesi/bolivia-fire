from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from bolivia_fire.landcover.core import FEATURES


def issue_features(
    annual: pd.DataFrame, cell_ids: Sequence[str], issue_dates: pd.DatetimeIndex,
) -> pd.DataFrame:
    if annual.duplicated(["cell_id", "year"]).any() or len(set(cell_ids)) != len(cell_ids):
        raise ValueError("Duplicate land-cover map or requested cell")
    if annual.empty or not cell_ids or len(issue_dates) == 0:
        raise ValueError("Maps, cells and issue dates are required")
    dates = pd.DatetimeIndex(pd.to_datetime(issue_dates, utc=True))
    if (
        dates.hasnans or dates.has_duplicates or (dates.day_of_week != 0).any()
        or not (dates == dates.normalize()).all()
    ):
        raise ValueError("Issue dates must be unique UTC Monday midnights")
    if not annual.year.eq(annual.map_year).all():
        raise ValueError("Annual map-year alias differs from its reference year")
    fractions = annual.loc[annual.available, list(FEATURES)].to_numpy(dtype=float)
    if (
        not np.isfinite(fractions).all()
        or (fractions < 0).any() or (fractions > 1).any()
        or (np.abs(fractions.sum(axis=1) - 1) > 1e-6).any()
        or not annual.loc[annual.available, "mapped_area_fraction"].between(0.95, 1).all()
    ):
        raise ValueError("Available annual fractions or coverage are invalid")
    years = sorted(annual.year.unique())
    pieces = []
    for issue_year in sorted(dates.year.unique()):
        nominal = int(issue_year) - 1
        candidates = [year for year in years if year <= nominal and issue_year - year <= 2]
        selected = max(candidates) if candidates else None
        base = pd.DataFrame({"cell_id": cell_ids})
        if selected is not None:
            source = annual.loc[
                annual.year.eq(selected),
                ["cell_id", "available", "mapped_area_fraction", *FEATURES],
            ]
            base = base.merge(source, on="cell_id", how="left", validate="one_to_one")
            base.loc[~base.available.eq(True), list(FEATURES)] = np.nan
        else:
            for field in ("mapped_area_fraction", *FEATURES):
                base[field] = np.nan
        base = base.rename(columns={"mapped_area_fraction": "land_cover_mapped_area_fraction"})
        base["land_cover_nominal_map_year"] = nominal
        base["land_cover_map_year"] = pd.array([selected] * len(base), dtype="Int64")
        base["land_cover_reference_age_years"] = pd.array(
            [int(issue_year) - selected if selected is not None else None] * len(base),
            dtype="Int64",
        )
        base = base.drop(columns="available", errors="ignore")
        for date in dates[dates.year == issue_year].sort_values():
            part = base.copy()
            part.insert(1, "issue_date", date.tz_localize(None).as_unit("us"))
            pieces.append(part)
    return pd.concat(pieces, ignore_index=True)
