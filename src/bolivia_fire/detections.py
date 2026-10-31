from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import shapely
from shapely.geometry.base import BaseGeometry

from bolivia_fire.config import Paths
from bolivia_fire.firms.storage import durable_write, request_identity, sha256_file
from bolivia_fire.firms.validation import HEADER, validate_csv
from bolivia_fire.grid import (
    coordinate_indices,
    load_boundary_geometries,
    provenance_metadata,
    write_parquet,
)
from bolivia_fire.outages import load_outages, observation_days

SOURCE_DTYPES = dict.fromkeys(HEADER, "string")
type Statistics = dict[str, int | float | str]


def selection_mask(detections: pd.DataFrame, *, all_confidence: bool = False) -> pd.Series:
    confidence = ["l", "n", "h"] if all_confidence else ["n", "h"]
    return detections["type"].eq(0) & detections.confidence.isin(confidence)


def _manifest_snapshot(path: Path) -> tuple[list[dict[str, Any]], str]:
    body = path.read_bytes() if path.exists() else b""
    lines = body.splitlines(keepends=True)
    records = []
    for index, line in enumerate(lines):
        try:
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError
        except ValueError:
            if index == len(lines) - 1 and not line.endswith(b"\n"):
                break
            raise ValueError(f"Corrupt manifest record {index + 1}") from None
        records.append(record)
    return records, hashlib.sha256(body).hexdigest()


def _canonical_partitions(
    records: list[dict[str, Any]], protocol: dict[str, Any]
) -> tuple[list[tuple[dict[str, Any], set[date]]], list[dict[str, Any]]]:
    candidates = []
    rejected = []
    for index, record in enumerate(records):
        if record.get("outcome") not in {"ok_rows", "ok_empty"}:
            continue
        identity = request_identity(record)
        if (
            identity is None
            or identity[0] != protocol["target"]["source_product"]
            or identity[1] != tuple(protocol["study_area"]["acquisition_bbox"])
        ):
            rejected.append(
                {
                    "path": record.get("path"),
                    "manifest_index": index,
                    "reason": "request_identity_mismatch",
                }
            )
            continue
        candidates.append((index, record))
    candidates.sort(key=lambda item: (-int(item[1]["day_range"]), item[1]["start"], item[0]))
    covered: set[date] = set()
    chosen = []
    overlaps = rejected
    for _, record in candidates:
        start = date.fromisoformat(record["start"])
        length = int(record["day_range"])
        if not 1 <= length <= 5:
            raise ValueError("Manifest partition must span one to five days")
        interval = {start + timedelta(days=offset) for offset in range(length)}
        remaining = interval - covered
        excluded = interval & covered
        if excluded:
            overlaps.append(
                {
                    "path": record["path"],
                    "sha256": record["sha256"],
                    "start": record["start"],
                    "day_range": length,
                    "rows": int(record["rows"]),
                    "excluded_dates": sorted(map(str, excluded)),
                    "fully_covered": not remaining,
                }
            )
        if remaining:
            chosen.append((record, remaining))
            covered.update(interval)
    return chosen, overlaps


def _empty_source() -> pd.DataFrame:
    return pd.DataFrame(
        {
            **{name: pd.Series(dtype="string") for name in HEADER},
            "source_file": pd.Series(dtype="string"),
            "source_line": pd.Series(dtype="int64"),
            "_source_order": pd.Series(dtype="int64"),
        }
    )


def _source_frames(
    paths: Paths,
    protocol: dict[str, Any],
    partitions: list[tuple[dict[str, Any], set[date]]],
    days: pd.DataFrame,
) -> Iterator[pd.DataFrame]:
    for order, (record, retained_dates) in enumerate(partitions):
        path = (paths.root / record["path"]).resolve()
        if not path.is_relative_to(paths.firms_raw.resolve()):
            raise ValueError("Manifest partition is outside the FIRMS raw directory")
        if sha256_file(path) != record["sha256"]:
            raise ValueError(f"Raw partition hash mismatch: {path.name}")
        validation = validate_csv(
            path.read_bytes(),
            date.fromisoformat(record["start"]),
            int(record["day_range"]),
            tuple(protocol["study_area"]["acquisition_bbox"]),
        )
        if not validation.ok:
            raise ValueError(f"Invalid raw partition: {path.name} ({validation.outcome})")
        frame = pd.read_csv(path, dtype=SOURCE_DTYPES, keep_default_na=False)
        if len(frame) != record["rows"]:
            raise ValueError(f"Manifest row count mismatch: {path.name}")
        frame["source_file"] = path.relative_to(paths.root).as_posix()
        frame["source_line"] = np.arange(1, len(frame) + 1, dtype=np.int64)
        frame["_source_order"] = order
        frame = frame[frame.acq_date.isin(set(map(str, retained_dates)))]
        days.loc[days.date.isin(pd.to_datetime(sorted(retained_dates))), "acquired"] = True
        yield frame


def _parse_source(data: pd.DataFrame, country: BaseGeometry) -> pd.DataFrame:
    data = data.astype(SOURCE_DTYPES)
    for name in ("latitude", "longitude", "bright_ti4", "scan", "track", "bright_ti5", "frp"):
        data[name] = pd.to_numeric(data[name], errors="raise").astype(
            "float64" if name in {"latitude", "longitude"} else "float32"
        )
    data["type"] = pd.to_numeric(data["type"], errors="raise").astype("int8")
    data["acq_time"] = data.acq_time.str.zfill(4)
    data["acq_datetime"] = pd.to_datetime(
        data.acq_date + " " + data.acq_time, format="%Y-%m-%d %H%M", utc=True, errors="raise"
    )
    shapely.prepare(country)
    data["in_bolivia"] = shapely.covers(country, shapely.points(data.longitude, data.latitude))
    rows, cols = coordinate_indices(data.longitude, data.latitude)
    data["cell_id"] = pd.Series(
        [f"r{row:04d}c{col:04d}" for row, col in zip(rows, cols, strict=True)], dtype="string"
    )
    data["primary"] = selection_mask(data)
    data["allconf"] = selection_mask(data, all_confidence=True)
    return data


def _process_detections(
    paths: Paths,
    protocol: dict[str, Any],
    country: BaseGeometry,
    sink: Callable[[pd.DataFrame, dict[bytes, bytes]], None],
    *,
    manifest_snapshot: Path | None = None,
) -> tuple[pd.DataFrame, Statistics, dict[bytes, bytes]]:
    records, manifest_hash = _manifest_snapshot(manifest_snapshot or paths.firms_manifest)
    outages, outage_hash = load_outages(paths)
    partitions, overlaps = _canonical_partitions(records, protocol)
    days = pd.DataFrame(
        {
            "date": pd.date_range(
                protocol["time"]["acquisition_start"], protocol["time"]["acquisition_end"]
            ),
            "acquired": False,
            "bbox_detection_count": np.int64(0),
            "bolivia_detection_count": np.int64(0),
        }
    )
    paths.temporary.mkdir(parents=True, exist_ok=True)
    metadata = provenance_metadata(paths, protocol)
    metadata[b"firms_manifest_sha256"] = manifest_hash.encode()
    metadata[b"outage_snapshot_sha256"] = outage_hash.encode()
    metadata[b"overlap_partitions"] = json.dumps(overlaps, sort_keys=True).encode()
    metadata[b"canonical_partition_rule"] = (
        b"longest, earliest start, manifest order; uncovered days"
    )
    statistics: Statistics = {
        "rows": 0,
        "source_rows": 0,
        "duplicates": 0,
        "in_bolivia": 0,
        "firms_manifest_sha256": manifest_hash,
        "manifest_records": len(records),
        "canonical_partitions": len(partitions),
        "overlap_partitions_skipped": sum(
            record.get("fully_covered", False) for record in overlaps
        ),
        "overlap_days_skipped": sum(len(record.get("excluded_dates", [])) for record in overlaps),
        "identity_rejections": sum("reason" in record for record in overlaps),
        "outage_snapshot_sha256": outage_hash,
        "outage_table_rows": len(outages),
    }
    with (
        tempfile.TemporaryDirectory(prefix="detections-", dir=paths.temporary) as scratch,
        duckdb.connect(
            str(Path(scratch) / "source.duckdb"),
            config={
                "memory_limit": "512MB",
                "threads": 2,
                "temp_directory": str(Path(scratch) / "spill"),
            },
        ) as connection,
    ):
        connection.register("source_batch", _empty_source())
        connection.execute("CREATE TABLE raw AS SELECT * FROM source_batch")
        connection.unregister("source_batch")
        for frame in _source_frames(paths, protocol, partitions, days):
            statistics["source_rows"] += len(frame)
            connection.register("source_batch", frame)
            connection.execute("INSERT INTO raw SELECT * FROM source_batch")
            connection.unregister("source_batch")
        columns = ", ".join(f'"{name}"' for name in HEADER)
        connection.execute(
            "CREATE TABLE deduplicated AS SELECT * FROM raw QUALIFY "
            f"row_number() OVER (PARTITION BY {columns} "
            "ORDER BY _source_order, source_line) = 1"
        )
        statistics["rows"] = connection.execute("SELECT count(*) FROM deduplicated").fetchall()[0][
            0
        ]
        statistics["duplicates"] = statistics["source_rows"] - statistics["rows"]
        metadata[b"exact_duplicates_removed"] = str(statistics["duplicates"]).encode()
        relation = connection.sql(
            "SELECT * EXCLUDE (_source_order) FROM deduplicated ORDER BY _source_order, source_line"
        )
        reader = (
            relation.to_arrow_reader(100_000)
            if hasattr(relation, "to_arrow_reader")
            else relation.fetch_record_batch(100_000)
        )
        emitted = False
        for batch in reader:
            emitted = True
            data = _parse_source(batch.to_pandas(), country)
            observed = data.acq_datetime.dt.tz_localize(None).dt.normalize()
            for name, counts in (
                ("bbox_detection_count", observed.value_counts()),
                ("bolivia_detection_count", observed[data.in_bolivia].value_counts()),
            ):
                days[name] += days.date.map(counts).fillna(0).astype("int64")
            statistics["in_bolivia"] += int(data.in_bolivia.sum())
            sink(data, metadata)
        if not emitted:
            sink(_parse_source(_empty_source().drop(columns="_source_order"), country), metadata)
    statistics["in_bolivia_share"] = (
        statistics["in_bolivia"] / statistics["rows"] if statistics["rows"] else 0.0
    )
    statistics["acquired_days"] = int(days.acquired.sum())
    days = observation_days(days, outages)
    statistics["observed_days"] = int(days.observed.sum())
    return days, statistics, metadata


def read_detections(
    paths: Paths, protocol: dict[str, Any], country: BaseGeometry
) -> tuple[pd.DataFrame, pd.DataFrame, Statistics]:
    frames = []
    days, statistics, _ = _process_detections(
        paths, protocol, country, lambda frame, metadata: frames.append(frame)
    )
    return pd.concat(frames, ignore_index=True), days, statistics


def build_detections(
    paths: Paths,
    protocol: dict[str, Any],
    output_directory: Path | None = None,
    *,
    manifest_snapshot: Path | None = None,
) -> Statistics:
    country, _ = load_boundary_geometries(paths)
    interim = output_directory or paths.datasets / "interim"
    interim.mkdir(parents=True, exist_ok=True)
    bundle = interim / "detection_bundles" / uuid4().hex
    bundle.mkdir(parents=True)
    destination = bundle / "detections.parquet"
    writer = None

    def write_batch(frame: pd.DataFrame, metadata: dict[bytes, bytes]) -> None:
        nonlocal writer
        table = pa.Table.from_pandas(frame, preserve_index=False)
        table = table.replace_schema_metadata({**(table.schema.metadata or {}), **metadata})
        if writer is None:
            writer = pq.ParquetWriter(destination, table.schema, compression="zstd")
        writer.write_table(table)

    try:
        days, statistics, metadata = _process_detections(
            paths, protocol, country, write_batch, manifest_snapshot=manifest_snapshot
        )
    finally:
        if writer is not None:
            writer.close()
    availability = bundle / "acquisition_days.parquet"
    write_parquet(days, availability, metadata)
    for source in (destination, availability):
        with source.open("r+b") as stream:
            os.fsync(stream.fileno())
    pointer = {
        "directory": bundle.relative_to(interim).as_posix(),
        "files": {path.name: sha256_file(path) for path in (destination, availability)},
        "provenance": {
            key.decode(): metadata[key].decode()
            for key in (b"protocol_sha256", b"firms_manifest_sha256", b"outage_snapshot_sha256")
        },
    }
    temporary = interim / f"detection_bundle.{bundle.name}.tmp"
    durable_write(temporary, json.dumps(pointer, sort_keys=True).encode())
    os.replace(temporary, interim / "detection_bundle.json")
    statistics["bundle_id"] = bundle.name
    return statistics


def resolve_detection_bundle(
    paths: Paths, protocol: dict[str, Any], input_directory: Path | None = None
) -> tuple[Path, Path, dict[bytes, bytes]]:
    interim = input_directory or paths.datasets / "interim"
    pointer = json.loads((interim / "detection_bundle.json").read_text(encoding="utf-8"))
    bundle = (interim / pointer["directory"]).resolve()
    if not bundle.is_relative_to((interim / "detection_bundles").resolve()):
        raise ValueError("Detection bundle is outside its versioned directory")
    expected = {key.encode(): value.encode() for key, value in pointer["provenance"].items()}
    current_protocol = provenance_metadata(paths, protocol)[b"protocol_sha256"]
    _, outage_hash = load_outages(paths)
    if expected.get(b"protocol_sha256") != current_protocol:
        raise ValueError("Detection bundle protocol provenance mismatch")
    if expected.get(b"outage_snapshot_sha256") != outage_hash.encode():
        raise ValueError("Detection bundle outage provenance mismatch")
    files = [bundle / name for name in ("detections.parquet", "acquisition_days.parquet")]
    for source in files:
        if sha256_file(source) != pointer["files"][source.name]:
            raise ValueError("Detection bundle file hash mismatch")
        metadata = pq.read_schema(source).metadata or {}
        for key in (b"protocol_sha256", b"firms_manifest_sha256", b"outage_snapshot_sha256"):
            if key not in expected or metadata.get(key) != expected[key]:
                raise ValueError(f"Detection bundle provenance mismatch: {key.decode()}")
    return files[0], files[1], expected
