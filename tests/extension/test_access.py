import json

import pytest
from conftest import write_json

from bolivia_fire.extension.access import finish_opening, open_batch, verify_opening
from bolivia_fire.extension.registration import verify_registration
from bolivia_fire.firms.storage import sha256_file


def synthetic_frozen(paths):
    path = paths.root / "artifacts/models/extension/synthetic.joblib"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"synthetic frozen estimator")
    return {
        "artifact_hashes": {path.relative_to(paths.root).as_posix(): sha256_file(path)},
        "input_hashes": {
            "configs/experiments.yaml": sha256_file(paths.root / "configs/experiments.yaml")
        },
        "runs": {"synthetic": {"status": "evaluated_eligible"}},
    }


def test_synthetic_exploratory_opening_is_durable_before_labels(synthetic_registration):
    paths, _ = synthetic_registration
    registration = verify_registration(paths)
    frozen = synthetic_frozen(paths)
    with pytest.raises(ValueError, match="explicit"):
        open_batch(registration, frozen, scope="test", explicit=False, reason="synthetic")
    opening = open_batch(
        registration,
        frozen,
        scope="test",
        explicit=True,
        reason="synthetic exploratory reopening",
        row_hashes={"synthetic": "abc"},
    )
    log = json.loads((paths.root / "artifacts/evaluation/final_test_log.json").read_text("utf-8"))
    assert log[0]["reopening"] is True
    assert log[0]["state"] == "opened"
    assert log[0]["reason"] == "synthetic exploratory reopening"
    verify_opening(registration, opening, "test")
    finish_opening(registration, opening, "failed", "synthetic scoring failure")
    assert len(json.loads((paths.root / opening.log_path).read_text("utf-8"))) == 2
    with pytest.raises(ValueError, match="closed"):
        verify_opening(registration, opening, "test")


def test_synthetic_confirmation_refuses_missing_gates(synthetic_registration):
    paths, _ = synthetic_registration
    with pytest.raises(ValueError, match="confirmation"):
        open_batch(
            verify_registration(paths),
            synthetic_frozen(paths),
            scope="confirmation",
            explicit=True,
            reason="synthetic confirmation",
        )
    assert not (paths.root / "artifacts/evaluation/extension_confirmation_log.json").exists()


def test_synthetic_confirmation_failure_consumes_single_opening(synthetic_registration):
    paths, _ = synthetic_registration
    audit = paths.root / "artifacts/evaluation/synthetic_confirmation_audit.json"
    evidence = {
        "access_audit": {"untouched": True, "inspected_for_development": False},
        "coverage": {
            "firms_end": "2026-07-31",
            "outages_end": "2026-07-31",
            "chirps_end": "2026-07-19",
        },
        "archive": {
            "cutoff": "2026-07-31T23:59:59Z",
            "all_used_granules_produced_and_archived_by_cutoff": True,
        },
        "frozen_before_label_access": True,
        "input_hashes": {
            "configs/experiments.yaml": sha256_file(paths.root / "configs/experiments.yaml")
        },
    }
    write_json(audit, evidence)
    frozen = synthetic_frozen(paths)
    frozen["input_hashes"][audit.relative_to(paths.root).as_posix()] = sha256_file(audit)
    reg = verify_registration(paths)
    opening = open_batch(
        reg,
        frozen,
        scope="confirmation",
        explicit=True,
        reason="synthetic",
        row_hashes={"synthetic": "synthetic row-key digest"},
        confirmation_evidence=audit.relative_to(paths.root).as_posix(),
    )
    finish_opening(reg, opening, "failed", "synthetic failure after access")
    with pytest.raises(ValueError, match="already"):
        open_batch(
            reg,
            frozen,
            scope="confirmation",
            explicit=True,
            reason="synthetic retry",
            row_hashes={"synthetic": "synthetic row-key digest"},
            confirmation_evidence=audit.relative_to(paths.root).as_posix(),
        )


def test_synthetic_hash_mutation_prevents_test_opening(synthetic_registration):
    paths, _ = synthetic_registration
    frozen = synthetic_frozen(paths)
    (paths.root / "artifacts/models/extension/synthetic.joblib").write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash"):
        open_batch(
            verify_registration(paths), frozen, scope="test", explicit=True, reason="synthetic"
        )
