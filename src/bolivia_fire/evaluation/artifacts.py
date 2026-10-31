from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import time
import uuid
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

import joblib
from filelock import FileLock

from bolivia_fire.config import Paths, load_protocol
from bolivia_fire.firms.storage import sha256_file
from bolivia_fire.pipeline_cli import peak_ram_mb


def atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with pending.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


def save_candidate(path: Path, candidate: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        joblib.dump(candidate, pending, compress=3)
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


def snapshot(paths: Paths, *, variant: str, include_test: bool = False) -> dict[str, str]:
    protocol = load_protocol(paths.root)
    files = [paths.root / "configs/protocol.yaml", paths.root / "configs/experiments.yaml"]
    sources = paths.datasets / "sources.yaml"
    if sources.exists():
        files.append(sources)
    first = int(protocol["partitions"]["train"][0][:4])
    last = int(protocol["partitions"]["test" if include_test else "val_calib"][1][:4])
    files.extend(
        path
        for path in (paths.datasets / "processed/panel").glob("year=*/part.parquet")
        if first <= int(path.parent.name.removeprefix("year=")) <= last
    )
    if variant.startswith("retrospective"):
        covariates = paths.datasets / "processed/covariates"
        if (covariates / "chirps_manifest.jsonl").exists():
            files.append(covariates / "chirps_manifest.jsonl")
        files.extend(
            path for path in (covariates / "chirps_features").glob("year=*/part.parquet")
            if first <= int(path.parent.name.removeprefix("year=")) <= last
        )
    return {path.relative_to(paths.root).as_posix(): sha256_file(path) for path in sorted(files)}


def git_commit(root: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else None


def hardware() -> dict[str, Any]:
    ram: int | None = None
    if os.name == "nt":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("load", ctypes.c_ulong),
                *[
                    (name, ctypes.c_ulonglong)
                    for name in (
                        "total_physical",
                        "available_physical",
                        "total_pagefile",
                        "available_pagefile",
                        "total_virtual",
                        "available_virtual",
                        "extended",
                    )
                ],
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            ram = status.total_physical
    elif hasattr(os, "sysconf"):
        ram = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    return {
        "cpu": platform.processor() or platform.machine(),
        "logical_cpus": os.cpu_count(),
        "ram_bytes": ram,
    }


def provenance(
    paths: Paths,
    config: dict[str, Any],
    tick: float,
    *,
    variant: str,
    include_test: bool = False,
) -> dict[str, Any]:
    return {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "git_commit": git_commit(paths.root),
        "dataset_snapshot_hashes": snapshot(paths, variant=variant, include_test=include_test),
        "protocol_sha256": sha256_file(paths.root / "configs/protocol.yaml"),
        "experiments_sha256": sha256_file(paths.root / "configs/experiments.yaml"),
        "config": config,
        "seed": config["seed"],
        "environment": {
            "python": platform.python_version(),
            "packages": {
                name: version(name)
                for name in (
                    "numpy",
                    "pyarrow",
                    "pandas",
                    "scikit-learn",
                    "lightgbm",
                    "joblib",
                )
            },
        },
        "hardware": hardware(),
        "runtime_seconds": time.perf_counter() - tick,
        "peak_ram_mb": peak_ram_mb(),
    }


def record_final_opening(
    paths: Paths,
    model_ids: dict[str, str],
    calibrator_ids: dict[str, str],
    config: dict[str, Any],
    reopen_reason: str | None,
    *,
    variant: str,
) -> dict[str, Any]:
    destination = paths.root / "artifacts/evaluation/final_test_log.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(destination.with_suffix(".lock"))):
        entries = json.loads(destination.read_text()) if destination.exists() else []
        reason = reopen_reason.strip() if reopen_reason else None
        if entries and not reason:
            raise ValueError("Final test already opened; a second opening requires --reopen-reason")
        entry = provenance(paths, config, time.perf_counter(), variant=variant, include_test=True)
        panel = {
            name: digest
            for name, digest in entry["dataset_snapshot_hashes"].items()
            if name.startswith("datasets/processed/panel/")
        }
        entry.update(
            {
                "model_ids": model_ids,
                "calibrator_ids": calibrator_ids,
                "panel_snapshot_sha256": hashlib.sha256(
                    json.dumps(panel, sort_keys=True).encode()
                ).hexdigest(),
                "reopening": bool(entries),
                "reopen_reason": reason,
            }
        )
        entries.append(entry)
        atomic_json(destination, entries)
        return entry
