from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from filelock import FileLock

from bolivia_fire.evaluation.artifacts import atomic_json
from bolivia_fire.extension.registration import Registration, local_path, read_json
from bolivia_fire.firms.storage import sha256_file


def now() -> str:
    return datetime.now(UTC).isoformat()


def check_hashes(registration: Registration, hashes: dict[str, str]) -> None:
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError("Nonempty frozen input hashes are required")
    for name, expected in hashes.items():
        path = local_path(registration.paths, name)
        if not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f"Frozen extension input hash mismatch: {name}")


@dataclass(frozen=True)
class Opening:
    identifier: str
    scope: str
    log_path: str
    registration_sha256: str


def confirmation_gates(
    registration: Registration, frozen: dict[str, Any], evidence_path: str | None
) -> dict[str, Any]:
    if not evidence_path or evidence_path not in frozen["input_hashes"]:
        raise ValueError("Fresh confirmation requires frozen confirmation evidence")
    evidence = read_json(local_path(registration.paths, evidence_path))
    try:
        audit, coverage, archive = (
            evidence[key] for key in ("access_audit", "coverage", "archive")
        )
        passed = (
            audit["untouched"] is True
            and audit["inspected_for_development"] is False
            and date.fromisoformat(coverage["firms_end"]) >= date(2026, 7, 31)
            and date.fromisoformat(coverage["outages_end"]) >= date(2026, 7, 31)
            and date.fromisoformat(coverage["chirps_end"]) >= date(2026, 7, 19)
            and archive["cutoff"] == "2026-07-31T23:59:59Z"
            and archive["all_used_granules_produced_and_archived_by_cutoff"] is True
            and evidence["frozen_before_label_access"] is True
        )
        check_hashes(registration, evidence["input_hashes"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Incomplete fresh confirmation evidence") from exc
    if not passed:
        raise ValueError("Fresh confirmation unavailable: contract prerequisite failed")
    return evidence


def open_batch(
    registration: Registration,
    frozen: dict[str, Any],
    *,
    scope: str,
    explicit: bool,
    reason: str | None,
    row_hashes: dict[str, str] | None = None,
    confirmation_evidence: str | None = None,
) -> Opening:
    if scope not in {"test", "confirmation"}:
        raise ValueError("Unknown extension scoring window")
    if not explicit:
        raise ValueError("Extension scoring requires an explicit open flag")
    if not reason or not reason.strip():
        raise ValueError("Extension scoring requires a nonempty reopening reason")
    check_hashes(registration, frozen["artifact_hashes"])
    check_hashes(registration, frozen["input_hashes"])
    evidence = (
        confirmation_gates(registration, frozen, confirmation_evidence)
        if (scope == "confirmation")
        else None
    )
    planned = [
        name
        for name, entry in frozen["runs"].items()
        if entry.get("status") == "evaluated_eligible"
        and (
            scope != "confirmation"
            or entry.get("run", {}).get("geography", "temporal") == "temporal"
        )
    ]
    if not row_hashes or any(name not in row_hashes for name in planned):
        raise ValueError("Every planned scoring run requires a pre-label row-key hash")
    relative = (
        "artifacts/evaluation/final_test_log.json"
        if scope == "test"
        else "artifacts/evaluation/extension_confirmation_log.json"
    )
    destination = registration.paths.root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(destination.with_suffix(".lock"))):
        entries = read_json(destination) if destination.exists() else []
        if not isinstance(entries, list):
            raise ValueError("Malformed extension access log")
        if scope == "confirmation" and any(entry.get("state") == "opened" for entry in entries):
            raise ValueError("Fresh confirmation already opened; the opening is consumed")
        identifier = uuid.uuid4().hex
        entries.append(
            {
                "type": f"extension_{scope}",
                "state": "opened",
                "opening_id": identifier,
                "timestamp_utc": now(),
                "reason": reason.strip(),
                "reopening": scope == "test",
                "classification": "exploratory"
                if scope == "test"
                else "supplementary_confirmation",
                "planned_runs": planned,
                "row_key_hashes_before_labels": row_hashes or {},
                "artifact_hashes": frozen["artifact_hashes"],
                "input_hashes": frozen["input_hashes"],
                "selection_sha256": frozen.get("selection_sha256"),
                "confirmation_evidence": evidence,
                **registration.provenance(),
            }
        )
        atomic_json(destination, entries)
    return Opening(identifier, scope, relative, registration.digest)


def verify_opening(registration: Registration, opening: Opening | None, scope: str) -> None:
    if (
        opening is None
        or opening.scope != scope
        or opening.registration_sha256 != registration.digest
    ):
        raise ValueError("Label access requires a matching durable extension opening")
    entries = read_json(registration.paths.root / opening.log_path)
    attempt = [entry for entry in entries if entry.get("opening_id") == opening.identifier]
    if not attempt or attempt[0].get("state") != "opened" or len(attempt) != 1:
        raise ValueError("Extension opening is missing or closed")
    check_hashes(registration, attempt[0]["artifact_hashes"])
    check_hashes(registration, attempt[0]["input_hashes"])


def finish_opening(
    registration: Registration, opening: Opening, state: str, reason: str | None = None
) -> None:
    if state not in {"completed", "failed"}:
        raise ValueError("Unknown extension opening outcome")
    destination = registration.paths.root / opening.log_path
    with FileLock(str(destination.with_suffix(".lock"))):
        entries = read_json(destination)
        entries.append(
            {
                "type": f"extension_{opening.scope}",
                "opening_id": opening.identifier,
                "state": state,
                "reason": reason,
                "timestamp_utc": now(),
            }
        )
        atomic_json(destination, entries)
