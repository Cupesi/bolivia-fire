from __future__ import annotations

import math
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import pyarrow.dataset as ds
from fastapi import HTTPException

from backend.app.catalog import Catalog, invalid, missing
from backend.app.prepare import prepared_dataset


def read_rows(
    files: list[Path] | ds.Dataset, columns: list[str], predicate: ds.Expression, maximum: int
) -> list[dict[str, Any]]:
    if isinstance(files, list) and not files:
        return []
    dataset = (
        files
        if isinstance(files, ds.Dataset)
        else ds.dataset([str(path) for path in files], format="parquet")
    )
    scanner = dataset.scanner(
        columns=columns,
        filter=predicate,
        batch_size=min(maximum + 1, 65_536),
        batch_readahead=1,
        fragment_readahead=1,
        use_threads=False,
    )
    result: list[dict[str, Any]] = []
    for batch in scanner.to_batches():
        if len(result) + batch.num_rows > maximum:
            raise HTTPException(503, "Prepared data exceeds the response bound")
        result.extend(batch.to_pylist())
    return result


def probability(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    if not 0 <= value <= 1:
        raise HTTPException(503, "Prepared probability is outside its valid range")
    return value


class Store(Catalog):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.series_dataset = prepared_dataset(self.root, self.panel_files)
        self.holdout_cells = {
            cell
            for cell, held_out in zip(
                self.cell_ids, self.grid["geo_holdout"] if self.grid else [], strict=True
            )
            if held_out
        }

    def geographic_model(self, variant: str, model: str) -> str | None:
        if not model.startswith("strict/"):
            return None
        name = model.split("/", 1)[1]
        family = "lightgbm_" if name.startswith("lightgbm_") else name
        matches = [
            candidate
            for candidate in self.models.get(variant, [])
            if candidate.startswith("strict_geo/")
            and (
                candidate.split("/", 1)[1].startswith(family)
                if family == "lightgbm_"
                else candidate == f"strict_geo/{name}"
            )
        ]
        return matches[0] if len(matches) == 1 else None

    def panel_rows(
        self, start: date, end: date, cell_id: str | None = None
    ) -> list[dict[str, Any]]:
        files = [
            path
            for year in range(start.year, end.year + 1)
            for path in self.panel_by_year.get(year, [])
        ]
        predicate = (ds.field("issue_date") >= datetime.combine(start, time())) & (
            ds.field("issue_date") <= datetime.combine(end, time())
        )
        if cell_id:
            predicate &= ds.field("cell_id") == cell_id
        source = self.series_dataset if cell_id and self.series_dataset is not None else files
        return read_rows(
            source,
            ["cell_id", "issue_date", "y_primary", "n_primary"],
            predicate,
            700 if cell_id else len(self.cell_ids),
        )

    def prediction_rows(
        self, start: date, end: date, variant: str, model: str, cell_id: str | None = None
    ) -> list[dict[str, Any]]:
        predicate = (
            (ds.field("issue_date") >= datetime.combine(start, time()))
            & (ds.field("issue_date") <= datetime.combine(end, time()))
            & (ds.field("model") == model)
        )
        if cell_id:
            predicate &= ds.field("cell_id") == cell_id
        return read_rows(
            self.predictions.get(variant, []),
            ["cell_id", "issue_date", "p"],
            predicate,
            700 if cell_id else len(self.cell_ids),
        )

    def layer(self, issue: date, kind: str, model: str | None, variant: str) -> dict[str, Any]:
        self.check_date(issue, issue=True)
        self.validate_variant(variant)
        if self.grid is None:
            raise HTTPException(503, "Prepared grid is not available")
        observed = {row["cell_id"]: row for row in self.panel_rows(issue, issue)}
        values: dict[str, float | int | None]
        selected = None
        sources: dict[str, str | None] = {}
        geographic = None
        if kind == "estimate":
            selected = self.select_model(variant, model, required=True)
            assert selected is not None
            rows = self.prediction_rows(issue, issue, variant, selected)
            values = {row["cell_id"]: probability(row["p"]) for row in rows}
            if len(values) != len(rows):
                raise HTTPException(503, "Prepared predictions contain duplicate cell-weeks")
            available = bool(rows)
            sources = {
                cell: selected if value is not None else None for cell, value in values.items()
            }
            if selected.startswith("strict/"):
                geographic = self.geographic_model(variant, selected)
                geo_rows = (
                    self.prediction_rows(issue, issue, variant, geographic) if geographic else []
                )
                geo_values = {row["cell_id"]: probability(row["p"]) for row in geo_rows}
                if len(geo_values) != len(geo_rows):
                    raise HTTPException(503, "Prepared predictions contain duplicate cell-weeks")
                available = available or bool(geo_values)
                for cell in self.holdout_cells:
                    values[cell] = geo_values.get(cell)
                    sources[cell] = geographic if values[cell] is not None else None
            if not available:
                raise missing(
                    "Prepared predictions are not available for this issue date and model"
                )
        else:
            values = {cell: row["y_primary"] for cell, row in observed.items()}
        cells = sorted(set(values) & self.cell_index.keys(), key=self.cell_index.__getitem__)
        return {
            "issue_date": issue.isoformat(),
            "kind": kind,
            "model": selected,
            "variant": variant,
            "cell_index": [self.cell_index[cell] for cell in cells],
            "value": [values[cell] for cell in cells],
            "n_primary": [observed.get(cell, {}).get("n_primary") for cell in cells],
            "value_model": [sources.get(cell) for cell in cells],
            "geographic_model": geographic,
        }

    def series(
        self, cell_id: str, start: date | None, end: date | None, model: str | None, variant: str
    ) -> dict[str, Any]:
        if cell_id not in self.cell_index:
            raise missing("Cell is not in the eligible grid")
        start, end = self.date_range(start, end)
        if (end - start).days // 7 + 1 > 700:
            raise invalid("A series is limited to 700 weeks; narrow start and end")
        selected = self.select_model(variant, model, required=False)
        if selected and selected.startswith("strict/") and cell_id in self.holdout_cells:
            selected = self.geographic_model(variant, selected)
        rows = sorted(self.panel_rows(start, end, cell_id), key=lambda row: row["issue_date"])
        predictions = (
            self.prediction_rows(start, end, variant, selected, cell_id) if selected else []
        )
        values = {row["issue_date"]: probability(row["p"]) for row in predictions}
        if len(values) != len(predictions) or len({row["issue_date"] for row in rows}) != len(rows):
            raise HTTPException(503, "Prepared data contains duplicate cell-weeks")
        return {
            "cell_id": cell_id,
            "model": selected,
            "variant": variant,
            "issue_date": [row["issue_date"].date().isoformat() for row in rows],
            "y": [row["y_primary"] for row in rows],
            "n_primary": [row["n_primary"] for row in rows],
            "p": [values.get(row["issue_date"]) for row in rows],
        }
