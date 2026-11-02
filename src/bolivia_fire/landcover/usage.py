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
    keys = (
        r"(?:project(?:[_-]?(?:id|number))?|consumer(?:[_-]?(?:id|number))?"
        r"|quota[_-]?project(?:[_-]?(?:id|number))?)"
    )
    result = re.sub(
        rf"(?i)(\b{keys}\s*[:=]\s*)\d{{6,}}(?![\w.])",
        r"\1<project-number>",
        result,
    )
    prefix = rf"\b{keys}(?:\\?[\"'])\s*[:=]\s*"
    result = re.sub(
        rf"(?i)({prefix})(?P<quote>\\?[\"'])\d{{6,}}(?P=quote)",
        lambda match: match[1] + match["quote"] + "<project-number>" + match["quote"],
        result,
    )

    def numeric_identifier(match: re.Match[str]) -> str:
        quote = r"\"" if r"\"" in match[1] else '"'
        return match[1] + quote + "<project-number>" + quote

    result = re.sub(rf"(?i)({prefix})\d{{6,}}(?![\w.])", numeric_identifier, result)
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

    def monitoring_batch_limit(self) -> int:
        return self._monitoring_batch_limit(self._load())

    @staticmethod
    def _monitoring_batch_limit(state: dict[str, Any]) -> int:
        projection = state.get("raster_full_14_year_projection_eecu_hours_1_5x")
        cap = state.get("workstream_safety_cap_eecu_hours")
        if (
            state.get("monitoring_expanded_batches_authorized") is True
            and state.get("raster_pilot_validation_passed") is True
            and state.get("raster_pilot_authorized") is False
            and cap in (60, 100)
            and isinstance(projection, (int, float))
            and math.isfinite(projection)
            and 0 < projection <= cap
        ):
            if cap == 100 and state.get("monitoring_two_year_batches_allowed") is True:
                return 360
            if state.get("monitoring_year_sized_batches_allowed") is True:
                return 180
            if state.get("monitoring_twenty_tile_batches_allowed") is True:
                return 20
        return 10

    def reserve_monitoring(self, batch: str, tile_count: int, remaining_tiles: int) -> float:
        with FileLock(str(self.path) + ".lock", timeout=0):
            state = self._load()
            rates = [
                item["monitoring_delta_eecu_seconds"] / 3600 / item["tile_count"]
                for item in state.get("batches", [])
                if item["status"] == "monitoring_reconciled"
                and item.get("tile_count", 0) > 0
                and item.get("rate_sample_complete", True)
            ] or [state.get("monitoring_seed_per_tile_eecu_hours")]
            if (
                state.get("accounting_primary") != "cloud_monitoring"
                or not 0 < tile_count <= self._monitoring_batch_limit(state)
                or remaining_tiles < tile_count
                or any(
                    not isinstance(rate, (int, float)) or not math.isfinite(rate) or rate <= 0
                    for rate in rates
                )
            ):
                self._pause(state, "Measured Monitoring per-tile usage is unavailable")
            rate = max(rates)
            estimate = rate * tile_count * 1.5
            projected = rate * remaining_tiles * 1.5
            self._reserve_locked(
                state,
                batch,
                estimate,
                remaining_plan_eecu_hours=(
                    None if state.get("raster_pilot_authorized") is True else projected
                ),
            )
            entry = next(item for item in state["batches"] if item["batch"] == batch)
            entry.update(
                tile_count=tile_count,
                planned_tile_count=tile_count,
                remaining_tiles=remaining_tiles,
                measured_per_tile_eecu_hours=rate,
                safety_factor=1.5,
                projected_remaining_eecu_hours_1_5x=projected,
                monitoring_before_eecu_seconds=state["project_month_eecu_hours"] * 3600,
            )
            atomic_json(self.path, state)
            return estimate

    def reconcile_monitoring(
        self,
        batch: str,
        total_seconds: float,
        observed_at: str,
        *,
        submitted_tile_count: int | None = None,
        completed_tile_count: int | None = None,
    ) -> None:
        with FileLock(str(self.path) + ".lock", timeout=0):
            state = self._load()
            entry = next(
                (
                    item
                    for item in state.get("batches", [])
                    if item["batch"] == batch and item["status"] == "reserved"
                ),
                None,
            )
            if entry is None:
                self._pause(state, "No unreconciled Monitoring reservation for this batch")
            planned = entry["tile_count"]
            attempted = planned if submitted_tile_count is None else submitted_tile_count
            completed = attempted if completed_tile_count is None else completed_tile_count
            if (
                not isinstance(attempted, int)
                or not isinstance(completed, int)
                or not 0 <= completed <= attempted <= planned * 2
            ):
                self._pause(state, "Actual submitted tile count is invalid")
            entry.update(
                planned_tile_count=planned,
                tile_count=attempted,
                attempted_tiles=attempted,
                completed_tiles=completed,
                rate_sample_complete=attempted == completed == planned,
            )
            baseline = entry["monitoring_before_eecu_seconds"]
            if not math.isfinite(total_seconds) or total_seconds <= baseline:
                self._pause(state, "Monitoring batch usage is unknown or has not increased")
            delta = total_seconds - baseline
            entry.update(
                status="monitoring_reconciled",
                monitoring_after_eecu_seconds=total_seconds,
                monitoring_delta_eecu_seconds=delta,
                reconciled_at_utc=observed_at,
                partial_batch_overhead_eecu_seconds=(0 if entry["rate_sample_complete"] else delta),
            )
            state.update(
                project_month_eecu_hours=total_seconds / 3600,
                workstream_eecu_hours=total_seconds / 3600,
                monitoring_reconciled_eecu_hours=total_seconds / 3600,
                usage_observed_at_utc=observed_at,
            )
            atomic_json(self.path, state)
            if (
                total_seconds / 3600 + state.get("unreconciled_overhead_reserve_eecu_hours", 0)
                > state["workstream_safety_cap_eecu_hours"]
            ):
                self._pause(state, "Measured usage reached the workstream safety cap")

    def reserve(
        self,
        batch: str,
        estimated_eecu_hours: float,
        *,
        remaining_plan_eecu_hours: float | None = None,
    ) -> None:
        with FileLock(str(self.path) + ".lock", timeout=0):
            record = self._load()
            self._reserve_locked(
                record,
                batch,
                estimated_eecu_hours,
                remaining_plan_eecu_hours=remaining_plan_eecu_hours,
            )
            atomic_json(self.path, record)

    def _reserve_locked(
        self,
        record: dict[str, Any],
        batch: str,
        estimated_eecu_hours: float,
        *,
        remaining_plan_eecu_hours: float | None = None,
    ) -> None:
        if remaining_plan_eecu_hours is not None:
            record["full_aggregation_estimate_eecu_hours"] = remaining_plan_eecu_hours
        if not math.isfinite(estimated_eecu_hours) or estimated_eecu_hours <= 0:
            self._pause(record, "A finite positive EECU reservation is required")
        if record.get("status") != "active" or record.get("submissions_authorized") is not True:
            self._pause(record, "Private usage ledger does not authorize submissions")
        try:
            timestamp = record["usage_observed_at_utc"]
            if record.get("accounting_primary") != "cloud_monitoring":
                timestamp = record.get("profile_usage_observed_at_utc", timestamp)
            observed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
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
        if not isinstance(overhead, (int, float)) or not math.isfinite(overhead) or overhead < 0:
            self._pause(record, "Diagnostic overhead reserve is unknown")
        cap = record.get("workstream_safety_cap_eecu_hours")
        if record.get("project_monthly_budget_eecu_hours") != 150 or cap not in (30, 60, 100):
            self._pause(record, "Usage budgets do not match the zero-cost contract")
        if values[0] + overhead + estimated_eecu_hours > 150:
            self._pause(record, "Monthly project EECU budget would be exceeded")
        if values[1] + overhead + estimated_eecu_hours > cap:
            self._pause(record, "Workstream EECU safety cap would be exceeded")
        planned = record.get("full_aggregation_estimate_eecu_hours")
        if planned is not None and (
            not isinstance(planned, (int, float))
            or not math.isfinite(planned)
            or planned < 0
            or values[1] + overhead + planned > cap
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
