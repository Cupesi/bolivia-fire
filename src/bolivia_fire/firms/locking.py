from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

from filelock import FileLock, Timeout

from bolivia_fire.firms.storage import durable_write


class RunLock:
    def __init__(
        self,
        directory: Path,
        *,
        clock: Callable[[], float] = time.time,
        heartbeat_seconds: float = 10,
        stale_seconds: float = 120,
    ) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.lock = FileLock(str(directory / "acquire_firms.lock"), timeout=0)
        self.metadata = directory / "acquire_firms_owner.json"
        self.clock = clock
        self.heartbeat_seconds = heartbeat_seconds
        self.stale_seconds = stale_seconds
        self.recovered_stale_lock = False
        self.recovered_interrupted_lock = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _write(self, active: bool) -> None:
        body = json.dumps({"pid": os.getpid(), "heartbeat": self.clock(), "active": active})
        temporary = self.metadata.with_suffix(".json.part")
        durable_write(temporary, body.encode())
        os.replace(temporary, self.metadata)

    def _heartbeat(self) -> None:
        while not self._stop.wait(self.heartbeat_seconds):
            self._write(True)

    def __enter__(self) -> RunLock:
        try:
            self.lock.acquire()
        except Timeout:
            raise RuntimeError("Another FIRMS acquisition holds the run lock") from None
        try:
            if self.metadata.exists():
                try:
                    owner = json.loads(self.metadata.read_text(encoding="utf-8"))
                    self.recovered_interrupted_lock = bool(owner.get("active"))
                    self.recovered_stale_lock = (
                        self.recovered_interrupted_lock
                        and self.clock() - owner["heartbeat"] >= self.stale_seconds
                    )
                except (ValueError, KeyError, TypeError):
                    self.recovered_stale_lock = True
            self._write(True)
            self._thread = threading.Thread(target=self._heartbeat, daemon=True)
            self._thread.start()
            return self
        except BaseException:
            self.lock.release()
            raise

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        try:
            self._write(False)
        finally:
            self.lock.release()
