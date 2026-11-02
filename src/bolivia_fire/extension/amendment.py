from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from bolivia_fire.config import Paths
from bolivia_fire.evaluation.artifacts import atomic_json
from bolivia_fire.extension import registration as base
from bolivia_fire.firms.storage import sha256_file

AMENDMENT_CONTRACT = "configs/extension_amendment_landcover.yaml"
AMENDMENT_DOCUMENT = "docs/extension-landcover-amendment.md"
AMENDMENT_REGISTRATION = "artifacts/evaluation/extension_amendment_registration.json"
AVAILABILITY = "artifacts/evaluation/extension_availability.json"


def _metric_paths(registration: base.Registration) -> set[Path]:
    directory = registration.paths.root / "artifacts/evaluation"
    patterns = (
        "extension_fit_*.json",
        "extension_checkpoint_*.json",
        "extension_execution_report.json",
        "extension_selection*.json",
        "extension_test_*.json",
        "extension_confirmation_*.json",
    )
    return {
        path
        for pattern in patterns
        for path in directory.rglob(pattern)
        if path.name != "extension_confirmation_evidence.json"
        and path.name != "extension_confirmation_guard.json"
        and path.name != "extension_confirmation_log.json"
    }


def metric_artifacts(registration: base.Registration) -> dict[str, str]:
    directory = registration.paths.root / "artifacts/evaluation"
    return {
        path.relative_to(registration.paths.root).as_posix(): sha256_file(path)
        for path in sorted(_metric_paths(registration))
        if path.relative_to(directory).parts[0] != "amendments"
    }


def _landcover_exposed(value: Any, identities: set[str]) -> bool:
    if isinstance(value, list):
        return any(_landcover_exposed(entry, identities) for entry in value)
    if not isinstance(value, dict):
        return False
    if value.get("state") == "opened" and identities.intersection(value.get("planned_runs", [])):
        return True
    metric_fields = {
        "completed_configurations",
        "results",
        "search",
        "validation",
        "calibrated",
        "average_precision",
        "val_select_uncalibrated_ap",
        "positives",
        "bootstrap",
    }

    def exposed_entry(entry: dict[str, Any]) -> bool:
        return bool(entry) and (
            entry.get("status") != "not_evaluated" or bool(metric_fields.intersection(entry))
        )

    run = value.get("run", {})
    if (
        value.get("run_id") in identities
        or isinstance(run, dict)
        and run.get("run_id") in identities
    ) and exposed_entry(value):
        return True
    for key, entry in value.items():
        if key in identities | {"H4", "H5"} and isinstance(entry, dict) and exposed_entry(entry):
            return True
        # A not-evaluated placeholder may contain the registered run definition.
        if key != "run" and _landcover_exposed(entry, identities):
            return True
    return False


def _committed(registration: base.Registration, name: str, commit: str = "HEAD") -> None:
    base.git_bytes(registration.paths.root, "ls-files", "--error-unmatch", "--", name)
    if (
        base.git_bytes(registration.paths.root, "show", f"{commit}:{name}")
        != base.local_path(registration.paths, name).read_bytes()
    ):
        raise ValueError(f"Amendment file differs from committed proof: {name}")


def _contract(registration: base.Registration) -> dict[str, Any]:
    contract = yaml.safe_load(
        base.local_path(registration.paths, AMENDMENT_CONTRACT).read_text("utf-8")
    )
    expected_scope = {
        "hypotheses": ["H4", "H5"],
        "run_manifest": "inherit_exactly_from_base",
        "ordered_features": "unchanged",
        "comparators": "unchanged",
        "search_seed_budget": "unchanged",
        "selection_and_calibration": "unchanged",
        "identical_rows": "same_comparators_separate_H4_H5_intersections_preserving_frozen_H3",
        "bootstrap_and_decision_rules": "unchanged",
        "exploratory_test_handling": "unchanged",
        "confirmation_gates": "unchanged",
        "H3": "preserve_all_runs_scores_decisions_and_frozen_rows_without_recomputation",
    }
    if (
        not isinstance(contract, dict)
        or contract.get("scope") != expected_scope
        or contract.get("amendment_version") != "1.0"
        or not re.fullmatch(r"[a-z0-9-]+", contract.get("amendment_id", ""))
        or contract.get("disclosure", {}).get("h3_validation_metrics_previously_seen") is not True
        or contract.get("disclosure", {}).get("no_landcover_or_combined_labels_or_metrics_accessed")
        is not True
    ):
        raise ValueError("Invalid land-cover amendment scope or disclosure")
    try:
        datetime.strptime(contract["date"], "%Y-%m-%d")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Amendment requires an explicit date") from exc
    for name in ("contract", "document"):
        if (
            contract.get("base_registration", {}).get(f"{name}_sha256")
            != registration.record[f"{name}_sha256"]
        ):
            raise ValueError("Amendment base registration hash mismatch")
    return contract


def register_amendment(paths: Paths) -> dict[str, Any]:
    registration = base.verify_registration(paths)
    destination = paths.root / AMENDMENT_REGISTRATION
    if destination.exists():
        raise ValueError("Amendment registration already exists; retain it unchanged")
    contract = _contract(registration)
    for name in (AMENDMENT_CONTRACT, AMENDMENT_DOCUMENT):
        _committed(registration, name)
    prior = metric_artifacts(registration)
    land_ids = {run["run_id"] for run in registration.runs if run["hypothesis"] in {"H4", "H5"}}
    directory = paths.root / "artifacts/evaluation"
    exposure_paths = _metric_paths(registration)
    for name in ("final_test_log.json", "extension_confirmation_log.json"):
        exposure_paths.update(directory.rglob(name))
    for path in sorted(exposure_paths):
        if _landcover_exposed(base.read_json(path), land_ids):
            raise ValueError("Cannot register after land-cover or combined metric exposure")
    if (paths.root / AVAILABILITY).exists():
        prior[AVAILABILITY] = sha256_file(paths.root / AVAILABILITY)
    record = {
        "amendment_id": contract["amendment_id"],
        "amendment_version": contract["amendment_version"],
        "registered_at_utc": datetime.now(UTC).isoformat(),
        "amendment_commit": registration.commit,
        "amendment_contract_sha256": sha256_file(paths.root / AMENDMENT_CONTRACT),
        "amendment_document_sha256": sha256_file(paths.root / AMENDMENT_DOCUMENT),
        "base_registration_sha256": registration.digest,
        "base_contract_sha256": registration.record["contract_sha256"],
        "base_document_sha256": registration.record["document_sha256"],
        "h3_validation_metrics_previously_seen": True,
        "no_landcover_or_combined_labels_or_metrics_accessed": True,
        "authorized_runs": [
            run["run_id"] for run in registration.runs if run["run_id"] in land_ids
        ],
        "prior_artifact_hashes": prior,
    }
    atomic_json(destination, record)
    return record


def verify_amendment(registration: base.Registration) -> dict[str, Any]:
    paths = registration.paths
    contract = _contract(registration)
    record = base.read_json(paths.root / AMENDMENT_REGISTRATION)
    required = {
        "amendment_id": contract["amendment_id"],
        "amendment_version": contract["amendment_version"],
        "base_registration_sha256": registration.digest,
        "base_contract_sha256": registration.record["contract_sha256"],
        "base_document_sha256": registration.record["document_sha256"],
        "h3_validation_metrics_previously_seen": True,
        "no_landcover_or_combined_labels_or_metrics_accessed": True,
        "authorized_runs": [
            run["run_id"] for run in registration.runs if run["hypothesis"] in {"H4", "H5"}
        ],
    }
    if not isinstance(record, dict) or any(record.get(k) != v for k, v in required.items()):
        raise ValueError("Amendment registration chain mismatch")
    try:
        timestamp = datetime.fromisoformat(record["registered_at_utc"].replace("Z", "+00:00"))
        if timestamp.utcoffset() != timedelta(0) or timestamp.date().isoformat() < contract["date"]:
            raise ValueError("Invalid date")
        if not re.fullmatch(r"[0-9a-f]{40,64}", record["amendment_commit"]):
            raise ValueError("Invalid commit")
    except (KeyError, AttributeError, TypeError, ValueError) as exc:
        raise ValueError("Amendment requires a UTC date and commit") from exc
    for kind, name in (("contract", AMENDMENT_CONTRACT), ("document", AMENDMENT_DOCUMENT)):
        if sha256_file(paths.root / name) != record.get(f"amendment_{kind}_sha256"):
            raise ValueError("Amendment SHA-256 mismatch")
        _committed(registration, name, record["amendment_commit"])
    _committed(registration, AMENDMENT_REGISTRATION)
    prior = record.get("prior_artifact_hashes")
    if not isinstance(prior, dict):
        raise ValueError("Amendment lacks prior metric preservation evidence")
    for name, digest in prior.items():
        path = base.local_path(paths, name)
        if not path.is_file() or sha256_file(path) != digest:
            raise ValueError("Prior extension metric artifact changed after amendment")
    if any(prior.get(name) != digest for name, digest in metric_artifacts(registration).items()):
        raise ValueError("Prior extension metric set changed after amendment")
    return {**record, "registration_sha256": sha256_file(paths.root / AMENDMENT_REGISTRATION)}


def authorize_availability(
    registration: base.Registration, requested: set[str]
) -> dict[str, Any] | None:
    promoted = {
        run["run_id"]
        for run in registration.runs
        if run["status"] == "not_evaluated" and run["run_id"] in requested
    }
    frozen_path = registration.paths.root / AVAILABILITY
    if frozen_path.exists():
        frozen = base.read_json(frozen_path)
        if frozen.get("registration_sha256") != registration.digest:
            raise ValueError("Availability belongs to another registration")
        changed = requested ^ set(frozen["available_runs"])
    else:
        changed = set()
    if not promoted and not changed:
        return None
    if not (registration.paths.root / AMENDMENT_REGISTRATION).exists():
        raise ValueError("Availability activation requires a committed land-cover amendment")
    record = verify_amendment(registration)
    if (promoted | changed) - set(record["authorized_runs"]):
        raise ValueError("Availability change lies outside the committed amendment")
    return record


def amendment_provenance(record: dict[str, Any] | None) -> dict[str, Any]:
    if record is None:
        return {}
    return {
        "amendment_registration_sha256": record["registration_sha256"],
        **{
            key: record[key]
            for key in (
                "amendment_id",
                "amendment_commit",
                "amendment_contract_sha256",
                "amendment_document_sha256",
                "prior_artifact_hashes",
            )
        },
    }


def preserved_h3(registration: base.Registration, record: dict[str, Any]) -> dict[str, Any]:
    evidence = {}
    for name, digest in record["prior_artifact_hashes"].items():
        value = base.read_json(base.local_path(registration.paths, name))
        runs = {
            k: v
            for k, v in value.get("runs", {}).items()
            if "vegetation" in k and "land_cover" not in k
        }
        if runs or value.get("run", {}).get("hypothesis") == "H3":
            evidence[name] = {"sha256": digest, "record": value}
    return evidence
