from __future__ import annotations

import json
from datetime import datetime

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from conftest import write_json

from bolivia_fire.covariates.chirps import PRODUCT, REFERENCE_RULE
from bolivia_fire.extension.inputs import FrozenInputs
from bolivia_fire.extension.registration import verify_registration
from bolivia_fire.extension.resources import Resources
from bolivia_fire.firms.storage import sha256_file


@pytest.fixture
def synthetic_inputs(synthetic_registration):
    paths, _ = synthetic_registration
    reg = verify_registration(paths)
    files = []
    for name in (
        "datasets/processed/cells.parquet",
        "datasets/manifests/snpp_outages.csv",
        "datasets/manifests/firms_VIIRS_SNPP_SP.jsonl",
        "datasets/sources.yaml",
    ):
        path = paths.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic frozen metadata")
        files.append(path)
    source = paths.root / "datasets/processed/vegetation/source.json"
    write_json(
        source,
        {
            "extension_contract_sha256": reg.record["contract_sha256"],
            "vegetation_snapshot_sha256": "synthetic dependency fingerprint",
            "no_labels_accessed": True,
            "cells_sha256": sha256_file(paths.root / "datasets/processed/cells.parquet"),
        },
    )
    files.append(source)
    strict = reg.contract["comparators"]["published_strict_context"]["features"]
    rain = reg.contract["comparators"]["published_retrospective"]["features"][-5:]
    manifest = paths.root / "datasets/processed/covariates/chirps_manifest.jsonl"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("", encoding="utf-8")
    files.append(manifest)
    dates = [datetime(year, 1, 4) for year in (2013, 2021, 2022, 2024)]
    panel = pa.table(
        {
            **{name: [1.0] * 4 for name in strict},
            "cell_id": [1, 2, 3, 4],
            "issue_date": dates,
            "partition": ["train", "val_select", "val_calib", "test"],
            "geo_holdout": [False] * 4,
            "santa_cruz": [True] * 4,
            "block_id": [1] * 4,
            "national_area_km2": [1.0] * 4,
            "window_obs_complete_28d": [True] * 4,
            "y_primary": [0.0, 1.0, 0.0, 1.0],
        }
    )
    panel_path = paths.root / "datasets/processed/panel/synthetic.parquet"
    panel_path.parent.mkdir(parents=True)
    pq.write_table(panel, panel_path)
    files.append(panel_path)
    for i, date in enumerate(dates):
        chirps = pa.table(
            {
                "cell_id": [i + 1],
                "issue_date": [date],
                **{name: [1.0] for name in rain},
                **{
                    name: [True]
                    for name in (
                        "rain_complete_7d",
                        "rain_complete_30d",
                        "rain_complete_90d",
                        "rain_climatology_complete",
                    )
                },
            }
        ).replace_schema_metadata(
            {
                b"product": PRODUCT.encode(),
                b"protocol_sha256": reg.record["protocol_sha256"].encode(),
                b"train_years": b"[2013,2014,2015,2016,2017,2018,2019,2020]",
                b"reference_rule": REFERENCE_RULE.encode(),
                b"manifest_sha256": sha256_file(manifest).encode(),
            }
        )
        path = (
            paths.root
            / f"datasets/processed/covariates/chirps_features/year={date.year}/part.parquet"
        )
        path.parent.mkdir(parents=True)
        pq.write_table(chirps, path)
        files.append(path)
    runs = {}
    for run in reg.runs:
        if run["status"] == "not_evaluated":
            continue
        extra = [name for name in run["ordered_features"] if name.startswith("veg_")]
        feature = pa.table(
            {"cell_id": [1, 2, 3, 4], "issue_date": dates, **{name: [0.5] * 4 for name in extra}}
        ).replace_schema_metadata(
            {
                b"extension_contract_sha256": reg.record["contract_sha256"].encode(),
                b"vegetation_lag_days": str(run["availability_lag_days"]).encode(),
                b"vegetation_geography": run["geography"].encode(),
                b"vegetation_snapshot_sha256": b"synthetic dependency fingerprint",
            }
        )
        path = paths.root / f"datasets/processed/vegetation/{run['run_id']}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(feature, path)
        files.append(path)
        audit_path = paths.root / f"artifacts/evaluation/synthetic_audit_{run['run_id']}.json"
        audit = {
            key: run[key]
            for key in ("run_id", "ordered_features", "geography", "availability_lag_days")
        }
        audit.update(
            {
                "status": "evaluated_eligible",
                "contract_sha256": reg.record["contract_sha256"],
                "input_hashes": {path.relative_to(paths.root).as_posix(): sha256_file(path)},
                "checks": {
                    key: True
                    for key in (
                        "scaling_verified",
                        "quality_verified",
                        "temporal_verified",
                        "climatology_verified",
                        "provenance_verified",
                    )
                },
            }
        )
        write_json(audit_path, audit)
        files.append(audit_path)
        runs[run["run_id"]] = {
            "status": "evaluated_eligible",
            "audit": audit_path.relative_to(paths.root).as_posix(),
            "feature_files": [path.relative_to(paths.root).as_posix()],
        }
    plan = {
        "contract_sha256": reg.record["contract_sha256"],
        "runs": runs,
        "panel_files": [panel_path.relative_to(paths.root).as_posix()],
        "input_hashes": {
            path.relative_to(paths.root).as_posix(): sha256_file(path) for path in files
        },
    }
    write_json(paths.root / "artifacts/evaluation/synthetic_inputs.json", plan)
    return reg, "artifacts/evaluation/synthetic_inputs.json"


def test_synthetic_real_input_adapter_keeps_partitions_separate(synthetic_inputs):
    reg, plan = synthetic_inputs
    source = FrozenInputs(reg, plan)
    try:
        with Resources(reg, "synthetic_loading") as resources:
            data = source.load(reg.runs[0], "val_select", resources)
            assert data.y.tolist() == [1.0]
            assert data.column("cell_id").tolist() == [2]
            assert data.features == reg.runs[0]["ordered_features"]
            assert data.x.shape == (1, 36)
            assert np.isfinite(data.x).all()
            with pytest.raises(ValueError, match="opening"):
                source.load(reg.runs[0], "test", resources)
            unlabeled = source.load(reg.runs[0], "test", resources, labels=False)
            assert np.isnan(unlabeled.y).all()
            assert unlabeled.column("cell_id").tolist() == [4]
    finally:
        source.close()


def test_synthetic_missing_feature_audit_refuses_before_labels(synthetic_inputs):
    reg, plan = synthetic_inputs
    audit = next((reg.paths.root / "artifacts/evaluation").glob("synthetic_audit_*.json"))
    audit.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        FrozenInputs(reg, plan)


def test_synthetic_combined_variant_joins_separate_audited_groups(synthetic_inputs):
    reg, plan_path = synthetic_inputs
    plan_file = reg.paths.root / plan_path
    plan = json.loads(plan_file.read_text("utf-8"))
    vegetation = reg.runs[0]
    combined = next(
        run for run in reg.runs if run["run_id"] == "retrospective_vegetation_land_cover_lag16"
    )
    land = reg.paths.root / "datasets/processed/vegetation/synthetic_land_cover.parquet"
    dates = [datetime(year, 1, 4) for year in (2013, 2021, 2022, 2024)]
    fields = [name for name in combined["ordered_features"] if name.startswith("mapbiomas_")]
    pq.write_table(
        pa.table(
            {"cell_id": [1, 2, 3, 4], "issue_date": dates, **{name: [0.2] * 4 for name in fields}}
        ).replace_schema_metadata(
            {
                b"extension_contract_sha256": reg.record["contract_sha256"].encode(),
                b"land_cover_snapshot_sha256": b"synthetic land-cover fingerprint",
            }
        ),
        land,
    )
    land_name = land.relative_to(reg.paths.root).as_posix()
    plan["input_hashes"][land_name] = sha256_file(land)
    audit_name = "artifacts/evaluation/synthetic_combined_audit.json"
    audit = json.loads(
        (reg.paths.root / plan["runs"][vegetation["run_id"]]["audit"]).read_text("utf-8")
    )
    audit.update(
        {
            key: combined[key]
            for key in ("run_id", "ordered_features", "geography", "availability_lag_days")
        }
    )
    audit["checks"].update(zero_cost_mapbiomas_verification=True, land_cover_features=True)
    audit["input_hashes"][land_name] = sha256_file(land)
    write_json(reg.paths.root / audit_name, audit)
    plan["input_hashes"][audit_name] = sha256_file(reg.paths.root / audit_name)
    plan["runs"][combined["run_id"]] = {
        "status": "evaluated_eligible",
        "audit": audit_name,
        "feature_files": [*plan["runs"][vegetation["run_id"]]["feature_files"], land_name],
    }
    write_json(plan_file, plan)
    source = FrozenInputs(reg, plan_path)
    try:
        with Resources(reg, "synthetic_combined") as resources:
            data = source.load(combined, "val_select", resources)
        assert data.x.shape == (1, 41)
        np.testing.assert_allclose(data.x[0, -5:], [0.2] * 5)
        assert source.available["retrospective_land_cover"]["status"] == "not_evaluated"
    finally:
        source.close()


def test_synthetic_registered_input_mutation_refuses_before_projection(synthetic_inputs):
    reg, plan_path = synthetic_inputs
    source = FrozenInputs(reg, plan_path)
    try:
        path = reg.paths.root / "configs/extension_vegetation.yaml"
        path.write_bytes(path.read_bytes() + b"\n")
        with (
            Resources(reg, "synthetic_mutation") as resources,
            pytest.raises(ValueError, match="hash"),
        ):
            source.load(reg.runs[0], "val_select", resources)
    finally:
        source.close()
