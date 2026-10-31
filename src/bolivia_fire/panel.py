from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from bolivia_fire.config import Paths
from bolivia_fire.detections import resolve_detection_bundle
from bolivia_fire.features import DailyHistory, TrainingClimatology
from bolivia_fire.firms.storage import sha256_file
from bolivia_fire.grid import provenance_metadata
from bolivia_fire.labels import labels_for_issue
from bolivia_fire.splits import issue_dates, partition_for_issue


def _read_interval(source: Path, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    return pd.read_parquet(
        source,
        columns=["cell_id", "acq_datetime", "in_bolivia", "type", "confidence", "frp"],
        filters=[
            ("acq_datetime", ">=", start.tz_localize("UTC")),
            ("acq_datetime", "<", end.tz_localize("UTC")),
        ],
    )


def _prior_cells(source: Path, before: pd.Timestamp) -> set[str]:
    dataset = ds.dataset(source, format="parquet")
    condition = (
        ds.field("in_bolivia")
        & (ds.field("type") == 0)
        & ds.field("confidence").isin(["n", "h"])
        & (ds.field("acq_datetime") < before.tz_localize("UTC"))
    )
    cells = set()
    for batch in dataset.to_batches(columns=["cell_id"], filter=condition, batch_size=100_000):
        cells.update(batch.column(0).to_pylist())
    return cells


def fit_climatology(
    source: Path, cells: pd.DataFrame, days: pd.DataFrame, protocol: dict[str, Any]
) -> TrainingClimatology:
    first, last = map(pd.Timestamp, protocol["partitions"]["train"])
    fitted = TrainingClimatology(cells, range(first.year, last.year + 1))
    available = days.set_index("date").observed
    training = [
        issue
        for issue in issue_dates(protocol, first, last)
        if partition_for_issue(issue, protocol) == "train"
        and available.reindex(pd.date_range(issue, periods=7), fill_value=False).all()
    ]
    for year in sorted({issue.year for issue in training}):
        issues = [issue for issue in training if issue.year == year]
        detections = _read_interval(source, min(issues), max(issues) + pd.Timedelta(days=7))
        for issue in issues:
            fitted.add(issue, labels_for_issue(cells, detections, days, issue))
    return fitted


def build_panel(
    paths: Paths,
    protocol: dict[str, Any],
    start_issue: str | pd.Timestamp | None = None,
    end_issue: str | pd.Timestamp | None = None,
    output_directory: Path | None = None,
) -> dict[str, Any]:
    tick = time.perf_counter()
    cells_source = (output_directory or paths.datasets / "processed") / "cells.parquet"
    detections_source, days_source, bundle_provenance = resolve_detection_bundle(
        paths, protocol, output_directory
    )
    cells = pd.read_parquet(cells_source)
    days = pd.read_parquet(days_source)
    issues = issue_dates(protocol, start_issue, end_issue)
    if not len(issues):
        raise ValueError("Issue range contains no protocol Monday")
    climatology = fit_climatology(detections_source, cells, days, protocol)
    summary: dict[str, Any] = {"rows": 0, "issues": [], "files": []}
    provenance = provenance_metadata(paths, protocol)
    provenance.update(bundle_provenance)
    cell_metadata = pq.read_schema(cells_source).metadata or {}
    if cell_metadata.get(b"protocol_sha256") != provenance[b"protocol_sha256"]:
        raise ValueError("Cells and detection bundle protocol provenance mismatch")
    for name, source in (
        ("cells", cells_source),
        ("detections", detections_source),
        ("acquisition_days", days_source),
    ):
        provenance[f"{name}_sha256".encode()] = sha256_file(source).encode()
    for year in sorted(set(issues.year)):
        year_issues = issues[issues.year == year]
        first, last = year_issues.min(), year_issues.max()
        history_start = first - pd.Timedelta(days=730)
        detections = _read_interval(detections_source, history_start, last + pd.Timedelta(days=7))
        history = DailyHistory(
            cells,
            detections,
            days,
            history_start,
            last,
            _prior_cells(detections_source, history_start),
        )
        directory = (output_directory or paths.datasets / "processed") / "panel" / f"year={year}"
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / "part.parquet"
        temporary = directory / "part.parquet.tmp"
        writer = None
        try:
            for issue in year_issues:
                features = history.features(issue)
                features["train_climatology_cell"] = climatology.predict(issue)
                features["train_climatology_dept"] = climatology.predict_department(issue)
                labels = labels_for_issue(cells, detections, days, issue)
                row = pd.concat(
                    [cells, features.drop(columns="cell_id"), labels.drop(columns="cell_id")],
                    axis=1,
                )
                row["issue_date"] = issue
                row["partition"] = pd.Series(
                    partition_for_issue(issue, protocol), index=row.index, dtype="string"
                )
                table = pa.Table.from_pandas(row, preserve_index=False)
                metadata = dict(table.schema.metadata or {})
                metadata.update(provenance)
                metadata[b"feature_information_rule"] = b"acq_datetime < issue_date (UTC)"
                table = table.replace_schema_metadata(metadata)
                if writer is None:
                    writer = pq.ParquetWriter(temporary, table.schema, compression="zstd")
                writer.write_table(table)
                summary["rows"] += len(row)
                summary["issues"].append(
                    {
                        "issue_date": issue.date().isoformat(),
                        "rows": len(row),
                        "target_complete": bool(labels.target_complete.all()),
                        "positive_count": int(labels.y_primary.sum())
                        if labels.target_complete.all()
                        else None,
                        "positive_rate": float(labels.y_primary.mean())
                        if labels.target_complete.all()
                        else None,
                        "feature_window_complete": bool(features.feature_window_complete.all()),
                        "window_complete_28d": bool(features.window_complete_28d.all()),
                        "window_obs_complete_28d": bool(features.window_obs_complete_28d.all()),
                        "window_obs_complete_365d": bool(features.window_obs_complete_365d.all()),
                    }
                )
        finally:
            if writer is not None:
                writer.close()
        os.replace(temporary, destination)
        summary["files"].append(str(destination.resolve()))
        del history, detections
    summary["runtime_seconds"] = time.perf_counter() - tick
    return summary


def print_summary(summary: dict[str, Any]) -> None:
    print(json.dumps(summary, sort_keys=True, allow_nan=False))
