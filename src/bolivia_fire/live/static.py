from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from numpy.typing import NDArray
from shapely.geometry import mapping, shape

from bolivia_fire.config import Paths
from bolivia_fire.features import week_index
from bolivia_fire.firms.storage import sha256_file
from bolivia_fire.live.files import read_json, write_json, write_parquet
from bolivia_fire.panel import fit_climatology

GRID_COLUMNS = [
    "cell_id",
    "row",
    "col",
    "department",
    "national_area_km2",
    "santa_cruz",
    "geo_holdout",
]


def export_static(
    paths: Paths, out: Path, source: Path, days: pd.DataFrame, protocol: dict[str, Any]
) -> pd.DataFrame:
    cells = (
        pd.read_parquet(paths.datasets / "processed/cells.parquet")
        .sort_values("cell_id")
        .reset_index(drop=True)
    )
    grid = {
        **{key: protocol["grid"][key] for key in ("origin", "resolution_deg", "crs")},
        "row_range": [int(cells.row.min()), int(cells.row.max())],
        "col_range": [int(cells.col.min()), int(cells.col.max())],
        **cells[GRID_COLUMNS].to_dict(orient="list"),
    }
    write_json(out / "static/grid.json", grid)
    write_parquet(out / "static/cells.parquet", cells)
    sources = yaml.safe_load((paths.datasets / "sources.yaml").read_text())
    for level in ("ADM0", "ADM1"):
        pin = sources["boundaries"][level]
        boundary_path = paths.root / pin["path"]
        if sha256_file(boundary_path) != pin["sha256"]:
            raise ValueError("Boundary hash mismatch")
        data = read_json(boundary_path)
        for feature in data["features"]:
            feature["geometry"] = mapping(
                shape(feature["geometry"]).simplify(0.005, preserve_topology=True)
            )
        write_json(out / f"static/boundaries_{level.lower()}.json", data)
    fitted = fit_climatology(source, cells, days, protocol)
    rows = []
    for week in range(52):
        issue = pd.Timestamp("2026-01-01") + pd.Timedelta(days=week * 7)
        rows.append(
            pd.DataFrame(
                {
                    "cell_id": cells.cell_id,
                    "week_index": week,
                    "train_climatology_cell": fitted.predict(issue),
                    "train_climatology_dept": fitted.predict_department(issue),
                }
            )
        )
    write_parquet(out / "static/train_climatology_cell.parquet", pd.concat(rows, ignore_index=True))
    return cells


def climatology_for_issue(
    cells: pd.DataFrame, climate: pd.DataFrame, issue: pd.Timestamp
) -> tuple[pd.DataFrame, NDArray]:
    features = (
        climate[climate.week_index.eq(week_index(issue))]
        .set_index("cell_id")
        .reindex(cells.cell_id)[["train_climatology_cell", "train_climatology_dept"]]
        .reset_index(drop=True)
    )
    baseline = features.train_climatology_cell.fillna(features.train_climatology_dept).to_numpy()
    return features, baseline
