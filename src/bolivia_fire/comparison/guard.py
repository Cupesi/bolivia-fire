from __future__ import annotations

import hashlib
import json
from typing import Any

from filelock import FileLock

from bolivia_fire.comparison.runtime import check_hashes, input_hashes, now, read_json
from bolivia_fire.config import Paths
from bolivia_fire.evaluation.artifacts import atomic_json
from bolivia_fire.firms.storage import sha256_file


def freeze_selection(paths: Paths, payload: dict[str, Any]) -> dict[str, Any]:
    destination = paths.root / "artifacts/evaluation/comparison_selection.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(destination.with_suffix(".lock"))):
        if destination.exists():
            raise ValueError("Comparison selection already exists and is frozen")
        configs = {
            f"configs/{name}.yaml": sha256_file(paths.root / f"configs/{name}.yaml")
            for name in ("protocol", "experiments", "comparison")
        }
        entry = {**payload, "frozen_utc": now(), "config_hashes": configs}
        check_hashes(paths, entry["artifact_hashes"])
        check_hashes(paths, entry["input_hashes"])
        atomic_json(destination, entry)
        atomic_json(
            destination.with_name("comparison_selection_sha256.json"),
            {
                "file": destination.relative_to(paths.root).as_posix(),
                "sha256": sha256_file(destination),
                "frozen_utc": entry["frozen_utc"],
            },
        )
        return entry


def verify_selection(paths: Paths) -> tuple[dict[str, Any], str]:
    destination = paths.root / "artifacts/evaluation/comparison_selection.json"
    sidecar = destination.with_name("comparison_selection_sha256.json")
    if not destination.is_file() or not sidecar.is_file():
        raise ValueError("Freeze comparison selection before any test scoring")
    digest = sha256_file(destination)
    if read_json(sidecar)["sha256"] != digest:
        raise ValueError("Comparison selection changed: sha256 mismatch")
    entry = read_json(destination)
    for key in ("config_hashes", "input_hashes", "artifact_hashes", "source_hashes"):
        check_hashes(paths, entry.get(key, {}))
    return entry, digest


def open_test(
    paths: Paths,
    *,
    post_hoc_test: bool,
    reason: str | None,
    reopen_reason: str | None = None,
) -> dict[str, Any]:
    if not post_hoc_test:
        raise ValueError("Exploratory test scoring requires --post-hoc-test")
    if reason is None or not reason.strip():
        raise ValueError("Exploratory test scoring requires a nonempty --reason")
    destination = paths.root / "artifacts/evaluation/final_test_log.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(destination.with_suffix(".lock"))):
        selection, digest = verify_selection(paths)
        entries = read_json(destination) if destination.exists() else []
        repeated = any(entry.get("type") == "post_hoc_comparison" for entry in entries)
        if repeated and (reopen_reason is None or not reopen_reason.strip()):
            raise ValueError(
                "Comparison test already opened; a second run requires --reopen-reason"
            )
        hashes = input_hashes(paths, include_test=True)
        panel = {
            name: value
            for name, value in hashes.items()
            if name.startswith("datasets/processed/panel/")
        }
        entry = {
            "type": "post_hoc_comparison",
            "timestamp_utc": now(),
            "reason": reason.strip(),
            "comparison_selection_sha256": digest,
            "model_hashes": {
                name: value
                for name, value in selection["artifact_hashes"].items()
                if name.startswith("artifacts/models/")
            },
            "frozen_artifact_hashes": selection["artifact_hashes"],
            "source_hashes": selection.get("source_hashes", {}),
            "dataset_snapshot_hashes": hashes,
            "panel_snapshot_sha256": hashlib.sha256(
                json.dumps(panel, sort_keys=True).encode()
            ).hexdigest(),
            "config_sha256": {
                name: hashes[f"configs/{name}.yaml"]
                for name in ("protocol", "experiments", "comparison")
            },
            "reopening": repeated,
            "reopen_reason": reopen_reason.strip() if reopen_reason else None,
            "prior_primary_openings": sum(
                entry.get("type") != "post_hoc_comparison" for entry in entries
            ),
        }
        entries.append(entry)
        atomic_json(destination, entries)
        return entry
