from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from filelock import FileLock


class UsagePaused(RuntimeError):
    pass


@dataclass(frozen=True)
class ProfileCost:
    reported_eecu_seconds: float
    upper_eecu_seconds: float


def parse_profile(profile: str) -> ProfileCost:
    lines = profile.splitlines()
    if not lines or "EECU" not in lines[0] or "Description" not in lines[0]:
        raise UsagePaused("EECU profile is unavailable")
    values: list[Decimal] = []
    uncertainty = Decimal(0)
    for line in lines[1:]:
        match = re.match(r"^\s*(\d+(?:\.\d+)?)([kM]?)\s+", line)
        if match:
            number, suffix = match.groups()
            multiplier = {"": Decimal(1), "k": Decimal(1000), "M": Decimal(1000000)}[suffix]
            value = Decimal(number)
            values.append(value * multiplier)
            uncertainty += Decimal(1).scaleb(value.as_tuple().exponent) * multiplier
        elif line.strip() and not line.lstrip().startswith("-"):
            raise UsagePaused("Unrecognized EECU profile row")
    if not values:
        raise UsagePaused("EECU profile contains no measured cost")
    total = sum(values)
    return ProfileCost(float(total), float(total + uncertainty))


def redact_project(text: str, project: str) -> str:
    result = text.replace(project, "<configured-project>") if project else text
    result = re.sub(r"projects/\d{6,}", "projects/<project-number>", result)
    result = re.sub(
        r"(?i)([?&](?:access_token|map_key|key|token)=)[^&\s\"']+", r"\1<redacted>", result
    )
    return re.sub(r"(?i)(bearer\s+)\S+", r"\1<redacted>", result)


def atomic_json(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(record, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


class UsageLedger:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise UsagePaused("Private usage ledger is unavailable") from exc

    def _pause(self, record: dict[str, Any], reason: str) -> None:
        record.update(status="paused", submissions_authorized=False, pause_reason=reason)
        atomic_json(self.path, record)
        raise UsagePaused(reason)

    def reserve(self, batch: str, estimated_eecu_hours: float) -> None:
        with FileLock(str(self.path) + ".lock", timeout=0):
            record = self._load()
            if not math.isfinite(estimated_eecu_hours) or estimated_eecu_hours <= 0:
                self._pause(record, "A finite positive EECU reservation is required")
            if record.get("status") != "active" or record.get("submissions_authorized") is not True:
                self._pause(record, "Private usage ledger does not authorize submissions")
            try:
                observed = datetime.fromisoformat(
                    record.get(
                        "profile_usage_observed_at_utc", record["usage_observed_at_utc"]
                    ).replace("Z", "+00:00")
                )
                age = (datetime.now(UTC) - observed).total_seconds()
            except (KeyError, TypeError, ValueError):
                self._pause(record, "Project-month usage observation is unavailable")
            if not 0 <= age <= 3600:
                self._pause(record, "Project-month usage observation is stale")
            values = [record.get("project_month_eecu_hours"), record.get("workstream_eecu_hours")]
            if any(
                not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
                for value in values
            ):
                self._pause(record, "Project or workstream EECU usage is unknown")
            if any(item["status"] == "reserved" for item in record.get("batches", [])):
                self._pause(record, "An earlier request has unreconciled EECU usage")
            overhead = record.get("unreconciled_overhead_reserve_eecu_hours", 0)
            if (
                not isinstance(overhead, (int, float))
                or not math.isfinite(overhead)
                or overhead < 0
            ):
                self._pause(record, "Diagnostic overhead reserve is unknown")
            if (
                record.get("project_monthly_budget_eecu_hours") != 150
                or record.get("workstream_safety_cap_eecu_hours") != 30
            ):
                self._pause(record, "Usage budgets do not match the zero-cost contract")
            if values[0] + overhead + estimated_eecu_hours > 150:
                self._pause(record, "Monthly project EECU budget would be exceeded")
            if values[1] + overhead + estimated_eecu_hours > 30:
                self._pause(record, "Workstream EECU safety cap would be exceeded")
            planned = record.get("full_aggregation_estimate_eecu_hours")
            if planned is not None and (
                not isinstance(planned, (int, float))
                or not math.isfinite(planned)
                or planned < 0
                or values[1] + overhead + planned > 30
            ):
                self._pause(record, "Full extraction plan exceeds the workstream EECU safety cap")
            if any(item["batch"] == batch for item in record.get("batches", [])):
                self._pause(record, "Batch already recorded; refusing duplicate submission")
            record.setdefault("batches", []).append(
                {
                    "batch": batch,
                    "status": "reserved",
                    "reserved_eecu_hours": estimated_eecu_hours,
                    "submitted_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
                }
            )
            atomic_json(self.path, record)

    def reconcile(
        self,
        batch: str,
        *,
        reported_eecu_seconds: float | None,
        upper_eecu_seconds: float | None,
    ) -> None:
        with FileLock(str(self.path) + ".lock", timeout=0):
            record = self._load()
            entry = next(
                (
                    item
                    for item in record.get("batches", [])
                    if item["batch"] == batch and item["status"] == "reserved"
                ),
                None,
            )
            if entry is None:
                self._pause(record, "No unreconciled reservation for this batch")
            values = [reported_eecu_seconds, upper_eecu_seconds]
            if any(value is None or not math.isfinite(value) or value < 0 for value in values):
                record["last_known_project_month_eecu_hours"] = record["project_month_eecu_hours"]
                record["last_known_workstream_eecu_hours"] = record["workstream_eecu_hours"]
                record["usage_unknown_batch"] = batch
                record["project_month_eecu_hours"] = record["workstream_eecu_hours"] = None
                self._pause(record, "Request EECU usage could not be determined")
            if upper_eecu_seconds < reported_eecu_seconds:
                self._pause(record, "EECU upper bound is below the reported measurement")
            record["project_month_eecu_hours"] += upper_eecu_seconds / 3600
            record["workstream_eecu_hours"] += upper_eecu_seconds / 3600
            entry.update(
                status="measured",
                reported_eecu_seconds=reported_eecu_seconds,
                upper_eecu_seconds=upper_eecu_seconds,
            )
            record["profile_usage_observed_at_utc"] = datetime.now(UTC).isoformat(
                timespec="seconds"
            )
            record["profile_usage_accounting"] = (
                "Retained project-month baseline plus every reconciled request upper bound; "
                "diagnostic overhead reserve remains separate"
            )
            atomic_json(self.path, record)
            if upper_eecu_seconds / 3600 > entry["reserved_eecu_hours"]:
                self._pause(record, "Measured EECU usage exceeded its conservative reservation")
