from __future__ import annotations

import json
import shutil

import pytest
from conftest import write_json

from bolivia_fire.config import REPO_ROOT
from bolivia_fire.extension.registration import verify_registration


@pytest.fixture
def synthetic_amendment_files(synthetic_registration):
    paths, blobs = synthetic_registration
    for name in (
        "configs/extension_amendment_landcover.yaml",
        "docs/extension-landcover-amendment.md",
    ):
        target = paths.root / name
        shutil.copyfile(REPO_ROOT / name, target)
        blobs[name] = target.read_bytes()
    return paths, blobs


@pytest.fixture
def synthetic_amendment(synthetic_amendment_files):
    paths, blobs = synthetic_amendment_files
    prior = "artifacts/evaluation/extension_execution_report.json"
    write_json(
        paths.root / prior,
        {
            "registration_sha256": verify_registration(paths).digest,
            "runs": {
                "retrospective_vegetation_lag16": {
                    "status": "evaluated_eligible",
                    "average_precision": 0.4,
                    "row_key_sha256": "synthetic frozen H3 rows",
                },
                "retrospective_land_cover": {"status": "not_evaluated"},
            },
            "primary_hypotheses": {"H3": {"status": "inconclusive"}},
        },
    )
    return paths, blobs


@pytest.mark.parametrize(
    "identity",
    [
        "retrospective_land_cover",
        "retrospective_land_cover_geo",
        "retrospective_vegetation_land_cover_lag16",
        "retrospective_vegetation_land_cover_lag32",
        "retrospective_vegetation_land_cover_geo_lag16",
        "retrospective_vegetation_land_cover_geo_lag32",
    ],
)
@pytest.mark.parametrize("namespace", ["", "extension_archive_synthetic", "amendments/synthetic"])
def test_synthetic_checkpoint_alone_refuses_amendment_registration(
    synthetic_amendment_files, identity, namespace
):
    from bolivia_fire.extension.amendment import AMENDMENT_REGISTRATION, register_amendment

    paths, _ = synthetic_amendment_files
    directory = paths.root / "artifacts/evaluation" / namespace
    write_json(
        directory / f"extension_checkpoint_{identity}.json",
        {
            "run_id": identity,
            "stage": "search",
            "completed_configurations": 1,
            "results": [{"name": "lightgbm_00", "val_select_uncalibrated_ap": 0.0}],
        },
    )
    with pytest.raises(ValueError, match="metric exposure"):
        register_amendment(paths)
    assert not (paths.root / AMENDMENT_REGISTRATION).exists()


@pytest.mark.parametrize(
    "filename,record",
    [
        (
            "extension_fit_synthetic.json",
            {"run": {"run_id": "retrospective_land_cover"}, "validation": {"rows": 8}},
        ),
        (
            "extension_selection.json",
            {"runs": {"retrospective_land_cover": {"status": "evaluated_eligible"}}},
        ),
        (
            "extension_execution_report.json",
            {"runs": {"retrospective_land_cover": {"status": "completed_validation"}}},
        ),
        (
            "extension_test_synthetic.json",
            {"runs": {"retrospective_land_cover": {"calibrated": {"average_precision": 0.4}}}},
        ),
        (
            "extension_confirmation_synthetic.json",
            {"runs": {"retrospective_land_cover": {"rows": 8, "positives": 4}}},
        ),
        (
            "extension_selection.json",
            {"hypotheses": {"H5": {"status": "inconclusive"}}},
        ),
        (
            "extension_test_synthetic.json",
            {"lag_sensitivity": {"retrospective_vegetation_land_cover_lag16": {"rows": 8}}},
        ),
        (
            "final_test_log.json",
            [{"state": "opened", "planned_runs": ["retrospective_land_cover"]}],
        ),
        (
            "extension_confirmation_log.json",
            [{"state": "opened", "planned_runs": ["retrospective_land_cover"]}],
        ),
    ],
)
def test_synthetic_durable_landcover_exposure_refuses_registration(
    synthetic_amendment_files, filename, record
):
    from bolivia_fire.extension.amendment import AMENDMENT_REGISTRATION, register_amendment

    paths, _ = synthetic_amendment_files
    write_json(paths.root / "artifacts/evaluation" / filename, record)
    with pytest.raises(ValueError, match="metric exposure"):
        register_amendment(paths)
    assert not (paths.root / AMENDMENT_REGISTRATION).exists()


def test_synthetic_metric_exposure_without_final_selection_requires_amendment(
    synthetic_amendment,
):
    from bolivia_fire.extension.amendment import authorize_availability

    paths, _ = synthetic_amendment
    reg = verify_registration(paths)
    assert not (paths.root / "artifacts/evaluation/extension_selection.json").exists()
    with pytest.raises(ValueError, match="amendment"):
        authorize_availability(reg, {"retrospective_land_cover"})


def test_synthetic_committed_amendment_authorizes_only_unchanged_chain(synthetic_amendment):
    from bolivia_fire.extension.amendment import (
        AMENDMENT_REGISTRATION,
        authorize_availability,
        register_amendment,
    )

    paths, blobs = synthetic_amendment
    reg = verify_registration(paths)
    record = register_amendment(paths)
    assert record["h3_validation_metrics_previously_seen"] is True
    assert record["no_landcover_or_combined_labels_or_metrics_accessed"] is True
    assert record["amendment_commit"] == "a" * 40
    with pytest.raises(ValueError, match="committed|uncommitted"):
        authorize_availability(reg, {"retrospective_land_cover"})
    blobs[AMENDMENT_REGISTRATION] = (paths.root / AMENDMENT_REGISTRATION).read_bytes()
    proof = authorize_availability(reg, {"retrospective_land_cover"})
    assert proof["prior_artifact_hashes"]
    assert "retrospective_vegetation_lag16" not in proof["authorized_runs"]
    amendment = paths.root / "configs/extension_amendment_landcover.yaml"
    amendment.write_bytes(amendment.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="amendment|Amendment"):
        authorize_availability(reg, {"retrospective_land_cover"})


def test_synthetic_amendment_preserves_prior_metrics_and_refuses_re_registration(
    synthetic_amendment,
):
    from bolivia_fire.extension.amendment import (
        AMENDMENT_REGISTRATION,
        authorize_availability,
        register_amendment,
    )

    paths, blobs = synthetic_amendment
    register_amendment(paths)
    blobs[AMENDMENT_REGISTRATION] = (paths.root / AMENDMENT_REGISTRATION).read_bytes()
    with pytest.raises(ValueError, match="exists"):
        register_amendment(paths)
    prior = paths.root / "artifacts/evaluation/extension_execution_report.json"
    record = json.loads(prior.read_text("utf-8"))
    record["runs"]["retrospective_vegetation_lag16"]["average_precision"] = 0.9
    write_json(prior, record)
    with pytest.raises(ValueError, match="prior|Prior"):
        authorize_availability(verify_registration(paths), {"retrospective_land_cover"})


def test_synthetic_access_log_append_preserves_amendment_chain(synthetic_amendment):
    from bolivia_fire.extension.amendment import (
        AMENDMENT_REGISTRATION,
        authorize_availability,
        register_amendment,
    )

    paths, blobs = synthetic_amendment
    register_amendment(paths)
    blobs[AMENDMENT_REGISTRATION] = (paths.root / AMENDMENT_REGISTRATION).read_bytes()
    write_json(
        paths.root / "artifacts/evaluation/extension_confirmation_log.json",
        [
            {"state": "opened", "opening_id": "synthetic-new-opening"},
        ],
    )
    assert authorize_availability(verify_registration(paths), {"retrospective_land_cover"})


def test_synthetic_amended_selection_retains_interrupted_h3_decision(
    synthetic_amendment,
    monkeypatch,
):
    from types import SimpleNamespace

    from bolivia_fire.extension.amendment import (
        AMENDMENT_REGISTRATION,
        authorize_availability,
        register_amendment,
    )
    from bolivia_fire.extension.runner import Extension

    paths, blobs = synthetic_amendment
    prior = paths.root / "artifacts/evaluation/extension_execution_report.json"
    previous = json.loads(prior.read_text("utf-8"))
    previous.pop("primary_hypotheses")
    write_json(prior, previous)
    decision = {"status": "inconclusive", "reason": "synthetic retained interval"}
    checkpoint = (
        paths.root / "artifacts/evaluation/extension_fit_retrospective_vegetation_lag16.json"
    )
    write_json(
        checkpoint,
        {
            "run": {
                "run_id": "retrospective_vegetation_lag16",
                "hypothesis": "H3",
                "geography": "temporal",
                "availability_lag_days": 16,
            },
            "validation": {"decision": decision},
        },
    )
    original = checkpoint.read_bytes()
    register_amendment(paths)
    blobs[AMENDMENT_REGISTRATION] = (paths.root / AMENDMENT_REGISTRATION).read_bytes()
    reg = verify_registration(paths)
    proof = authorize_availability(reg, {"retrospective_land_cover"})
    source = SimpleNamespace(
        amendment=proof,
        input_bytes=0,
        hashes={},
        close=lambda: None,
        validate=lambda: None,
        available={
            run["run_id"]: {
                "status": ("evaluated_eligible" if run["hypothesis"] == "H3" else "not_evaluated")
            }
            for run in reg.runs
        },
    )
    monkeypatch.setattr("bolivia_fire.extension.runner.FrozenInputs", lambda *args: source)
    experiment = Extension(paths, "synthetic-new-plan.json")
    monkeypatch.setattr(experiment, "_load_comparators", lambda: None)
    monkeypatch.setattr(experiment, "_select_geography", lambda *args: {})
    assert not experiment._active("temporal")
    assert experiment.selection_path.parent.name == proof["amendment_id"]
    assert experiment.select()["hypotheses"]["H3"] == decision
    assert checkpoint.read_bytes() == original
    assert not (paths.root / "artifacts/evaluation/extension_selection.json").exists()
