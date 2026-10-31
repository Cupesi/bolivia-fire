from __future__ import annotations

import shutil
import tempfile
import weakref
from collections.abc import Iterator
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import yaml
from numpy.typing import NDArray

from bolivia_fire.config import Paths
from bolivia_fire.models.retrospective import completeness_columns, join_verified, require_complete

META_COLUMNS = [
    "cell_id",
    "issue_date",
    "partition",
    "geo_holdout",
    "santa_cruz",
    "block_id",
    "national_area_km2",
    "count_last_7d",
]


def experiment_features(config: dict[str, Any], variant: str, drop_group: str | None) -> list[str]:
    names = list(config["strict_features"])
    if variant.startswith("retrospective"):
        names.extend(config["retrospective_features"])
    if variant.endswith("_geo"):
        names = [name for name in names if name != config["columns"]["climatology_cell"]]
    if drop_group:
        dropped = config["drop_groups"][drop_group]
        names = [name for name in names if name not in dropped]
    if config["columns"]["completeness_365d"] not in names:
        raise ValueError("The 365-day feature completeness flag is required")
    return list(dict.fromkeys(names))


@dataclass
class PanelData:
    x: NDArray
    y: NDArray
    metadata: pa.Table
    features: list[str]
    exclusions: dict[str, int]

    def column(self, name: str) -> NDArray:
        return self.metadata[name].to_numpy(zero_copy_only=False)

    def inputs(self, features: list[str]) -> NDArray:
        if features == self.features:
            return self.x
        if len(features) == 1 and features[0] in self.metadata.column_names:
            return self.column(features[0]).astype(np.float32).reshape(-1, 1)
        raise ValueError("Candidate feature definition does not match panel")


class PanelStore:
    def __init__(self, paths: Paths) -> None:
        self.paths = paths
        config = yaml.safe_load((paths.root / "configs/experiments.yaml").read_text())
        self.columns = config["columns"]
        self.minimum_free_bytes = int(config["minimum_free_gb"] * 1024**3)
        self.meta_columns = list(dict.fromkeys([*META_COLUMNS, *self.columns.values()]))
        self.files = sorted((paths.datasets / "processed" / "panel").glob("year=*/part.parquet"))
        if not self.files:
            raise ValueError("No panel partitions found; build the panel before training")
        cache = paths.root / "artifacts" / ".cache"
        cache.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(
            prefix="matrix_", dir=cache, ignore_cleanup_errors=True
        )
        self.arrays: list[np.memmap] = []
        self.serial = 0

    def __enter__(self) -> PanelStore:
        return self

    def __exit__(self, *args: object) -> None:
        for array in self.arrays:
            array.flush()
        self.arrays.clear()
        self.temporary.cleanup()

    def batches(
        self, splits: list[str], columns: list[str], *, retrospective: list[str] | None = None,
    ) -> Iterator[pa.Table]:
        condition = ds.field("partition").isin(splits)
        joined = set(retrospective or [])
        if retrospective:
            joined.update(completeness_columns(retrospective))
        projected = [name for name in columns if name not in joined]
        for path in self.files:
            dataset = ds.dataset(path, format="parquet")
            if dataset.count_rows(filter=condition) == 0:
                continue
            missing = sorted(set(projected) - set(dataset.schema.names))
            if missing:
                raise ValueError(f"Panel columns are missing: {', '.join(missing)}")
            for batch in dataset.to_batches(
                columns=projected, filter=condition, batch_size=100_000,
            ):
                table = pa.Table.from_batches([batch])
                if retrospective:
                    table = join_verified(table, self.paths, retrospective)
                yield table.select(columns)

    def load(
        self,
        splits: list[str],
        features: list[str],
        target: str,
        *,
        holdout: bool | None = False,
        require_labels: bool = True,
        exclude_low_coverage: bool = False,
        retrospective: list[str] | None = None,
    ) -> PanelData:
        columns = list(dict.fromkeys([*self.meta_columns, target, *features]))
        if retrospective:
            columns = list(dict.fromkeys([*columns, *completeness_columns(retrospective)]))
        counts = dict.fromkeys(
            [
                "scanned",
                "purged_partition",
                "missing_label",
                "geographic_holdout",
                "incomplete_28d",
                "target_low_coverage",
                "eligible",
            ],
            0,
        )
        purged = sum(
            ds.dataset(path, format="parquet").count_rows(filter=ds.field("partition").is_null())
            for path in self.files
        )
        counts["purged_partition"] = purged
        counts["scanned"] = purged

        def eligible(table: pa.Table, *, count: bool) -> NDArray:
            assigned = pc.is_valid(table["partition"]).to_numpy(zero_copy_only=False)
            labels = table[target].to_numpy(zero_copy_only=False).astype(float)
            valid_label = np.isfinite(labels) if require_labels else np.ones(len(table), dtype=bool)
            held = table["geo_holdout"].to_numpy(zero_copy_only=False)
            correct_holdout = (
                np.ones(len(table), dtype=bool) if holdout is None else held == holdout
            )
            complete = pc.fill_null(table[self.columns["completeness_28d"]], False).to_numpy(
                zero_copy_only=False
            )
            low_coverage = (
                pc.fill_null(table[self.columns["low_coverage"]], True).to_numpy(
                    zero_copy_only=False
                )
                if exclude_low_coverage
                else np.zeros(len(table), dtype=bool)
            )
            mask = assigned & valid_label & correct_holdout & complete & ~low_coverage
            if not np.isin(labels[mask & np.isfinite(labels)], [0, 1]).all():
                raise ValueError("Panel contains nonbinary labels")
            if count:
                counts["scanned"] += len(table)
                counts["missing_label"] += int((assigned & ~valid_label).sum())
                counts["geographic_holdout"] += int(
                    (assigned & valid_label & ~correct_holdout).sum()
                )
                counts["incomplete_28d"] += int(
                    (assigned & valid_label & correct_holdout & ~complete).sum()
                )
                counts["target_low_coverage"] += int(
                    (assigned & valid_label & correct_holdout & complete & low_coverage).sum()
                )
                counts["eligible"] += int(mask.sum())
            return mask

        for table in self.batches(splits, columns, retrospective=retrospective):
            mask = eligible(table, count=True)
            if retrospective:
                require_complete(table, retrospective, mask)
        n = counts["eligible"]
        needed = n * len(features) * np.dtype(np.float32).itemsize
        if shutil.disk_usage(self.paths.root).free - needed < self.minimum_free_bytes:
            raise ValueError(
                "Matrix allocation would leave less than the configured free disk reserve"
            )
        self.serial += 1
        if n:
            x = np.memmap(
                Path(self.temporary.name) / f"{self.serial}.f32",
                mode="w+",
                shape=(n, len(features)),
                dtype=np.float32,
            )
            self.arrays.append(x)
            weakref.finalize(x, _release_matrix, x._mmap, Path(x.filename))
        else:
            x = np.empty((0, len(features)), dtype=np.float32)
        targets = np.empty(n, dtype=np.float32)
        metadata = []
        offset = 0
        for table in self.batches(splits, columns, retrospective=retrospective):
            table = table.filter(pa.array(eligible(table, count=False)))
            stop = offset + len(table)
            for index, name in enumerate(features):
                values = table[name].to_numpy(zero_copy_only=False).astype(np.float32)
                if np.isinf(values).any():
                    raise ValueError(f"Infinite feature values: {name}")
                x[offset:stop, index] = values
            targets[offset:stop] = table[target].to_numpy(zero_copy_only=False)
            metadata.append(table.select(self.meta_columns))
            offset = stop
        if metadata:
            meta = pa.concat_tables(metadata)
        else:
            meta = pa.table({name: [] for name in self.meta_columns})
        return PanelData(x, targets, meta, features, counts)


def _release_matrix(mapping: Any, path: Path) -> None:
    mapping.close()
    path.unlink(missing_ok=True)
    with suppress(OSError):
        path.parent.rmdir()
