from __future__ import annotations

import json
import os
import random
import shutil
import threading
import time
from contextlib import suppress
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from filelock import FileLock


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def stored_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


class Budget:
    def __init__(
        self, root: Path, *, transfer_limit: int | None = None,
        storage_limit: int | None = None, minimum_free_bytes: int = 55_000_000_000,
    ) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.transfer_limit = transfer_limit
        self.storage_limit = storage_limit
        self.minimum_free_bytes = minimum_free_bytes
        self.path = root / "transfer_budget.json"
        self.lock = threading.Lock()
        self.request_lock = threading.Lock()

    def pace_request(self, interval: float = 3.0) -> None:
        gate = self.root / "request_gate.json"
        with self.request_lock, FileLock(str(gate) + ".lock"):
            previous = read_json(gate).get("next_request", 0) if gate.exists() else 0
            delay = previous - time.time()
            if delay > 0:
                time.sleep(delay)
            atomic_json(gate, {"next_request": time.time() + interval})

    @property
    def cooldown_remaining(self) -> float:
        path = self.root / "access_state.json"
        state = read_json(path) if path.exists() else {}
        if state.get("consecutive_cooldowns", 0) >= 6:
            raise RuntimeError("CHIRPS circuit breaker: six consecutive cooldowns")
        return max(0, state.get("cooldown_until", 0) - time.time())

    def record_throttle(
        self, retry_after: str | None = None, *, jitter: bool = True,
    ) -> dict[str, Any]:
        path = self.root / "access_state.json"
        state = read_json(path) if path.exists() else {}
        consecutive = state.get("consecutive_cooldowns", 0) + 1
        delay = min(600 * 2 ** (consecutive - 1), 3600)
        if jitter:
            delay = random.uniform(delay, min(delay * 1.1, 3600))
        if retry_after:
            try:
                delay = max(float(retry_after), 0)
            except ValueError:
                with suppress(ValueError, TypeError):
                    delay = max(0, parsedate_to_datetime(retry_after).timestamp() - time.time())
        state = {"consecutive_cooldowns": consecutive, "cooldown_until": time.time() + delay,
                 "delay_seconds": delay, "http_status": 429}
        atomic_json(path, state)
        if consecutive >= 6:
            raise RuntimeError("CHIRPS circuit breaker: six consecutive cooldowns")
        return state

    def record_success(self) -> None:
        atomic_json(self.root / "access_state.json", {"consecutive_cooldowns": 0})

    @property
    def transferred(self) -> int:
        return int(read_json(self.path)["bytes_transferred"]) if self.path.exists() else 0

    def consume(self, count: int) -> None:
        if count < 0:
            raise ValueError("Byte count must be nonnegative")
        with self.lock, FileLock(str(self.path) + ".lock"):
            total = self.transferred + count
            if self.transfer_limit is not None and total > self.transfer_limit:
                raise RuntimeError("Covariate transfer budget exceeded")
            atomic_json(self.path, {"bytes_transferred": total})

    def check_storage(self, additional: int = 0) -> None:
        if shutil.disk_usage(self.root).free - additional < self.minimum_free_bytes:
            raise RuntimeError("Covariate batch would reduce drive free space below 55 GB")
        if (self.storage_limit is not None
                and stored_bytes(self.root) + additional > self.storage_limit):
            raise RuntimeError("Covariate storage budget exceeded")
