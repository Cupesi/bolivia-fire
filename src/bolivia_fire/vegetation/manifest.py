from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from filelock import FileLock

from bolivia_fire.vegetation.client import AppEEARSClient, AppEEARSError


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


class UncertainSubmissionError(AppEEARSError):
    pass


class TaskManifest:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]
        except (ValueError, TypeError):
            raise AppEEARSError("Task manifest is malformed; refusing submission") from None

    def append(self, record: dict[str, Any]) -> None:
        rows = self.records() + [record]
        temporary = self.path.with_suffix(".jsonl.part")
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            for row in rows:
                stream.write(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.path)

    def ensure_task(self, client: AppEEARSClient, payload: dict[str, Any]) -> str:
        fingerprint = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        task_name = "bolivia-mod13a2-" + fingerprint[:24]
        with FileLock(str(self.path) + ".lock", timeout=0):
            rows = [row for row in self.records() if row.get("request_sha256") == fingerprint]
            for row in reversed(rows):
                if row.get("task_id"):
                    return row["task_id"]
            matches = [t for t in client.tasks() if t.get("task_name") == task_name]
            if len(matches) > 1:
                raise UncertainSubmissionError(
                    "Multiple matching AppEEARS tasks; manual review needed"
                )
            request = {**payload, "task_name": task_name}
            record = {"request_sha256": fingerprint, "request": request, "recorded_utc": utc_now()}
            if matches:
                task_id = matches[0].get("task_id")
                if not isinstance(task_id, str) or not task_id:
                    raise UncertainSubmissionError("Matching task lacks an id")
            elif rows:
                # A lost POST response cannot be distinguished from a lost POST request.
                raise UncertainSubmissionError(
                    "Prior submission has no confirmed id; refusing to resubmit"
                )
            else:
                self.append({**record, "event": "submission_intent"})
                task_id = client.submit(request)
            self.append({**record, "event": "submitted", "task_id": task_id})
            return task_id
