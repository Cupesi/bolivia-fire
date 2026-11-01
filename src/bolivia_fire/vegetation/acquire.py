from __future__ import annotations

import json
import math
import re
import shutil
import time
from collections import deque
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from filelock import FileLock

from bolivia_fire.vegetation.client import AppEEARSClient, AppEEARSError
from bolivia_fire.vegetation.credentials import load_credentials
from bolivia_fire.vegetation.download import Budget, download_file, validate_bundle
from bolivia_fire.vegetation.manifest import TaskManifest, atomic_json, utc_now
from bolivia_fire.vegetation.request import (
    PRODUCT,
    bolivia_aoi,
    build_request,
    discover_layers,
    year_ranges,
)

FULL_START, FULL_END = date(2011, 12, 1), date(2026, 7, 31)
PILOT_START, PILOT_END = date(2024, 8, 1), date(2024, 9, 30)


def queued_requests(
    client: AppEEARSClient,
    manifest: TaskManifest,
    payloads: Iterable[dict[str, Any]],
) -> Iterator[tuple[dict[str, Any], str]]:
    pending: deque[tuple[dict[str, Any], str]] = deque()
    remaining = iter(payloads)
    exhausted = False
    while pending or not exhausted:
        while len(pending) < client.task_queue_limit and not exhausted:
            try:
                payload = next(remaining)
            except StopIteration:
                exhausted = True
                break
            task_id = manifest.ensure_task(client, payload)
            pending.append((payload, task_id))
            print(f"AppEEARS task {task_id}: attached; queue {len(pending)}", flush=True)
        if pending:
            yield pending.popleft()


def composite_dates(start: date, end: date) -> list[date]:
    return [
        day
        for year in range(start.year, end.year + 1)
        for offset in range(0, 365 + (date(year, 12, 31).timetuple().tm_yday == 366), 16)
        if start <= (day := date(year, 1, 1) + timedelta(days=offset)) <= end
    ]


def estimate_full(pilot: dict[str, Any]) -> dict[str, Any]:
    sizes = list(pilot["composite_bytes"].values())
    if not sizes or any(size <= 0 for size in sizes):
        raise ValueError("Pilot has no measured composite sizes")
    average = sum(sizes) / len(sizes)
    count = len(composite_dates(FULL_START, FULL_END))
    sidecar_bytes = max(0, pilot.get("bytes_stored", sum(sizes)) - sum(sizes))
    transfer = math.ceil(average * count + sidecar_bytes * len(year_ranges(FULL_START, FULL_END)))
    return {
        "kind": "estimate",
        "period": [FULL_START.isoformat(), FULL_END.isoformat()],
        "composite_count": count,
        "estimated_bytes_per_composite": average,
        "estimated_sidecar_bytes_per_task": sidecar_bytes,
        "estimated_transfer_bytes": transfer,
        "storage_safety_multiplier": 1.5,
        "reserved_storage_bytes": math.ceil(transfer * 1.5),
    }


def inspect_raster(path: Path, role: str, source: dict[str, Any]) -> dict[str, Any]:
    import rasterio

    minimum, maximum, valid_count = math.inf, -math.inf, 0
    with rasterio.open(path) as dataset:
        for _, window in dataset.block_windows(1):
            values = dataset.read(1, window=window, masked=True)
            if values.count():
                minimum = min(minimum, float(values.min()))
                maximum = max(maximum, float(values.max()))
                valid_count += int(values.count())
        scaled = None
        if role in {"ndvi", "evi"} and valid_count:
            scaled = dataset.dtypes[0].startswith("float") and -0.201 <= minimum <= maximum <= 1.001
        return {
            "file_name": path.name,
            "role": role,
            "dtype": dataset.dtypes[0],
            "crs": dataset.crs.to_string() if dataset.crs else None,
            "crs_wkt": dataset.crs.to_wkt() if dataset.crs else None,
            "resolution": list(dataset.res),
            "width": dataset.width,
            "height": dataset.height,
            "nodata": dataset.nodata,
            "delivered_scale": dataset.scales[0],
            "delivered_offset": dataset.offsets[0],
            "source_scale_factor": source.get("ScaleFactor"),
            "source_add_offset": source.get("AddOffset"),
            "source_fill_value": source.get("FillValue"),
            "observed_min": minimum if valid_count else None,
            "observed_max": maximum if valid_count else None,
            "valid_pixels": valid_count,
            "values_already_scaled": scaled,
            "file_size": path.stat().st_size,
        }


def _run_task(
    client: AppEEARSClient,
    root: Path,
    payload: dict[str, Any],
    layers: dict[str, str],
    budget: Budget,
    *,
    workers: int,
    timeout: float,
    task_id: str | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    manifests = root / "datasets" / "manifests"
    if task_id is None:
        task_id = TaskManifest(manifests / "appeears_tasks.jsonl").ensure_task(client, payload)
    print(f"AppEEARS task {task_id}: polling", flush=True)
    task = client.wait(task_id, timeout=timeout)
    bundle = client.bundle(task_id)
    if bundle.get("task_id") != task_id:
        raise AppEEARSError("AppEEARS bundle task identity mismatch")
    period = payload["params"]["dates"][0]
    expected_dates = {
        day.strftime("%Y%j")
        for day in composite_dates(
            datetime.strptime(period["startDate"], "%m-%d-%Y").date(),
            datetime.strptime(period["endDate"], "%m-%d-%Y").date(),
        )
    }
    files = validate_bundle(bundle, list(layers.values()), expected_dates=expected_dates)
    safe_files = [
        {
            key: item[key]
            for key in ("file_id", "file_name", "file_size", "sha256", "file_type")
            if key in item
        }
        for item in files
    ]
    raw = root / "datasets" / "raw" / "vegetation" / task_id
    raw.mkdir(parents=True, exist_ok=True)
    budget.check(sum(item["file_size"] for item in files if not (raw / item["file_name"]).exists()))
    atomic_json(
        manifests / f"appeears_bundle_{task_id}.json",
        {
            "task_id": task_id,
            "files": safe_files,
            "retrieved_utc": utc_now(),
        },
    )
    with ThreadPoolExecutor(max_workers=workers) as pool:
        downloaded = list(
            pool.map(
                lambda item: download_file(client, task_id, item, raw, budget),
                safe_files,
            )
        )
    composite_bytes: dict[str, int] = {}
    for item in safe_files:
        if match := re.search(r"_doy(\d{7})", item["file_name"]):
            composite_bytes[match[1]] = composite_bytes.get(match[1], 0) + item["file_size"]
    result = {
        "task_id": task_id,
        "status": "downloaded",
        "period": period,
        "files": downloaded,
        "bytes_stored": sum(item["file_size"] for item in files),
        "bytes_transferred": sum(item["bytes_transferred"] for item in downloaded),
        "composite_bytes": composite_bytes,
        "runtime_seconds": time.perf_counter() - started,
        "created_utc": task.get("created"),
        "completed_utc": task.get("completed"),
        "retrieved_utc": utc_now(),
    }
    if task.get("created") and task.get("completed"):
        with suppress(ValueError, TypeError):
            result["service_elapsed_seconds"] = (
                datetime.fromisoformat(task["completed"]) - datetime.fromisoformat(task["created"])
            ).total_seconds()
    atomic_json(manifests / f"appeears_download_{task_id}.json", result)
    print(f"AppEEARS task {task_id}: verified {len(files)} files", flush=True)
    return result


def acquire(
    root: Path,
    *,
    full: bool = False,
    workers: int = 2,
    timeout: float = 21600,
    minimum_free_gb: float = 55,
) -> dict[str, Any]:
    if not 1 <= workers <= 4:
        raise ValueError("Download workers must be between 1 and 4")
    if minimum_free_gb < 55:
        raise ValueError("Minimum free disk space cannot be less than 55 GiB")
    raw = root / "datasets" / "raw" / "vegetation"
    budget = Budget(raw, minimum_free_gb)
    manifests = root / "datasets" / "manifests"
    report_path = manifests / ("appeears_full.json" if full else "appeears_pilot.json")
    result: dict[str, Any] = {
        "started_utc": utc_now(),
        "mode": "full" if full else "pilot",
        "minimum_free_gib": minimum_free_gb,
        "poll_timeout_seconds": timeout,
        "minimum_poll_interval_seconds": 300,
        "download_workers": workers,
    }
    started = time.perf_counter()
    with FileLock(str(raw / ".acquisition.lock"), timeout=0):
        try:
            with AppEEARSClient(*load_credentials(root)) as client:
                try:
                    product = next(
                        (p for p in client.products() if p.get("ProductAndVersion") == PRODUCT),
                        None,
                    )
                    if not product or not product.get("Available"):
                        raise ValueError("MOD13A2.061 is not available from AppEEARS")
                    metadata = client.layers(PRODUCT)
                    layers = discover_layers(metadata)
                    geo, aoi = bolivia_aoi(root)
                    atomic_json(
                        manifests / "appeears_discovery.json",
                        {
                            "product": product,
                            "selected_layers": layers,
                            "layers": {role: metadata[name] for role, name in layers.items()},
                            "aoi": aoi,
                            "retrieved_utc": utc_now(),
                        },
                    )
                    if full:
                        pilot_path = manifests / "appeears_pilot.json"
                        if not pilot_path.exists():
                            raise ValueError(
                                "Complete the authenticated pilot before full acquisition"
                            )
                        pilot = json.loads(pilot_path.read_text(encoding="utf-8"))
                        if pilot.get("status") != "complete":
                            raise ValueError("Pilot did not complete; full acquisition stopped")
                        estimate = estimate_full(pilot["task"])
                        result["estimate"] = estimate
                        result["free_bytes_before"] = shutil.disk_usage(raw).free
                        budget.check(estimate["reserved_storage_bytes"])
                        ranges = year_ranges(FULL_START, FULL_END)
                    else:
                        ranges = [(PILOT_START, PILOT_END)]
                    tasks = []
                    payloads = [
                        build_request(start, end, list(layers.values()), geo)
                        for start, end in ranges
                    ]
                    manifest = TaskManifest(manifests / "appeears_tasks.jsonl")
                    for payload, task_id in queued_requests(client, manifest, payloads):
                        budget.check()
                        task = _run_task(
                            client, root, payload, layers, budget,
                            workers=workers, timeout=timeout, task_id=task_id,
                        )
                        tasks.append(task)
                        result["tasks"] = tasks
                        result["status"] = "running"
                        atomic_json(report_path, result)
                    if not full:
                        task = tasks[0]
                        task_root = raw / task["task_id"]
                        rasters = []
                        for role, name in layers.items():
                            item = next(
                                f
                                for f in task["files"]
                                if name.lstrip("_") in f["file_name"]
                                and f["file_name"].lower().endswith(".tif")
                            )
                            rasters.append(
                                inspect_raster(task_root / item["file_name"], role, metadata[name])
                            )
                        result["task"], result["rasters"] = task, rasters
                        result["estimate"] = estimate_full(task)
                    result["status"] = "complete"
                finally:
                    result["remote_task_queue_limit"] = client.task_queue_limit
                    result["rate_limit_or_abuse_encountered"] = client.task_queue_limit == 2
                    try:
                        client.logout()
                    except AppEEARSError:
                        result["logout_status"] = "service_error_token_discarded"
        except (AppEEARSError, OSError, ValueError, TimeoutError) as error:
            # Only locally controlled messages are persisted; service response bodies are excluded.
            result["status"] = "stopped"
            result["reason"] = (
                str(error) if isinstance(error, AppEEARSError) else type(error).__name__
            )
            if isinstance(error, OSError) and "free-space" in str(error):
                result["reason"] = "Insufficient disk space to preserve the 55 GiB free-space floor"
        finally:
            result["finished_utc"] = utc_now()
            result["runtime_seconds"] = time.perf_counter() - started
            result["bytes_transferred"] = budget.transferred
            result["bytes_stored"] = sum(
                p.stat().st_size
                for p in raw.rglob("*")
                if p.is_file() and not p.name.endswith(".lock")
            )
            result["free_bytes_after"] = shutil.disk_usage(raw).free
            atomic_json(report_path, result)
    return result
