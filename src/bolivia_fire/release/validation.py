from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from bolivia_fire.release.build import read_json


def check_schema(schema: pa.Schema, columns: dict[str, Any]) -> list[str]:
    errors = []
    for name, description in columns.items():
        if (isinstance(description, dict) and description.get("scope")
                and name not in schema.names):
            continue
        if name not in schema.names:
            errors.append(f"missing {name}")
            continue
        expected = (description.get("dtype", description.get("type"))
                    if isinstance(description, dict) else description)
        actual = schema.field(name).type
        matches = {
            "string": pa.types.is_string(actual) or pa.types.is_large_string(actual),
            "timestamp": pa.types.is_timestamp(actual),
            "timestamp[ns]": pa.types.is_timestamp(actual),
            "timestamp[us]": pa.types.is_timestamp(actual),
            "int": pa.types.is_integer(actual),
            "bool": pa.types.is_boolean(actual),
            "float32": actual == pa.float32(),
            "float64": actual == pa.float64(),
            "list_of_struct": (pa.types.is_list(actual) or pa.types.is_large_list(actual)),
        }.get(expected, str(actual) == expected)
        if expected is not None and not matches:
            errors.append(f"{name}: expected {expected}, found {actual}")
    return errors


def check_panel_table(table: pd.DataFrame, protocol: dict[str, Any], *,
                      expected_year: int | None = None) -> dict[str, Any]:
    expected = pd.Series(None, index=table.index, dtype=object)
    date = table.issue_date
    horizon = pd.Timedelta(days=protocol["target"]["horizon_days"])
    for partition in ("train", "val_select", "val_calib", "test"):
        start, end = map(pd.Timestamp, protocol["partitions"][partition])
        end += pd.Timedelta(days=1)
        mask = (date >= start) & (date < end)
        # The final test has no later partition to purge against.
        if partition != "test":
            mask &= date + horizon <= end
        expected.loc[mask] = partition
    assignment_errors = int((table.partition.fillna("purged")
                             != expected.fillna("purged")).sum())
    required_missing = ~table.target_complete
    nullness_errors = sum(int((table[name] != required_missing).sum())
                          for name in ("primary_missing", "allconf_missing"))
    duplicate_keys = int(table.duplicated(["cell_id", "issue_date"]).sum())
    null_keys = int(table[["cell_id", "issue_date"]].isna().any(axis=1).sum())
    year_errors = int((date.dt.year != expected_year).sum()) if expected_year is not None else 0
    return {"passed": not (assignment_errors or nullness_errors or duplicate_keys
                            or null_keys or year_errors),
            "rows": len(table), "assignment_errors": assignment_errors,
            "nullness_errors": nullness_errors, "duplicate_keys": duplicate_keys,
            "null_keys": null_keys,
            "year_errors": year_errors,
            "partition_counts": table.partition.fillna("purged").value_counts().to_dict()}


def columns_for(path: str, schemas: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    if path == "datasets/processed/cells.parquet":
        return "cells", schemas["cells"]["columns"]
    if path == "datasets/processed/cell_places.parquet":
        return "cell_places", schemas["cell_places"]["columns"]
    if path.startswith("datasets/processed/panel/"):
        return "panel", schemas["cells"]["columns"] | schemas["panel"]["columns"]
    if "/chirps_features/" in path:
        return "covariates", schemas["covariates"]["chirps"]["columns"]
    if "/vegetation/composites/" in path:
        return "vegetation_features", dict.fromkeys(
            schemas["vegetation_features"]["composites"]["columns"], {})
    if "/vegetation/features/" in path or "/landcover/combined/" in path:
        feature = schemas["vegetation_features"]["features"]
        columns = {"cell_id": "string", "issue_date": "timestamp"}
        columns.update(dict.fromkeys(feature["ordered_features"], "float32"))
        columns.update(dict.fromkeys(feature["audit_columns"], {}))
        if "/landcover/combined/" in path:
            columns.update(landcover_columns(schemas))
        return "vegetation_features", columns
    if "/landcover/features/" in path:
        return "landcover", landcover_columns(schemas)
    if "/landcover/" in path and Path(path).name != "source.parquet":
        return "landcover", schemas["landcover"]["fields"]
    if path.startswith("datasets/interim/"):
        name = Path(path).name
        if name == "detections.parquet":
            return "detections", schemas["detections"]["columns"]
        if name == "acquisition_days.parquet":
            if path == "datasets/interim/acquisition_days.parquet":
                return "legacy_acquisition_days", {
                    "date": "timestamp", "acquired": "bool", "bbox_detection_count": "int64",
                    "bolivia_detection_count": "int64"}
            return "detections", schemas["detections"]["acquisition_days"]["columns"]
    return None


def landcover_columns(schemas: dict[str, Any]) -> dict[str, Any]:
    fields = schemas["landcover"]["fields"]
    return ({"cell_id": "string", "issue_date": "timestamp"}
            | {k: v for k, v in fields.items() if k.startswith("mapbiomas_")}
            | schemas["landcover"]["issue_features"]["fields"])


def sql_path(path: Path) -> str:
    return "'" + path.as_posix().replace("'", "''") + "'"


def validate_data(benchmark: Path, reproduction: Path) -> dict[str, Any]:
    schema_root = benchmark / "datasets/schemas"
    schemas = {p.stem: yaml.safe_load(p.read_text(encoding="utf-8"))
               for p in schema_root.glob("*.yaml")}
    protocol = yaml.safe_load((reproduction / "configs/protocol.yaml").read_text("utf-8"))
    summary = read_json(reproduction / "artifacts/figures/dataset_summary.json")
    parquet_checks = []
    unmatched_schemas = []
    for path in sorted(benchmark.rglob("*.parquet")):
        name = path.relative_to(benchmark).as_posix()
        contract = columns_for(name, schemas)
        if contract is None:
            unmatched_schemas.append(name)
            continue
        schema_name, columns = contract
        parquet = pq.ParquetFile(path)
        errors = check_schema(parquet.schema_arrow, columns)
        null_counts = {parquet.schema_arrow.names[i]: sum(
            parquet.metadata.row_group(g).column(i).statistics.null_count
            if parquet.metadata.row_group(g).column(i).statistics is not None else 0
            for g in range(parquet.metadata.num_row_groups))
            for i in range(len(parquet.schema_arrow))
            if not pa.types.is_nested(parquet.schema_arrow.field(i).type)}
        parquet_checks.append({"path": name, "schema": schema_name, "passed": not errors,
                               "errors": errors, "null_counts": null_counts})
    partitions: Counter = Counter()
    panels = []
    panel_keys: dict[int, Path] = {}
    con = duckdb.connect()
    con.execute("SET memory_limit='1GB'")
    con.execute("SET threads=2")
    con.execute("SET enable_progress_bar=false")
    cells = benchmark / "datasets/processed/cells.parquet"
    con.execute(f"CREATE VIEW cells AS SELECT cell_id FROM read_parquet({sql_path(cells)})")
    for path in sorted((benchmark / "datasets/processed/panel").glob("year=*/part.parquet")):
        year = int(path.parent.name.split("=")[1])
        panel_keys[year] = path
        projection = (
            "cell_id,issue_date,partition,target_complete,"
            "y_primary IS NULL AS primary_missing,y_allconf IS NULL AS allconf_missing"
        )
        frame = con.execute(f"SELECT {projection} FROM read_parquet({sql_path(path)})").df()
        check = check_panel_table(frame, protocol, expected_year=year)
        partitions.update(check["partition_counts"])
        start = max(pd.Timestamp(year=year, month=1, day=1), pd.Timestamp(summary["first_issue"]))
        end = min(pd.Timestamp(year=year, month=12, day=31), pd.Timestamp(summary["last_issue"]))
        expected_rows = len(pd.date_range(start, end, freq="W-MON")) * summary["eligible_cells"]
        check.update(year=year, expected_rows=expected_rows)
        check["passed"] &= check["rows"] == expected_rows
        panels.append(check)
        del frame
    joins = []
    outside_domain = []
    for path in sorted(benchmark.rglob("*.parquet")):
        name = path.relative_to(benchmark).as_posix()
        feature = ("/chirps_features/" in name or "/vegetation/features/" in name
                   or "/landcover/features/" in name or "/landcover/combined/" in name)
        if not feature:
            continue
        year = int(path.parent.name.split("=")[1])
        con.execute(f"CREATE OR REPLACE VIEW f AS SELECT cell_id,issue_date "
                    f"FROM read_parquet({sql_path(path)})")
        rows, unique, nulls = con.execute(
            "SELECT count(*), count(DISTINCT (cell_id, issue_date)), "
            "count(*) FILTER(WHERE cell_id IS NULL OR issue_date IS NULL) FROM f").fetchone()
        missing_cells = con.execute(
            "SELECT count(*) FROM f ANTI JOIN cells USING(cell_id)").fetchone()[0]
        check = {"path": name, "rows": rows, "duplicate_keys": rows - unique,
                 "null_keys": nulls, "missing_cells": missing_cells}
        if year in panel_keys:
            con.execute(f"CREATE OR REPLACE VIEW p AS SELECT cell_id,issue_date "
                        f"FROM read_parquet({sql_path(panel_keys[year])})")
            missing = con.execute(
                "SELECT count(*) FROM f ANTI JOIN p USING(cell_id,issue_date)").fetchone()[0]
            check.update(unjoined_rows=missing,
                         passed=not (rows - unique or nulls or missing or missing_cells))
            joins.append(check)
        else:
            check.update(status="confirmation-support inputs outside the panel domain",
                         passed=year == 2026 and not (rows - unique or nulls or missing_cells))
            outside_domain.append(check)
    con.close()
    expected_partitions = summary["rows_per_partition"]
    counts_passed = dict(partitions) == expected_partitions
    return {"passed": (all(c["passed"] for c in parquet_checks + panels + joins + outside_domain)
                       and counts_passed and not unmatched_schemas),
            "schemas": parquet_checks, "unmatched_schemas": unmatched_schemas,
            "legacy_schema_exceptions": ["Standalone interim acquisition_days.parquet is a "
                                         "retained four-column historical derivative; the current "
                                         "detection_bundle.json selects the canonical table"],
            "timestamp_interpretation": "Arrow ns/us timestamps accepted as UTC midnight; "
                                        "physical unit recorded in extracted Parquet",
            "panel": panels, "partition_counts": dict(partitions),
            "global_panel_key_uniqueness": {
                "passed": all(not c["duplicate_keys"] and not c["year_errors"] for c in panels),
                "basis": "Unique keys within each file and disjoint verified issue-date years"},
            "expected_partition_counts": expected_partitions, "counts_passed": counts_passed,
            "feature_joins": joins, "outside_panel_domain": outside_domain,
            "test_scope": "Schema, row counts, keys, assignments and nullness only; "
                          "no test label values aggregated and no test predictions scored"}
