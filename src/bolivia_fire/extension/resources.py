from __future__ import annotations

import math
import shutil
import threading
import time
from pathlib import Path
from typing import Any

import psutil

from bolivia_fire.evaluation.artifacts import atomic_json
from bolivia_fire.extension.access import now
from bolivia_fire.extension.registration import Registration
from bolivia_fire.firms.storage import sha256_file

GIB = 1024**3


def implementation_hashes() -> dict[str, str]:
    return {
        f"src/bolivia_fire/extension/{path.name}": sha256_file(path)
        for path in sorted(Path(__file__).parent.glob("*.py"))
    }


class Resources:
    def __init__(
        self,
        registration: Registration,
        stage: str,
        *,
        input_bytes: int = 0,
        memory_limit_gib: float | None = None,
        memory_limit_source: str | None = None,
    ) -> None:
        self.registration, self.stage = registration, stage
        self.root = registration.paths.root
        self.limits = dict(registration.contract["resources"])
        self.registered_memory_limit = self.limits["memory_limit_gib"]
        self.memory_limit_source = memory_limit_source or (
            "runner_argument" if memory_limit_gib is not None else "registered_contract"
        )
        if memory_limit_gib is not None:
            if not math.isfinite(memory_limit_gib) or not 0.25 < memory_limit_gib <= 20:
                raise ValueError(
                    "Runtime memory limit must be finite, above 0.25 and at most 20 GiB"
                )
            self.limits["memory_limit_gib"] = memory_limit_gib
        self.input_bytes = input_bytes
        self.started = now()
        self.tick = time.perf_counter()
        self.peak_rss = 0
        self.current_rss = 0
        self.peak_timestamp: str | None = None
        self.minimum_free = shutil.disk_usage(self.root).free
        self.peak_disk = 0
        self.violation: str | None = None
        self.process = psutil.Process()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._watch, daemon=True)

    def _disk_bytes(self) -> int:
        directories = (
            self.root / "artifacts/models/extension",
            self.root / "artifacts/.cache/extension",
        )
        return self.input_bytes + sum(
            path.stat().st_size
            for directory in directories
            for path in directory.rglob("*")
            if path.is_file()
        )

    def _measure(self) -> None:
        try:
            resident = 0
            for process in [self.process, *self.process.children(recursive=True)]:
                try:
                    resident += process.memory_info().rss
                except psutil.NoSuchProcess:
                    continue
            if resident > self.peak_rss:
                self.peak_rss, self.peak_timestamp = resident, now()
            self.current_rss = resident
            self.minimum_free = min(self.minimum_free, shutil.disk_usage(self.root).free)
            self.peak_disk = max(self.peak_disk, self._disk_bytes())
            if resident >= (self.limits["memory_limit_gib"] - 0.25) * GIB:
                self.violation = "Process-tree memory reached the pre-limit checkpoint margin"
            if self.minimum_free < self.limits["disk_free_floor_gib"] * GIB:
                self.violation = "Extension disk free-space reserve exhausted"
            if self.peak_disk > self.limits["extension_disk_budget_gib"] * GIB:
                self.violation = "Extension disk budget exhausted"
        except Exception as exc:
            self.violation = f"Process-tree resource monitoring failed: {type(exc).__name__}"

    def _watch(self) -> None:
        while not self.stop.wait(1):
            self._measure()

    def check(self, *, projected_ram: int = 0, projected_disk: int = 0) -> None:
        self._measure()
        if self.violation:
            raise RuntimeError(self.violation)
        if self.current_rss + projected_ram >= (self.limits["memory_limit_gib"] - 0.25) * GIB:
            raise RuntimeError("Projected allocation exceeds process-tree memory reserve")
        if (
            shutil.disk_usage(self.root).free - projected_disk
            < self.limits["disk_free_floor_gib"] * GIB
        ):
            raise RuntimeError("Projected allocation exceeds disk free-space reserve")
        if self._disk_bytes() + projected_disk > self.limits["extension_disk_budget_gib"] * GIB:
            raise RuntimeError("Projected allocation exceeds extension disk budget")

    def record(self, status: str) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": status,
            "started_at_utc": self.started,
            "timestamp_utc": now(),
            "runtime_seconds": time.perf_counter() - self.tick,
            "peak_process_tree_rss_bytes": self.peak_rss,
            "current_process_tree_rss_bytes": self.current_rss,
            "registered_memory_limit_gib": self.registered_memory_limit,
            "effective_memory_limit_gib": self.limits["memory_limit_gib"],
            "memory_limit_source": self.memory_limit_source,
            "implementation_hashes": implementation_hashes(),
            "peak_process_tree_rss_timestamp_utc": self.peak_timestamp,
            "minimum_free_disk_bytes": self.minimum_free,
            "peak_extension_disk_bytes": self.peak_disk,
            "sample_interval_seconds": 1,
            "violation": self.violation,
        }

    def __enter__(self) -> Resources:
        try:
            self.check()
        except RuntimeError:
            atomic_json(
                self.root / f"artifacts/evaluation/extension_resources_{self.stage}.json",
                {**self.record("failed"), **self.registration.provenance()},
            )
            raise
        self.thread.start()
        return self

    def __exit__(self, kind: type | None, *args: object) -> None:
        self.stop.set()
        self.thread.join(timeout=2)
        self._measure()
        atomic_json(
            self.root / f"artifacts/evaluation/extension_resources_{self.stage}.json",
            {
                **self.record("failed" if kind or self.violation else "completed"),
                **self.registration.provenance(),
            },
        )
        if kind is None and self.violation:
            raise RuntimeError(self.violation)


def owned_bytes(files: list[Path]) -> int:
    return sum(path.stat().st_size for path in set(files))
