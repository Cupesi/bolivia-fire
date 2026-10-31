from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Iterable
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from affine import Affine
from filelock import FileLock
from numpy.typing import NDArray

from bolivia_fire.covariates.raster import RasterAccessError, pixel_indices, read_rainfall_window
from bolivia_fire.covariates.storage import (
    Budget,
    append_jsonl,
    atomic_json,
    read_json,
    stored_bytes,
)
from bolivia_fire.firms.storage import sha256_file

BASE_URL = "https://data.chc.ucsb.edu/products/CHIRPS/v3.0/daily/final/rnl/cogs"
PRODUCT = "CHIRPS v3.0 final daily rnl (ERA5 disaggregation), native 0.05 degree COG"
REFERENCE_RULE = "training years only; LOYO for train issues, all for later issues"


def chirps_url(day: date) -> str:
    return f"{BASE_URL}/{day.year}/chirps-v3.0.rnl.{day:%Y.%m.%d}.cog"


def _remote_reader(day: date, budget: Budget) -> tuple[NDArray, dict[str, Any]]:
    attempt = 0
    while True:
        while budget.cooldown_remaining > 0:
            time.sleep(min(30, budget.cooldown_remaining))
        budget.check_storage()
        attempt += 1
        try:
            result = read_rainfall_window(chirps_url(day), budget)
            budget.record_success()
            return result
        except OSError as exc:
            append_jsonl(budget.root / "chirps_errors.jsonl", {
                "date": day.isoformat(), "url": chirps_url(day),
                "attempt": attempt, "error": str(exc),
            })
            if isinstance(exc, RasterAccessError) and exc.record.get("last_http_status") == 429:
                state = budget.record_throttle(exc.record.get("retry_after"))
                print(json.dumps({"date": day.isoformat(), "status": "cooldown", **state}),
                      flush=True)
                continue
            if attempt >= 3:
                raise
            time.sleep(30 * (attempt + 1))


def _array_hash(array: NDArray) -> str:
    return hashlib.sha256(np.ascontiguousarray(array, dtype="<f4").tobytes()).hexdigest()


def extract_year(
    year: int, root: Path, *, budget: Budget | None = None,
    reader: Callable[[date, Budget], tuple[NDArray, dict[str, Any]]] = _remote_reader,
    start: date | None = None, end: date | None = None, workers: int = 1,
) -> dict[str, Any]:
    start = max(start or date(year, 1, 1), date(year, 1, 1))
    end = min(end or date(year, 12, 31), date(year, 12, 31))
    if end < start:
        raise ValueError("Extraction interval is empty")
    if workers != 1:
        raise ValueError("CHIRPS extraction requires concurrency 1")
    budget = budget or Budget(root)
    destination = root / "chirps_daily" / f"year={year}"
    destination.mkdir(parents=True, exist_ok=True)
    with FileLock(str(destination / ".extract.lock"), timeout=0):
        output = destination / "part.npz"
        complete_path = destination / "complete.json"
        completed = {}
        if complete_path.exists():
            complete = read_json(complete_path)
            if sha256_file(output) != complete["sha256"]:
                raise RuntimeError("Completed CHIRPS year checksum mismatch")
            if complete["start"] <= start.isoformat() and complete["end"] >= end.isoformat():
                return complete
            with np.load(output) as archive:
                completed = dict(zip(archive["dates"].tolist(), archive["rain"], strict=True))
        manifest = root / "chirps_manifest.jsonl"
        records = {}
        if manifest.exists():
            for line in manifest.read_text(encoding="utf-8").splitlines():
                record = json.loads(line)
                records[record["date"]] = record
        arrays, dates = [], []
        transform = None
        requested = [stamp.date() for stamp in pd.date_range(start, end)]
        for day in requested:
            budget.check_storage()
            temporary_day = destination / f"{day.isoformat()}.npz"
            cached = records.get(day.isoformat())
            if cached and (temporary_day.exists() or day.isoformat() in completed):
                if temporary_day.exists():
                    with np.load(temporary_day) as archive:
                        array = archive["rain"]
                else:
                    array = completed[day.isoformat()]
                if _array_hash(array) != cached["window_sha256"]:
                    raise RuntimeError("Cached CHIRPS window checksum mismatch")
                record = cached
            else:
                array, record = reader(day, budget)
                array = np.asarray(array, dtype=np.float32)
                budget.check_storage(array.nbytes * 2)
                pending = temporary_day.with_suffix(".npz.part")
                with pending.open("wb") as stream:
                    np.savez_compressed(stream, rain=array)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(pending, temporary_day)
                record = dict(record, date=day.isoformat(), window_sha256=_array_hash(array))
                append_jsonl(manifest, record)
            current_transform = record.get("transform")
            if arrays and (array.shape != arrays[0].shape or current_transform != transform):
                raise ValueError("CHIRPS grid changed within a year")
            transform = current_transform
            arrays.append(array)
            dates.append(day.isoformat())
        budget.check_storage(sum(array.nbytes for array in arrays))
        pending = output.with_suffix(".npz.part")
        with pending.open("wb") as stream:
            np.savez_compressed(
                stream, rain=np.stack(arrays), dates=np.asarray(dates),
                transform=np.asarray(transform if transform is not None else []),
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, output)
        complete = {
            "year": year, "start": start.isoformat(), "end": end.isoformat(),
            "days": len(dates), "sha256": sha256_file(output), "product": PRODUCT,
            "bytes": output.stat().st_size, "transform": transform,
        }
        atomic_json(complete_path, complete)
        for day in dates:
            (destination / f"{day}.npz").unlink(missing_ok=True)
        budget.check_storage()
        return complete


def _window(
    dates: pd.DatetimeIndex, rain: NDArray, issue: pd.Timestamp, days: int,
) -> tuple[NDArray, NDArray]:
    requested = pd.date_range(issue - pd.Timedelta(days=days), periods=days)
    indices = dates.get_indexer(requested)
    values = np.full((days, rain.shape[1]), np.nan, dtype=np.float32)
    present = indices >= 0
    values[present] = rain[indices[present]]
    return values, np.isfinite(values).all(axis=0)


def rainfall_features(
    dates: Iterable, rain: NDArray, cell_ids: list[str], issues: Iterable,
    train_years: Iterable[int],
) -> pd.DataFrame:
    dates = pd.DatetimeIndex(dates).normalize()
    if dates.has_duplicates or not dates.is_monotonic_increasing:
        raise ValueError("Rainfall dates must be unique and increasing")
    if rain.shape != (len(dates), len(cell_ids)):
        raise ValueError("Rainfall dimensions do not match dates and cells")
    training = tuple(sorted(set(train_years)))
    output = []
    for value in issues:
        issue = pd.Timestamp(value).normalize()
        frame = pd.DataFrame({"cell_id": cell_ids, "issue_date": issue})
        windows = {}
        for days in (7, 30, 90):
            values, complete = _window(dates, rain, issue, days)
            total = np.sum(values, axis=0, dtype=np.float64).astype(np.float32)
            total[~complete] = np.nan
            frame[f"rain_sum_{days}d"] = total
            frame[f"rain_complete_{days}d"] = complete
            windows[days] = values
        dry = np.sum(windows[30] < 1, axis=0).astype(np.float32)
        dry[~frame.rain_complete_30d.to_numpy()] = np.nan
        frame["dry_days_30d"] = dry
        baseline = []
        reference_years = tuple(year for year in training if year != issue.year)
        for year in reference_years:
            # February 29 is paired with February 28 in non-leap training years.
            try:
                reference = issue.replace(year=year)
            except ValueError:
                reference = issue.replace(year=year, day=28)
            values, complete = _window(dates, rain, reference, 90)
            total = np.sum(values, axis=0, dtype=np.float64)
            total[~complete] = np.nan
            baseline.append(total)
        if baseline:
            totals = np.stack(baseline)
            count = np.isfinite(totals).sum(axis=0)
            mean = np.divide(np.nansum(totals, axis=0), count,
                             out=np.full(len(cell_ids), np.nan), where=count > 0)
        else:
            count = np.zeros(len(cell_ids), dtype=int)
            mean = np.full(len(cell_ids), np.nan)
        anomaly = np.divide(frame.rain_sum_90d.to_numpy(), mean,
                            out=np.full(len(cell_ids), np.nan), where=mean > 0)
        frame["rain_anomaly_90d"] = anomaly.astype(np.float32)
        frame["rain_climatology_years"] = count.astype(np.int16)
        frame["rain_climatology_complete"] = (count == len(reference_years)) & (count > 0)
        output.append(frame)
    return pd.concat(output, ignore_index=True) if output else pd.DataFrame()


def load_daily(
    root: Path, cells: pd.DataFrame, years: Iterable[int],
) -> tuple[pd.DatetimeIndex, NDArray]:
    dates, arrays = [], []
    for year in sorted(set(years)):
        directory = root / "chirps_daily" / f"year={year}"
        if not (directory / "complete.json").exists():
            continue
        complete = read_json(directory / "complete.json")
        path = directory / "part.npz"
        if sha256_file(path) != complete["sha256"]:
            raise RuntimeError("CHIRPS input checksum mismatch")
        with np.load(path) as archive:
            transform = Affine(*archive["transform"].tolist())
            rows, cols = pixel_indices(transform, cells.longitude, cells.latitude)
            rain = archive["rain"]
            if (rows.min() < 0 or cols.min() < 0 or rows.max() >= rain.shape[1]
                    or cols.max() >= rain.shape[2]):
                raise ValueError("Eligible cell center falls outside the CHIRPS window")
            arrays.append(rain[:, rows, cols])
            dates.extend(archive["dates"].tolist())
    if not dates:
        raise ValueError("No completed CHIRPS daily years are available")
    return pd.DatetimeIndex(dates), np.concatenate(arrays)


def build_features(
    root: Path, cells: pd.DataFrame, issues: Iterable, train_years: Iterable[int],
) -> dict[str, Any]:
    issues = pd.DatetimeIndex(issues)
    training = tuple(train_years)
    counts = {}
    for year in sorted(issues.year.unique()):
        Budget(root).check_storage(len(cells) * 12 * 8 * int(sum(issues.year == year)))
        needed = set(training) | {y - 1 for y in training} | {year, year - 1}
        dates, rain = load_daily(root, cells, needed)
        destination = root / "chirps_features" / f"year={year}" / "part.parquet"
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".parquet.part")
        rows = 0
        schema = pa.Schema.from_pandas(rainfall_features(
            dates, rain, cells.cell_id.tolist(), issues[issues.year == year][:1], training,
        ))
        manifest = root / "chirps_manifest.jsonl"
        metadata = {
            b"product": PRODUCT.encode(),
            b"train_years": json.dumps(training).encode(),
            b"reference_rule": REFERENCE_RULE.encode(),
        }
        if manifest.exists():
            metadata[b"manifest_sha256"] = sha256_file(manifest).encode()
        protocol_path = root.parents[2] / "configs" / "protocol.yaml"
        if protocol_path.exists():
            metadata[b"protocol_sha256"] = sha256_file(protocol_path).encode()
        with pq.ParquetWriter(temporary, schema=schema.with_metadata(metadata),
                              compression="zstd") as writer:
            for issue in issues[issues.year == year]:
                frame = rainfall_features(dates, rain, cells.cell_id.tolist(), [issue], training)
                writer.write_table(pa.Table.from_pandas(frame, preserve_index=False))
                rows += len(frame)
        os.replace(temporary, destination)
        counts[str(year)] = rows
        Budget(root).check_storage()
    return {"feature_rows": counts, "bytes_stored": stored_bytes(root)}
