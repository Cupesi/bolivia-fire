from __future__ import annotations

import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil


class Resources:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.stop = threading.Event()
        self.error: BaseException | None = None
        self.peak_rss = 0
        self.peak_at = ""
        self.minimum_free = shutil.disk_usage(root).free
        self.thread = threading.Thread(target=self._monitor, daemon=True)

    def _sample(self) -> None:
        process = psutil.Process()
        rss = sum(p.memory_info().rss for p in [process, *process.children(recursive=True)])
        if rss > self.peak_rss:
            self.peak_rss, self.peak_at = rss, datetime.now(UTC).isoformat()
        if rss >= 11 * 1024**3:
            raise RuntimeError("Process-tree memory reached 11 GiB safety checkpoint")
        self.minimum_free = min(self.minimum_free, shutil.disk_usage(self.root).free)

    def _monitor(self) -> None:
        try:
            while not self.stop.wait(1):
                self._sample()
        except BaseException as exc:
            self.error = exc

    def __enter__(self) -> Resources:
        self._sample()
        self.thread.start()
        self.check(1024**3)
        return self

    def check(self, additional: int = 0) -> None:
        if self.error is not None:
            raise RuntimeError("Process-tree resource monitoring failed") from self.error
        if self.peak_rss + additional >= 12 * 1024**3:
            raise RuntimeError("Projected allocation exceeds 12 GiB process-tree memory")
        free = shutil.disk_usage(self.root).free
        self.minimum_free = min(self.minimum_free, free)
        if free - additional < 55 * 1024**3:
            raise RuntimeError("Projected write would breach the 55 GiB free-space floor")
        if additional:
            raw = self.root.parent.parent / "raw" / "vegetation"
            stored = sum(
                p.stat().st_size
                for base in (self.root, raw)
                for p in base.rglob("*")
                if p.is_file()
            )
            if stored + additional > 30 * 1024**3:
                raise RuntimeError("Projected vegetation storage exceeds the 30 GiB budget")

    def record(self) -> dict[str, Any]:
        return {
            "peak_process_tree_rss_bytes": self.peak_rss,
            "peak_rss_at_utc": self.peak_at,
            "sample_interval_seconds": 1,
            "minimum_free_bytes": self.minimum_free,
        }

    def __exit__(self, *args: object) -> None:
        self.stop.set()
        self.thread.join()
        self._sample()
