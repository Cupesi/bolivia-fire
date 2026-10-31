from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from bolivia_fire.config import REPO_ROOT


def join_retrospective(
    panel_table: pa.Table, *, root: Path | None = None,
) -> pa.Table:
    root = root or REPO_ROOT / "datasets" / "processed" / "covariates"
    panel = panel_table.select(["cell_id", "issue_date"]).to_pandas()
    panel["issue_date"] = pd.to_datetime(panel.issue_date)
    keys = panel.copy()
    keys["_row"] = range(len(keys))
    rain_parts = []
    for year in sorted(panel.issue_date.dt.year.unique()):
        path = root / "chirps_features" / f"year={year}" / "part.parquet"
        if path.exists():
            table = pq.ParquetFile(path).read()
            rain_parts.append(table.to_pandas())
    result = panel_table
    if rain_parts:
        rain = pd.concat(rain_parts, ignore_index=True)
        rain["issue_date"] = pd.to_datetime(rain.issue_date)
        if rain.duplicated(["cell_id", "issue_date"]).any():
            raise ValueError("CHIRPS features contain duplicate cell-issue keys")
        joined = keys.merge(rain, how="left", on=["cell_id", "issue_date"],
                            validate="many_to_one").sort_values("_row")
        for name in rain.columns.difference(["cell_id", "issue_date"]):
            if name in result.column_names:
                raise ValueError(f"Retrospective column already present: {name}")
            result = result.append_column(name, pa.Array.from_pandas(joined[name]))
    land_path = root / "mapbiomas_fractions.parquet"
    if land_path.exists():
        from bolivia_fire.covariates.mapbiomas import previous_year_features

        fractions = pd.read_parquet(land_path)
        joined = previous_year_features(panel, fractions)
        for name in fractions.columns.difference(["cell_id", "year"]):
            if name in result.column_names:
                raise ValueError(f"Retrospective column already present: {name}")
            result = result.append_column(name, pa.Array.from_pandas(joined[name]))
    return result
