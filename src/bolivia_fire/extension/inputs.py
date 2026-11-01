from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from bolivia_fire.extension.access import Opening, check_hashes, verify_opening
from bolivia_fire.extension.registration import (
    REGISTERED_FILES,
    REGISTRATION,
    Registration,
    local_path,
    read_json,
)
from bolivia_fire.extension.resources import Resources, owned_bytes
from bolivia_fire.firms.storage import sha256_file
from bolivia_fire.models.data import META_COLUMNS, PanelData
from bolivia_fire.models.retrospective import completeness_columns


class FrozenInputs:
    def __init__(self, registration: Registration, plan_path: str) -> None:
        self.registration = registration
        self.paths = registration.paths
        self.plan_path = plan_path
        path = local_path(self.paths, plan_path)
        self.plan = read_json(path)
        if self.plan.get("contract_sha256") != registration.record["contract_sha256"]:
            raise ValueError("Extension input plan contract hash mismatch")
        check_hashes(registration, self.plan["input_hashes"])
        self.hashes = {
            **self.plan["input_hashes"],
            plan_path: sha256_file(path),
            REGISTRATION: registration.digest,
            **{
                relative: registration.record[f"{name}_sha256"]
                for name, relative in REGISTERED_FILES.items()
            },
        }
        self.panel_files = self._files(self.plan["panel_files"])
        self._files(
            [
                "datasets/processed/cells.parquet",
                "datasets/manifests/snpp_outages.csv",
                "datasets/manifests/firms_VIIRS_SNPP_SP.jsonl",
                "datasets/sources.yaml",
            ]
        )
        self.rain = list(
            registration.contract["comparators"]["published_retrospective"]["features"][-5:]
        )
        self.rain_flags = completeness_columns(self.rain)
        if set(self.plan.get("runs", {})) - {run["run_id"] for run in registration.runs}:
            raise ValueError("Input plan requests an unregistered extension run")
        self.available: dict[str, dict[str, Any]] = {}
        for run in registration.runs:
            identity = run["run_id"]
            entry = self.plan.get("runs", {}).get(identity, {})
            if run["status"] == "not_evaluated" and entry.get("status") != "evaluated_eligible":
                self.available[identity] = {
                    "status": "not_evaluated",
                    "reason": "registered_group_unavailable",
                }
                continue
            if entry.get("status") != "evaluated_eligible":
                raise ValueError(f"Required extension feature preflight unavailable: {identity}")
            audit_name = entry["audit"]
            self._files([audit_name])
            audit = read_json(local_path(self.paths, audit_name))
            expected = {
                key: run[key]
                for key in (
                    "run_id",
                    "geography",
                    "availability_lag_days",
                    "ordered_features",
                )
            }
            if any(audit.get(key) != value for key, value in expected.items()):
                raise ValueError(f"Feature audit differs from registered run: {identity}")
            checks = (
                "scaling_verified",
                "quality_verified",
                "temporal_verified",
                "climatology_verified",
                "provenance_verified",
            )
            if (
                audit.get("status") != "evaluated_eligible"
                or audit.get("contract_sha256") != registration.record["contract_sha256"]
                or any(audit.get("checks", {}).get(key) is not True for key in checks)
            ):
                raise ValueError(f"Feature audit failed: {identity}")
            for gate in run["required_gates"]:
                if gate != "vegetation" and audit.get("checks", {}).get(gate) is not True:
                    raise ValueError(f"Land-cover availability gate failed: {gate}")
            check_hashes(registration, audit["input_hashes"])
            for name, digest in audit["input_hashes"].items():
                if self.hashes.get(name) != digest:
                    raise ValueError("Feature audit inputs must be frozen in the input plan")
            self._files(entry["feature_files"])
            source_manifests = {}
            for feature_path in self._files(entry["feature_files"]):
                schema = pq.read_schema(feature_path)
                metadata = schema.metadata or {}
                required = {
                    b"extension_contract_sha256": registration.record["contract_sha256"].encode(),
                }
                if any(name.startswith("veg_") for name in schema.names):
                    required.update(
                        {
                            b"vegetation_lag_days": str(run["availability_lag_days"]).encode(),
                            b"vegetation_geography": run["geography"].encode(),
                        }
                    )
                    source_name = "datasets/processed/vegetation/source.json"
                    self._files([source_name])
                    source = read_json(local_path(self.paths, source_name))
                    if (
                        source.get("extension_contract_sha256")
                        != registration.record["contract_sha256"]
                        or source.get("vegetation_snapshot_sha256", "").encode()
                        != metadata.get(b"vegetation_snapshot_sha256")
                        or source.get("no_labels_accessed") is not True
                        or source.get("cells_sha256")
                        != self.hashes["datasets/processed/cells.parquet"]
                    ):
                        raise ValueError("Vegetation source manifest provenance mismatch")
                    source_manifests[source_name] = {
                        "sha256": self.hashes[source_name],
                        "acquisition_buffer_degrees": source.get("acquisition_buffer_degrees"),
                        "contract_buffer_degrees": source.get("contract_buffer_degrees"),
                    }
                if any(metadata.get(key) != value for key, value in required.items()):
                    raise ValueError(
                        f"Feature snapshot metadata differs from registered run: {identity}"
                    )
                if not (
                    metadata.get(b"vegetation_snapshot_sha256")
                    or metadata.get(b"land_cover_snapshot_sha256")
                ):
                    raise ValueError("Feature snapshot lacks dependency provenance")
            self.available[identity] = {
                **entry,
                "audit_evidence": {
                    **audit,
                    "source_manifests": source_manifests,
                },
            }
        feature_files = [
            local_path(self.paths, name)
            for entry in self.available.values()
            for name in entry.get("feature_files", [])
        ]
        self.input_bytes = owned_bytes(feature_files)
        cache = self.paths.root / "artifacts/.cache/extension"
        cache.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(
            prefix="matrix_", dir=cache, ignore_cleanup_errors=True
        )
        self.arrays: list[np.memmap] = []

    def _files(self, names: list[str]) -> list[Path]:
        if not names or any(name not in self.hashes for name in names):
            raise ValueError("Every extension input must have a frozen checksum")
        return [local_path(self.paths, name) for name in names]

    def validate(self) -> None:
        check_hashes(self.registration, self.hashes)

    def close(self) -> None:
        for array in self.arrays:
            array.flush()
            array._mmap.close()
        self.arrays.clear()
        self.temporary.cleanup()

    def _rain_source(self, year: int) -> Path:
        from bolivia_fire.covariates.chirps import PRODUCT, REFERENCE_RULE

        relative = f"datasets/processed/covariates/chirps_features/year={year}/part.parquet"
        manifest_name = "datasets/processed/covariates/chirps_manifest.jsonl"
        path, _manifest = self._files([relative, manifest_name])
        schema = pq.read_schema(path)
        metadata = schema.metadata or {}
        if (
            not set(self.rain + self.rain_flags + ["cell_id", "issue_date"]).issubset(schema.names)
            or metadata.get(b"product") != PRODUCT.encode()
            or metadata.get(b"protocol_sha256")
            != self.registration.record["protocol_sha256"].encode()
            or metadata.get(b"reference_rule") != REFERENCE_RULE.encode()
            or metadata.get(b"manifest_sha256") != self.hashes[manifest_name].encode()
            or json.loads(metadata.get(b"train_years", b"null")) != list(range(2013, 2021))
        ):
            raise ValueError(f"CHIRPS provenance preflight failed for year {year}")
        return path

    def _rain_join(self, table: pa.Table) -> pa.Table:
        pieces = []
        for year in pc.year(table["issue_date"]).unique().to_pylist():
            path = self._rain_source(year)
            condition = ds.field("issue_date").isin(table["issue_date"].unique().to_pylist())
            pieces.append(
                ds.dataset(path, format="parquet").to_table(
                    columns=["cell_id", "issue_date", *self.rain, *self.rain_flags],
                    filter=condition,
                )
            )
        return self._ordered_join(table, pa.concat_tables(pieces))

    @staticmethod
    def _ordered_join(table: pa.Table, features: pa.Table) -> pa.Table:
        for name in ("cell_id", "issue_date"):
            position = features.schema.get_field_index(name)
            features = features.set_column(
                position, name, pc.cast(features[name], table.schema.field(name).type, safe=True)
            )
        if features.select(["cell_id", "issue_date"]).to_pandas().duplicated().any():
            raise ValueError("Duplicate cell-issue feature keys")
        table = table.append_column("extension_row_order", pa.array(np.arange(len(table))))
        return (
            table.join(features, keys=["cell_id", "issue_date"], join_type="left outer")
            .sort_by("extension_row_order")
            .drop(["extension_row_order"])
        )

    def _feature_join(self, table: pa.Table, run: dict[str, Any], extra: list[str]) -> pa.Table:
        if not extra:
            return table
        entry = self.available[run["run_id"]]
        issue = pd.to_datetime(table["issue_date"].to_numpy(), utc=True)
        years = set(issue.year)
        groups: dict[tuple[str, ...], list[pa.Table]] = {}
        for path in self._files(entry["feature_files"]):
            dataset = ds.dataset(path, format="parquet")
            available = tuple(name for name in extra if name in dataset.schema.names)
            if not available:
                continue
            pieces = groups.setdefault(available, [])
            date_filter = pc.year(ds.field("issue_date")).isin(list(years)) & ds.field(
                "issue_date"
            ).isin(table["issue_date"].unique().to_pylist())
            for batch in dataset.to_batches(
                columns=["cell_id", "issue_date", *available],
                filter=date_filter,
                batch_size=100_000,
            ):
                pieces.append(pa.Table.from_batches([batch]))
        for names, pieces in groups.items():
            if pieces:
                if set(names) & set(table.column_names):
                    raise ValueError("Overlapping feature snapshot definitions")
                table = self._ordered_join(table, pa.concat_tables(pieces))
        for name in extra:
            if name not in table.column_names:
                table = table.append_column(name, pa.nulls(len(table), type=pa.float32()))
        return table

    def load(
        self,
        run: dict[str, Any],
        stage: str,
        resources: Resources,
        *,
        labels: bool = True,
        opening: Opening | None = None,
        holdout: bool = False,
    ) -> PanelData:
        self.validate()
        if stage not in {"train", "val_select", "val_calib", "test", "confirmation"}:
            raise ValueError("Unknown extension partition")
        if labels and stage in {"test", "confirmation"}:
            verify_opening(self.registration, opening, stage)
        if holdout and stage not in {"test", "confirmation"}:
            raise ValueError("Held-out labels cannot enter fitting, selection or calibration")
        names = run["ordered_features"]
        extra = [name for name in names if name.startswith(("veg_", "mapbiomas_"))]
        base = list(
            dict.fromkeys(
                [
                    *META_COLUMNS,
                    "window_obs_complete_28d",
                    *[name for name in names if name not in self.rain + extra],
                ]
            )
        )
        if labels:
            base.append("y_primary")
        condition = ds.field("partition") == stage
        if stage == "confirmation":
            condition = (ds.field("issue_date") >= pd.Timestamp("2026-01-05").date()) & (
                ds.field("issue_date") <= pd.Timestamp("2026-07-20").date()
            )
        counts = dict.fromkeys(
            (
                "scanned",
                "purged",
                "missing_label",
                "wrong_geography",
                "incomplete_recent_history",
                "missing_chirps_or_flags",
                "missing_features",
                "eligible",
            ),
            0,
        )

        for path in self.panel_files:
            dataset = ds.dataset(path, format="parquet")
            missing = set(base) - set(dataset.schema.names)
            if missing:
                raise ValueError(f"Panel preflight columns missing: {sorted(missing)}")
            counts["purged"] += dataset.count_rows(filter=ds.field("partition").is_null())
            for batch in dataset.to_batches(
                columns=["issue_date"], filter=condition, batch_size=100_000
            ):
                for year in pc.year(batch.column(0)).unique().to_pylist():
                    self._rain_source(year)

        def batches(count: bool):
            for path in self.panel_files:
                dataset = ds.dataset(path, format="parquet")
                for batch in dataset.to_batches(columns=base, filter=condition, batch_size=100_000):
                    resources.check(projected_ram=max(1, len(batch)) * (len(names) + 20) * 32)
                    table = pa.Table.from_batches([batch])
                    table = self._rain_join(table)
                    table = self._feature_join(table, run, extra)
                    y = (
                        table["y_primary"].to_numpy().astype(float)
                        if labels
                        else np.full(len(table), np.nan)
                    )
                    held = pc.fill_null(table["geo_holdout"], True).to_numpy()
                    complete = pc.fill_null(table["window_obs_complete_28d"], False).to_numpy()
                    rain_ok = np.ones(len(table), dtype=bool)
                    for name in self.rain:
                        rain_ok &= np.isfinite(table[name].to_numpy().astype(float))
                    for name in self.rain_flags:
                        rain_ok &= pc.fill_null(table[name], False).to_numpy()
                    features_ok = np.ones(len(table), dtype=bool)
                    feature_failures = {}
                    for name in names:
                        finite = np.isfinite(table[name].to_numpy().astype(float))
                        features_ok &= finite
                        feature_failures[name] = ~finite
                    quality_ok = np.ones(len(table), dtype=bool)
                    fresh_ok = quality_ok.copy()
                    if "veg_valid_pixel_fraction" in names:
                        fraction = table["veg_valid_pixel_fraction"].to_numpy().astype(float)
                        age = table["veg_observation_age_days"].to_numpy().astype(float)
                        quality_ok = (fraction >= 0.5) & (fraction <= 1)
                        fresh_ok = (age >= 0) & (age <= 64)
                    land_ok = np.ones(len(table), dtype=bool)
                    land = [name for name in names if name.startswith("mapbiomas_")]
                    if land:
                        fractions = np.column_stack(
                            [table[name].to_numpy().astype(float) for name in land]
                        )
                        land_ok = ((fractions >= 0) & (fractions <= 1)).all(axis=1)
                        land_ok &= np.abs(fractions.sum(axis=1) - 1) <= 1e-6
                    mask = np.ones(len(table), dtype=bool)
                    rules = [
                        ("missing_label", np.isfinite(y) if labels else mask.copy()),
                        ("wrong_geography", held == holdout),
                        ("incomplete_recent_history", complete),
                        ("missing_chirps_or_flags", rain_ok),
                        ("failed_qa_or_missing_coverage", quality_ok),
                        ("stale_or_missing_observation", fresh_ok),
                        ("invalid_land_cover_fractions", land_ok),
                        ("missing_features", features_ok),
                    ]
                    if count:
                        counts["scanned"] += len(table)
                    for key, valid in rules:
                        if count:
                            counts[key] = counts.get(key, 0) + int((mask & ~valid).sum())
                            if key == "missing_features":
                                for name, unavailable in feature_failures.items():
                                    field = f"missing_feature_{name}"
                                    counts[field] = counts.get(field, 0) + int(
                                        (mask & unavailable).sum()
                                    )
                        mask &= valid
                    if labels and not np.isin(y[mask], [0, 1]).all():
                        raise ValueError("Extension panel contains nonbinary labels")
                    if stage == "confirmation":
                        dates = pd.to_datetime(table["issue_date"].to_numpy(), utc=True)
                        if (dates.dayofweek != 0).any() or (dates != dates.normalize()).any():
                            raise ValueError("Confirmation issue calendar differs from contract")
                    if count:
                        counts["eligible"] += int(mask.sum())
                    yield table.filter(pa.array(mask)), y[mask]

        for _ in batches(True):
            pass
        n = counts["eligible"]
        size = n * len(names) * 4
        resources.check(projected_disk=size, projected_ram=n * 100)
        if n:
            x = np.memmap(
                Path(self.temporary.name) / f"{len(self.arrays)}.f32",
                mode="w+",
                shape=(n, len(names)),
                dtype=np.float32,
            )
            self.arrays.append(x)
        else:
            x = np.empty((0, len(names)), dtype=np.float32)
        y = np.empty(n, dtype=np.float32)
        metadata, offset = [], 0
        for table, target in batches(False):
            stop = offset + len(table)
            for index, name in enumerate(names):
                x[offset:stop, index] = table[name].to_numpy().astype(np.float32)
            y[offset:stop] = target
            metadata.append(table.select(META_COLUMNS))
            offset = stop
        meta = (
            pa.concat_tables(metadata)
            if metadata
            else pa.table({name: [] for name in META_COLUMNS})
        )
        return PanelData(x, y, meta, names, counts)
