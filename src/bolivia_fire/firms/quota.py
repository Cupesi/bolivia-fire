from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from filelock import FileLock

WINDOW = 600.0


class Quota:
    def __init__(
        self,
        directory: Path,
        budget: int = 4000,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 1 <= budget <= 5000:
            raise ValueError("Budget must be between 1 and 5000")
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "firms_quota.json"
        self.lock = FileLock(str(directory / "firms_quota.lock"), timeout=180)
        self.budget = budget
        self.clock = clock
        self.sleep = sleep

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "events": [],
                "last_reconcile": None,
                "last_status_request": None,
                "remote_request_start": None,
                "remote": None,
                "blocked_until": 0,
            }
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(state["events"], list):
                raise ValueError
            state.setdefault("last_status_request", state["last_reconcile"])
            state.setdefault("remote_request_start", state["last_reconcile"])
            for event in state["events"]:
                if (
                    not isinstance(event, list)
                    or len(event) != 2
                    or not isinstance(event[0], (int, float))
                    or not math.isfinite(event[0])
                    or type(event[1]) is not int
                    or not 1 <= event[1] <= 5000
                ):
                    raise ValueError
            for name in ("last_status_request", "last_reconcile", "remote_request_start"):
                value = state[name]
                if value is not None and (
                    not isinstance(value, (int, float)) or not math.isfinite(value)
                ):
                    raise ValueError
            if not isinstance(state["blocked_until"], (int, float)) or not math.isfinite(
                state["blocked_until"],
            ):
                raise ValueError
            if state["remote"] is not None and (
                not isinstance(state["remote"], dict)
                or type(state["remote"].get("current_transactions")) is not int
                or state["remote"]["current_transactions"] < 0
                or type(state["remote"].get("transaction_limit")) is not int
                or state["remote"]["transaction_limit"] < 1
            ):
                raise ValueError
            return state
        except (ValueError, KeyError, TypeError):
            raise RuntimeError("Quota ledger is corrupt; preserve it and investigate") from None

    def _write(self, state: dict[str, Any]) -> None:
        temporary = self.path.with_suffix(".json.part")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(state, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def _usage(self, state: dict[str, Any], now: float) -> int:
        state["events"] = [event for event in state["events"] if event[0] > now - WINDOW]
        local = sum(event[1] for event in state["events"])
        observed = state["last_reconcile"]
        if state["remote"] is not None and observed is not None and now < observed + WINDOW:
            self.budget = min(self.budget, state["remote"]["transaction_limit"])
            since = sum(
                event[1] for event in state["events"] if event[0] >= state["remote_request_start"]
            )
            return max(local, state["remote"]["current_transactions"] + since)
        return local

    def reserve(self, cost: int = 1, *, status_request: bool = False) -> None:
        if not 1 <= cost <= self.budget:
            raise ValueError("Request cost must fit within the quota budget")
        while True:
            with self.lock:
                now = self.clock()
                state = self._read()
                usage = self._usage(state, now)
                if cost > self.budget:
                    raise ValueError("Request cost exceeds the reconciled quota budget")
                last_status = state["last_status_request"]
                gate = last_status + 60 if status_request and last_status is not None else 0
                if now >= max(state["blocked_until"], gate) and usage + cost <= self.budget:
                    state["events"].append([now, cost])
                    if status_request:
                        state["last_status_request"] = now
                    self._write(state)
                    return
                deadlines = [event[0] + WINDOW for event in state["events"]]
                if state["remote"] is not None and state["last_reconcile"] is not None:
                    deadlines.append(state["last_reconcile"] + WINDOW)
                deadlines.append(state["blocked_until"])
                deadlines.append(gate)
                future = [deadline for deadline in deadlines if deadline > now]
                delay = max(0.001, min(future) - now) if future else WINDOW
                self._write(state)
            self.sleep(delay)

    def reconcile(
        self,
        fetch_status: Callable[[], dict[str, Any]],
        *,
        fresh: bool = False,
    ) -> dict[str, Any]:
        with self.lock:
            state = self._read()
            last = state["last_reconcile"]
            if (
                not fresh
                and last is not None
                and self.clock() - last < 60
                and state["remote"] is not None
            ):
                return state["remote"]
        self.reserve(1, status_request=True)
        requested_at = self.clock()
        status = fetch_status()
        limit, used = status.get("transaction_limit"), status.get("current_transactions")
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not isinstance(used, int)
            or isinstance(used, bool)
            or limit <= 0
            or used < 0
            or status.get("transaction_interval") != "10 minutes"
        ):
            raise RuntimeError("Unexpected FIRMS quota status schema")
        self.budget = min(self.budget, limit)
        with self.lock:
            state = self._read()
            previous = state["remote_request_start"]
            if previous is not None and previous > requested_at:
                return state["remote"]
            state["last_reconcile"] = self.clock()
            state["remote_request_start"] = requested_at
            state["remote"] = status
            self._usage(state, self.clock())
            self._write(state)
            return status

    def exhausted(self, retry_after: float = WINDOW) -> None:
        with self.lock:
            state = self._read()
            state["blocked_until"] = max(
                state["blocked_until"],
                self.clock() + max(WINDOW, retry_after),
            )
            self._write(state)
