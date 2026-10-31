from __future__ import annotations

import gc
import tempfile
from dataclasses import dataclass
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import yaml
from numpy.typing import NDArray

from bolivia_fire.comparison.runtime import Study, log, read_json
from bolivia_fire.evaluation.artifacts import atomic_json
from bolivia_fire.evaluation.metrics import metrics
from bolivia_fire.models.data import META_COLUMNS, PanelStore


@dataclass
class Split:
    name: str
    x: NDArray
    y: NDArray
    metadata: pa.Table
    exclusions: dict[str, int]

    def column(self, name: str) -> NDArray:
        return self.metadata[name].to_numpy(zero_copy_only=False)

    @property
    def issue(self) -> NDArray:
        return self.column("issue_date")

    @property
    def area(self) -> NDArray:
        return self.column("national_area_km2")

    def score(self, probability: NDArray) -> dict[str, Any]:
        return metrics(self.y, probability, area=self.area, issue=self.issue)


class ComparisonPanelStore(PanelStore):
    def __init__(self, study: Study) -> None:
        # Inherit all eligibility and ordering; isolate only the temporary matrix location.
        self.paths = study.paths
        config = yaml.safe_load((self.paths.root / "configs/experiments.yaml").read_text("utf-8"))
        self.columns = config["columns"]
        self.minimum_free_bytes = int(study.config["minimum_free_gib"] * 1024**3)
        self.meta_columns = list(dict.fromkeys([*META_COLUMNS, *self.columns.values()]))
        self.files = sorted((self.paths.datasets / "processed/panel").glob("year=*/part.parquet"))
        if not self.files:
            raise ValueError("No panel partitions found")
        self.temporary = tempfile.TemporaryDirectory(
            prefix="matrix_",
            dir=study.cache / "tmp",
            ignore_cleanup_errors=True,
        )
        self.arrays: list[np.memmap] = []
        self.serial = 0


def load_split(study: Study, name: str, *, opening: dict[str, Any] | None = None) -> Split:
    if name not in {"train", "val_select", "val_calib", "test"}:
        raise ValueError("Unknown comparison partition")
    if name == "test" and (opening is None or opening.get("type") != "post_hoc_comparison"):
        raise ValueError("Test rows require a recorded post-hoc opening")
    directory = study.cache / "data"
    complete = directory / f"{name}_exclusions.json"
    if not complete.exists():
        with ComparisonPanelStore(study) as store:
            data = store.load([name], study.features, "y_primary")
            reference = read_json(study.paths.root / "artifacts/evaluation/selection_strict.json")
            if name in reference["exclusions"] and data.exclusions != reference["exclusions"][name]:
                raise ValueError(f"Comparison eligibility differs from recorded benchmark: {name}")
            np.save(directory / f"{name}_x.npy", data.x)
            np.save(directory / f"{name}_y.npy", data.y)
            pq.write_table(data.metadata, directory / f"{name}_metadata.parquet")
            atomic_json(complete, data.exclusions)
            log(f"Loaded {name}: {data.exclusions}")
            del data
        gc.collect()
    return Split(
        name,
        np.load(directory / f"{name}_x.npy", mmap_mode="r"),
        np.load(directory / f"{name}_y.npy", mmap_mode="r"),
        pq.read_table(directory / f"{name}_metadata.parquet"),
        read_json(complete),
    )
