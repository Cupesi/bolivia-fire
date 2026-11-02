from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from conftest import commit_synthetic_amendment, write_json

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
    dates = [datetime(2013, 1, 7), datetime(2021, 1, 4), datetime(2022, 1, 3), datetime(2024, 1, 1)]
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


@pytest.mark.parametrize("stage, expected_cells", [("val_select", [2]), ("confirmation", [])])
def test_synthetic_annual_panels_skip_empty_partition_batches(
    synthetic_inputs, stage, expected_cells
):
    reg, plan_path = synthetic_inputs
    plan_file = reg.paths.root / plan_path
    plan = json.loads(plan_file.read_text("utf-8"))
    table = pq.ParquetFile(reg.paths.root / plan["panel_files"][0]).read()
    plan["panel_files"] = []
    for index in range(len(table)):
        path = reg.paths.root / f"datasets/processed/panel/synthetic_year_{index}.parquet"
        pq.write_table(table.slice(index, 1), path)
        name = path.relative_to(reg.paths.root).as_posix()
        plan["panel_files"].append(name)
        plan["input_hashes"][name] = sha256_file(path)
    write_json(plan_file, plan)
    source = FrozenInputs(reg, plan_path)
    try:
        with Resources(reg, "synthetic_annual_panels") as resources:
            data = (
                source.plan_support(reg.runs[0], stage, resources)
                if stage == "confirmation"
                else source.load(reg.runs[0], stage, resources, labels=False)
            )
        assert data.column("cell_id").tolist() == expected_cells
        assert data.exclusions["eligible" if stage == "confirmation" else "scanned"] == len(
            expected_cells
        )
        assert data.x.shape == (len(expected_cells), 0 if stage == "confirmation" else 36)
        assert np.isnan(data.y).all()
    finally:
        source.close()


def add_synthetic_landcover(synthetic_inputs, fractions):
    reg, plan_path = synthetic_inputs
    plan_file = reg.paths.root / plan_path
    plan = json.loads(plan_file.read_text("utf-8"))
    vegetation = reg.runs[0]
    combined = next(
        run for run in reg.runs if run["run_id"] == "retrospective_vegetation_land_cover_lag16"
    )
    land = reg.paths.root / "datasets/processed/vegetation/synthetic_land_cover.parquet"
    dates = [datetime(2013, 1, 7), datetime(2021, 1, 4), datetime(2022, 1, 3), datetime(2024, 1, 1)]
    fields = [name for name in combined["ordered_features"] if name.startswith("mapbiomas_")]
    source_name = "datasets/processed/landcover/source.json"
    raster_name = "datasets/manifests/mapbiomas_raster.jsonl"
    source_hashes = {
        "configs/extension_vegetation.yaml": reg.record["contract_sha256"],
        "datasets/processed/cells.parquet": plan["input_hashes"][
            "datasets/processed/cells.parquet"
        ],
    }
    identities = []
    for year in (2012, 2020, 2021, 2023):
        name = f"datasets/processed/landcover/mapbiomas_{year}.parquet"
        path = reg.paths.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic frozen annual map")
        source_hashes[name] = sha256_file(path)
        identities.append(
            {
                "year": year,
                "collection": "MapBiomas Bolivia Collection 4",
                "asset": reg.contract["land_cover"]["access"]["gee_route"]["asset"],
                "classification_band": f"classification_{year}",
                "output": name,
                "output_sha256": source_hashes[name],
            }
        )
    raster = reg.paths.root / raster_name
    raster.write_text("\n".join(json.dumps(row) for row in identities), encoding="utf-8")
    source_hashes[raster_name] = sha256_file(raster)
    snapshot = hashlib.sha256(json.dumps(source_hashes, sort_keys=True).encode()).hexdigest()
    import yaml

    from bolivia_fire.config import REPO_ROOT

    amendment = yaml.safe_load(
        (REPO_ROOT / "configs/extension_amendment_landcover.yaml").read_text()
    )
    acceptance = amendment["estimator"]["acceptance_evidence"]
    source = {
        "collection": 4,
        "no_labels_accessed": True,
        "no_training_selection_or_scoring": True,
        "extension_contract_sha256": reg.record["contract_sha256"],
        "land_cover_snapshot_sha256": snapshot,
        "input_hashes": source_hashes,
        "map_years": [2012, 2020, 2021, 2023],
        "polygon_pilot_validation": {
            k: acceptance[k]
            for k in (
                "cells",
                "fraction_max_absolute_error",
                "coverage_max_absolute_error",
                "absolute_tolerance",
                "passed",
            )
        },
    }
    write_json(reg.paths.root / source_name, source)
    plan["input_hashes"].update(source_hashes)
    plan["input_hashes"][source_name] = sha256_file(reg.paths.root / source_name)
    pq.write_table(
        pa.table(
            {
                "cell_id": [1, 2, 3, 4],
                "issue_date": dates,
                "land_cover_nominal_map_year": [date.year - 1 for date in dates],
                "land_cover_map_year": [date.year - 1 for date in dates],
                "land_cover_reference_age_years": [1] * 4,
                "land_cover_mapped_area_fraction": [0.99] * 4,
                **{
                    name: pa.array([fraction] * 4, type=pa.float32())
                    for name, fraction in zip(fields, fractions, strict=True)
                },
            }
        ).replace_schema_metadata(
            {
                b"extension_contract_sha256": reg.record["contract_sha256"].encode(),
                b"land_cover_snapshot_sha256": snapshot.encode(),
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
    audit["input_hashes"].update(source_hashes)
    audit["input_hashes"][source_name] = plan["input_hashes"][source_name]
    write_json(reg.paths.root / audit_name, audit)
    plan["input_hashes"][audit_name] = sha256_file(reg.paths.root / audit_name)
    plan["runs"][combined["run_id"]] = {
        "status": "evaluated_eligible",
        "audit": audit_name,
        "feature_files": [*plan["runs"][vegetation["run_id"]]["feature_files"], land_name],
    }
    write_json(plan_file, plan)
    return combined, land_name


@pytest.mark.parametrize(
    "fractions",
    [
        [0.2] * 5,
        [
            0.3275774419307709,
            0.39724981784820557,
            0.16077028214931488,
            0.05955145135521889,
            0.054851967841386795,
        ],
    ],
)
def test_synthetic_combined_variant_joins_separate_audited_groups(
    synthetic_inputs,
    synthetic_registration,
    fractions,
):
    reg, plan_path = synthetic_inputs
    commit_synthetic_amendment(*synthetic_registration)
    combined, _ = add_synthetic_landcover(synthetic_inputs, fractions)
    source = FrozenInputs(reg, plan_path)
    try:
        with Resources(reg, "synthetic_combined") as resources:
            data = source.load(combined, "val_select", resources)
            planned = source.plan_support(combined, "val_select", resources)
        assert data.x.shape == (1, 41)
        np.testing.assert_allclose(data.x[0, -5:], fractions)
        assert planned.column("cell_id").tolist() == data.column("cell_id").tolist()
        assert source.available["retrospective_land_cover"]["status"] == "not_evaluated"
    finally:
        source.close()


@pytest.mark.parametrize(
    "fault",
    [
        "absent_source",
        "snapshot",
        "grid",
        "collection",
        "annual_identity",
        "evidence",
        "missing_provenance",
        "missing_snapshot_metadata",
        "mismatched_snapshot_metadata",
        "future",
        "stale",
        "not_latest",
        "age",
        "nominal",
        "coverage",
    ],
)
def test_synthetic_combined_landcover_admission_refusals(
    synthetic_inputs,
    synthetic_registration,
    fault,
):
    reg, plan_path = synthetic_inputs
    commit_synthetic_amendment(*synthetic_registration)
    run, land_name = add_synthetic_landcover(synthetic_inputs, [0.2] * 5)
    plan_file = reg.paths.root / plan_path
    plan = json.loads(plan_file.read_text("utf-8"))
    source_name = "datasets/processed/landcover/source.json"
    source_file = reg.paths.root / source_name
    source = json.loads(source_file.read_text("utf-8"))
    if fault == "absent_source":
        del plan["input_hashes"][source_name]
    elif fault in {"snapshot", "grid", "collection", "annual_identity", "evidence"}:
        if fault == "snapshot":
            source["land_cover_snapshot_sha256"] = "f" * 64
        elif fault == "grid":
            source["input_hashes"]["datasets/processed/cells.parquet"] = "f" * 64
        elif fault == "collection":
            source["collection"] = 3
        elif fault == "evidence":
            source["polygon_pilot_validation"]["fraction_max_absolute_error"] = 0.001
        else:
            source["map_years"] = [2012, 2020, 2021]
        write_json(source_file, source)
        plan["input_hashes"][source_name] = sha256_file(source_file)
    else:
        path = reg.paths.root / land_name
        table = pq.read_table(path)
        if fault == "missing_provenance":
            table = table.drop(["land_cover_map_year"])
        elif fault in {"missing_snapshot_metadata", "mismatched_snapshot_metadata"}:
            metadata = dict(table.schema.metadata)
            metadata.pop(b"land_cover_snapshot_sha256")
            if fault == "mismatched_snapshot_metadata":
                metadata[b"land_cover_snapshot_sha256"] = b"synthetic mismatched snapshot"
            table = table.replace_schema_metadata(metadata)
        else:
            field, value = {
                "future": ("land_cover_map_year", 2025),
                "stale": ("land_cover_map_year", 2010),
                "not_latest": ("land_cover_map_year", 2019),
                "age": ("land_cover_reference_age_years", -4),
                "nominal": ("land_cover_nominal_map_year", 2025),
                "coverage": ("land_cover_mapped_area_fraction", 0.1),
            }[fault]
            values = table[field].to_pylist()
            values[1] = value
            table = table.set_column(table.schema.get_field_index(field), field, pa.array(values))
        pq.write_table(table, path)
        plan["input_hashes"][land_name] = sha256_file(path)
    audit_name = plan["runs"][run["run_id"]]["audit"]
    audit = json.loads((reg.paths.root / audit_name).read_text("utf-8"))
    audit["input_hashes"] = {
        name: plan["input_hashes"][name]
        for name in audit["input_hashes"]
        if name in plan["input_hashes"]
    }
    write_json(reg.paths.root / audit_name, audit)
    plan["input_hashes"][audit_name] = sha256_file(reg.paths.root / audit_name)
    write_json(plan_file, plan)
    with pytest.raises(ValueError, match="Land-cover|land-cover|checksum"):
        source = FrozenInputs(reg, plan_path)
        try:
            with Resources(reg, "synthetic_landcover_refusal") as resources:
                source.plan_support(run, "val_select", resources)
        finally:
            source.close()


def test_synthetic_landcover_only_uses_the_same_source_and_admission_gate(
    synthetic_inputs,
    synthetic_registration,
):
    reg, plan_path = synthetic_inputs
    commit_synthetic_amendment(*synthetic_registration)
    combined, land_name = add_synthetic_landcover(synthetic_inputs, [0.2] * 5)
    plan_file = reg.paths.root / plan_path
    plan = json.loads(plan_file.read_text("utf-8"))
    entry = plan["runs"].pop(combined["run_id"])
    run = next(run for run in reg.runs if run["run_id"] == "retrospective_land_cover")
    audit = json.loads((reg.paths.root / entry["audit"]).read_text("utf-8"))
    audit.update(
        {
            key: run[key]
            for key in (
                "run_id",
                "ordered_features",
                "geography",
                "availability_lag_days",
            )
        }
    )
    entry["audit"] = "artifacts/evaluation/synthetic_landcover_only_audit.json"
    entry["feature_files"] = [land_name]
    write_json(reg.paths.root / entry["audit"], audit)
    plan["input_hashes"][entry["audit"]] = sha256_file(reg.paths.root / entry["audit"])
    plan["runs"][run["run_id"]] = entry
    write_json(plan_file, plan)
    source = FrozenInputs(reg, plan_path)
    try:
        with Resources(reg, "synthetic_landcover_only") as resources:
            loaded = source.load(run, "val_select", resources)
            planned = source.plan_support(run, "val_select", resources)
        assert loaded.x.shape == (1, 31)
        assert planned.column("cell_id").tolist() == loaded.column("cell_id").tolist() == [2]
    finally:
        source.close()


def test_synthetic_prior_h3_metrics_and_new_audit_require_committed_amendment(
    synthetic_inputs,
    synthetic_registration,
):
    reg, plan_path = synthetic_inputs
    prior = reg.paths.root / "artifacts/evaluation/extension_execution_report.json"
    write_json(
        prior,
        {
            "runs": {
                "retrospective_vegetation_lag16": {
                    "status": "evaluated_eligible",
                    "average_precision": 0.4,
                }
            },
        },
    )
    prior_bytes = prior.read_bytes()
    add_synthetic_landcover(synthetic_inputs, [0.2] * 5)
    assert not (reg.paths.root / "artifacts/evaluation/extension_selection.json").exists()
    with pytest.raises(ValueError, match="amendment"):
        FrozenInputs(reg, plan_path)
    commit_synthetic_amendment(*synthetic_registration)
    source = FrozenInputs(reg, plan_path)
    try:
        assert source.available["retrospective_vegetation_land_cover_lag16"]["status"] == (
            "evaluated_eligible"
        )
        assert prior.read_bytes() == prior_bytes
    finally:
        source.close()
    amendment = reg.paths.root / "artifacts/evaluation/extension_amendment_registration.json"
    amendment.write_bytes(amendment.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="committed"):
        FrozenInputs(reg, plan_path)


def test_synthetic_frozen_input_plan_survives_adapter_restart(synthetic_inputs):
    reg, plan_path = synthetic_inputs
    first = FrozenInputs(reg, plan_path)
    try:
        first.freeze()
        frozen = dict(first.hashes)
    finally:
        first.close()
    second = FrozenInputs(reg, plan_path)
    try:
        assert second.hashes == frozen
    finally:
        second.close()
    path = reg.paths.root / plan_path
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="frozen"):
        FrozenInputs(reg, plan_path)


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


@pytest.mark.parametrize("stage", ["train", "val_select", "val_calib", "test", "confirmation"])
@pytest.mark.parametrize("issue", [datetime(2026, 1, 5), datetime(2012, 1, 2)])
def test_synthetic_mispartitioned_dates_refuse_before_label_projection(
    synthetic_inputs, monkeypatch, stage, issue
):
    reg, plan_path = synthetic_inputs
    plan_file = reg.paths.root / plan_path
    plan = json.loads(plan_file.read_text("utf-8"))
    panel_name = plan["panel_files"][0]
    panel_path = reg.paths.root / panel_name
    table = pq.read_table(panel_path)
    row = table.slice(0, 1).to_pydict()
    row["partition"] = [stage]
    row["issue_date"] = [issue if stage != "confirmation" else datetime(2025, 1, 6)]
    pq.write_table(pa.Table.from_pydict(row, schema=table.schema), panel_path)
    plan["input_hashes"][panel_name] = sha256_file(panel_path)
    write_json(plan_file, plan)
    source = FrozenInputs(reg, plan_path)
    monkeypatch.setattr(source, "_rain_source", lambda year: pytest.fail("Reached feature join"))
    try:
        with (
            Resources(reg, "synthetic_bad_partition") as resources,
            pytest.raises(ValueError, match="partition|window"),
        ):
            source.load(reg.runs[0], "train", resources, labels=False)
    finally:
        source.close()


@pytest.mark.parametrize("partition", ["train", "val_select"])
def test_synthetic_cross_partition_duplicate_keys_refuse(synthetic_inputs, partition):
    reg, plan_path = synthetic_inputs
    plan_file = reg.paths.root / plan_path
    plan = json.loads(plan_file.read_text("utf-8"))
    panel_path = reg.paths.root / plan["panel_files"][0]
    table = pq.read_table(panel_path)
    duplicate = table.slice(0, 1).set_column(
        table.schema.get_field_index("partition"), "partition", pa.array([partition])
    )
    path = reg.paths.root / "datasets/processed/panel/synthetic_duplicate.parquet"
    pq.write_table(duplicate, path)
    name = path.relative_to(reg.paths.root).as_posix()
    plan["panel_files"].append(name)
    plan["input_hashes"][name] = sha256_file(path)
    write_json(plan_file, plan)
    source = FrozenInputs(reg, plan_path)
    try:
        with (
            Resources(reg, "synthetic_duplicate") as resources,
            pytest.raises(ValueError, match="Duplicate|partition"),
        ):
            source.load(reg.runs[0], "train", resources, labels=False)
    finally:
        source.close()


@pytest.mark.parametrize(
    "issue", [datetime(2020, 12, 28), datetime(2013, 1, 8), datetime(2013, 1, 7, 1)]
)
def test_synthetic_partition_calendar_and_target_purge(synthetic_inputs, issue):
    reg, plan_path = synthetic_inputs
    plan_file = reg.paths.root / plan_path
    plan = json.loads(plan_file.read_text("utf-8"))
    name = plan["panel_files"][0]
    path = reg.paths.root / name
    table = pq.read_table(path).slice(0, 1)
    table = table.set_column(
        table.schema.get_field_index("issue_date"),
        "issue_date",
        pa.array([issue], type=table.schema.field("issue_date").type),
    )
    pq.write_table(table, path)
    plan["input_hashes"][name] = sha256_file(path)
    write_json(plan_file, plan)
    source = FrozenInputs(reg, plan_path)
    try:
        with (
            Resources(reg, "synthetic_calendar") as resources,
            pytest.raises(ValueError, match="calendar|boundary"),
        ):
            source.load(reg.runs[0], "train", resources)
    finally:
        source.close()


def test_synthetic_last_test_issue_preserves_registered_terminal_window(synthetic_inputs):
    reg, plan_path = synthetic_inputs
    plan_file = reg.paths.root / plan_path
    plan = json.loads(plan_file.read_text("utf-8"))
    name = plan["panel_files"][0]
    path = reg.paths.root / name
    table = pq.read_table(path).slice(3, 1)
    table = table.set_column(
        table.schema.get_field_index("issue_date"),
        "issue_date",
        pa.array([datetime(2025, 12, 29)], type=table.schema.field("issue_date").type),
    )
    pq.write_table(table, path)
    plan["input_hashes"][name] = sha256_file(path)
    write_json(plan_file, plan)
    source = FrozenInputs(reg, plan_path)
    try:
        with Resources(reg, "synthetic_terminal_window") as resources:
            source._validate_partitions(resources)
        assert source._partitions_validated
    finally:
        source.close()


def test_synthetic_support_planning_projects_only_metadata(synthetic_inputs, monkeypatch):
    from bolivia_fire.extension import inputs

    reg, plan = synthetic_inputs
    source = FrozenInputs(reg, plan)
    original_dataset = inputs.ds.dataset
    projections = []
    allowed = {
        "cell_id",
        "issue_date",
        "partition",
        "geo_holdout",
        "santa_cruz",
        "block_id",
        "national_area_km2",
    }

    class SyntheticMetadataScan:
        def __init__(self, dataset):
            self.dataset = dataset

        def __getattr__(self, name):
            return getattr(self.dataset, name)

        def to_batches(self, *, columns, **kwargs):
            assert set(columns) <= allowed
            projections.append(columns)
            return self.dataset.to_batches(columns=columns, **kwargs)

    def dataset(path, **kwargs):
        result = original_dataset(path, **kwargs)
        return SyntheticMetadataScan(result) if Path(path) in source.panel_files else result

    monkeypatch.setattr(inputs.ds, "dataset", dataset)
    try:
        with Resources(reg, "synthetic_support_plan") as resources:
            planned = source.plan_support(reg.runs[0], "val_select", resources)
            assert planned.column("cell_id").tolist() == [2]
            assert planned.x.shape == (1, 0)
            assert np.isnan(planned.y).all()
            assert set(planned.metadata.column_names) == allowed
            assert projections
            with pytest.raises(ValueError, match="opening"):
                source.load(reg.runs[0], "confirmation", resources, labels=False)
    finally:
        source.close()


def test_synthetic_training_matrix_released_between_loads(synthetic_inputs):
    reg, plan_path = synthetic_inputs
    source = FrozenInputs(reg, plan_path)
    try:
        with Resources(reg, "synthetic_release") as resources:
            for _ in range(4):
                data = source.load(reg.runs[0], "train", resources)
                np.testing.assert_array_equal(data.y, [0])
                source.release(data)
                assert not source.arrays
                assert not list(Path(source.temporary.name).glob("*.f32"))
    finally:
        source.close()
