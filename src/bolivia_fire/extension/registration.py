from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from bolivia_fire.config import Paths
from bolivia_fire.firms.storage import sha256_file

REGISTRATION = "artifacts/evaluation/extension_registration.json"
REGISTERED_FILES = {
    "contract": "configs/extension_vegetation.yaml",
    "document": "docs/extension-vegetation.md",
    "protocol": "configs/protocol.yaml",
    "experiments": "configs/experiments.yaml",
}


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Missing or malformed extension record: {path.name}") from exc


def local_path(paths: Paths, name: str) -> Path:
    candidate = Path(name)
    root = paths.root.resolve()
    result = (root / candidate).resolve()
    if candidate.is_absolute() or not result.is_relative_to(root) or ".env" in result.parts:
        raise ValueError("Extension inputs must use repository-relative, non-secret paths")
    return result


def git_bytes(root: Path, *args: str) -> bytes:
    process = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        check=False,
    )
    if process.returncode:
        raise ValueError("Extension registration requires committed Git proof")
    return process.stdout


@dataclass(frozen=True)
class Registration:
    paths: Paths
    contract: dict[str, Any]
    record: dict[str, Any]
    commit: str
    digest: str
    runs: list[dict[str, Any]]

    def provenance(self) -> dict[str, Any]:
        return {
            "registration_sha256": self.digest,
            "registration_commit": self.commit,
            **{f"{name}_sha256": self.record[f"{name}_sha256"] for name in REGISTERED_FILES},
        }


def verify_registration(paths: Paths) -> Registration:
    record = read_json(paths.root / REGISTRATION)
    if (
        not isinstance(record, dict)
        or record.get("no_extension_labels_or_metrics_accessed") is not True
    ):
        raise ValueError("Extension registration lacks the no-access attestation")
    try:
        timestamp = datetime.fromisoformat(record["registered_at_utc"].replace("Z", "+00:00"))
        if timestamp.utcoffset() != timedelta(0):
            raise ValueError("not UTC")
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError("Extension registration requires a valid UTC timestamp") from exc
    for name, relative in REGISTERED_FILES.items():
        path = local_path(paths, relative)
        if not path.is_file() or sha256_file(path) != record.get(f"{name}_sha256"):
            raise ValueError(f"Registered {name} SHA-256 mismatch")
    commit = git_bytes(paths.root, "rev-parse", "HEAD").decode().strip()
    for relative in (REGISTRATION, *REGISTERED_FILES.values()):
        git_bytes(paths.root, "ls-files", "--error-unmatch", "--", relative)
        if (
            git_bytes(paths.root, "show", f"HEAD:{relative}")
            != (paths.root / relative).read_bytes()
        ):
            raise ValueError(f"Registered file differs from committed HEAD: {relative}")
    contract = yaml.safe_load((paths.root / REGISTERED_FILES["contract"]).read_text("utf-8"))
    runs = []
    for item in contract["run_manifest"]:
        variant = contract["variants"][item["variant"]]
        expanded = {**variant, **item}
        runs.append(expanded)
    planned = record.get("planned_runs")
    if not isinstance(planned, list) or len(planned) != len(runs):
        raise ValueError("Registered run manifest differs from the contract")
    for expected, actual in zip(runs, planned, strict=True):
        if any(expected.get(name) != value for name, value in actual.items()):
            raise ValueError("Registered run features, geography, lag or comparator differ")
        required = {
            "run_id",
            "variant",
            "ordered_features",
            "geography",
            "availability_lag_days",
            "comparator",
            "contextual_comparator",
            "required_gates",
        }
        if not required.issubset(actual):
            raise ValueError("Incomplete registered run identity")
    return Registration(
        paths, contract, record, commit, sha256_file(paths.root / REGISTRATION), runs
    )
