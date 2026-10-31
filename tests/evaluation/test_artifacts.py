from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from bolivia_fire.config import REPO_ROOT, Paths
from bolivia_fire.evaluation.artifacts import record_final_opening, snapshot


@pytest.fixture
def synthetic_snapshot_paths(tmp_path: Path) -> Paths:
    configs = tmp_path / "configs"
    configs.mkdir()
    for name in ("protocol.yaml", "experiments.yaml"):
        shutil.copyfile(REPO_ROOT / "configs" / name, configs / name)
    for directory in ("panel", "covariates/chirps_features"):
        for year in (2012, 2020, 2022, 2023, 2026):
            path = tmp_path / f"datasets/processed/{directory}/year={year}/part.parquet"
            path.parent.mkdir(parents=True)
            # Snapshot hashes bytes without parsing the synthetic partition contents.
            path.write_bytes(b"synthetic partition\n")
    (tmp_path / "datasets/sources.yaml").write_text("synthetic: true\n")
    manifest = tmp_path / "datasets/processed/covariates/chirps_manifest.jsonl"
    manifest.write_text('{"synthetic": "initial"}\n')
    return Paths(tmp_path)


@pytest.mark.parametrize(
    ("variant", "uses_covariates"),
    [("strict", False), ("strict_geo", False), ("retrospective", True),
     ("retrospective_geo", True)],
)
@pytest.mark.parametrize("include_test", [False, True])
@pytest.mark.parametrize(
    "changed_file", ["chirps_manifest.jsonl", "chirps_features/year=2020/part.parquet"],
)
def test_synthetic_chirps_appends_only_change_retrospective_snapshots(
    synthetic_snapshot_paths: Paths,
    variant: str,
    uses_covariates: bool,
    include_test: bool,
    changed_file: str,
) -> None:
    paths = synthetic_snapshot_paths
    before = snapshot(paths, variant=variant, include_test=include_test)
    expected = {
        "configs/protocol.yaml", "configs/experiments.yaml", "datasets/sources.yaml",
        "datasets/processed/panel/year=2020/part.parquet",
        "datasets/processed/panel/year=2022/part.parquet",
    }
    covariates = {
        "datasets/processed/covariates/chirps_manifest.jsonl",
        "datasets/processed/covariates/chirps_features/year=2020/part.parquet",
        "datasets/processed/covariates/chirps_features/year=2022/part.parquet",
    }
    if include_test:
        expected.add("datasets/processed/panel/year=2023/part.parquet")
        covariates.add("datasets/processed/covariates/chirps_features/year=2023/part.parquet")
    assert set(before) == expected | (covariates if uses_covariates else set())
    changed_path = paths.datasets / "processed/covariates" / changed_file
    with changed_path.open("ab") as stream:
        stream.write(b'{"synthetic": "appended during run"}\n')
    after = snapshot(paths, variant=variant, include_test=include_test)
    assert {name: after[name] for name in expected} == {name: before[name] for name in expected}
    assert (after != before) is uses_covariates


@pytest.mark.parametrize("variant", ["strict", "retrospective"])
def test_synthetic_final_opening_records_variant_snapshot(
    synthetic_snapshot_paths: Paths, variant: str,
) -> None:
    paths = synthetic_snapshot_paths
    opening = record_final_opening(paths, {}, {}, {"seed": 7}, None, variant=variant)
    assert opening["dataset_snapshot_hashes"] == snapshot(paths, variant=variant, include_test=True)
    assert "datasets/processed/panel/year=2023/part.parquet" in opening["dataset_snapshot_hashes"]


def test_synthetic_final_opening_records_all_consumed_variants(
    synthetic_snapshot_paths: Paths,
) -> None:
    paths = synthetic_snapshot_paths
    variants = ("strict", "strict_geo", "retrospective")
    opening = record_final_opening(
        paths, {}, {}, {"seed": 7}, None, variant="strict", consumed_variants=variants,
    )
    snapshots = opening["consumed_variant_snapshot_hashes"]
    assert set(snapshots) == set(variants)
    chirps = "datasets/processed/covariates/chirps_features/year=2023/part.parquet"
    assert chirps not in snapshots["strict"]
    assert chirps not in snapshots["strict_geo"]
    assert chirps in snapshots["retrospective"]
    assert opening["dataset_snapshot_hashes"] == snapshots["retrospective"]
