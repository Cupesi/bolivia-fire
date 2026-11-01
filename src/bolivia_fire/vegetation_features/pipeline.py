from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import rasterio
import shapely
import yaml
from filelock import FileLock

from bolivia_fire.config import Paths
from bolivia_fire.covariates.storage import atomic_json
from bolivia_fire.firms.storage import sha256_file
from bolivia_fire.grid import load_boundary_geometries
from bolivia_fire.vegetation_features.catalog import discover, fingerprint
from bolivia_fire.vegetation_features.pixels import read_pixels
from bolivia_fire.vegetation_features.resources import Resources
from bolivia_fire.vegetation_features.temporal import FEATURES, CompositeSeries, seasonal_reference
from bolivia_fire.vegetation_features.weights import WeightMap, aggregate, build_weights


def cached_table(
    path: Path,
    dependency: str,
    build: Callable[[], pd.DataFrame | Iterable[pd.DataFrame]],
    *,
    metadata: dict[bytes, bytes] | None = None,
) -> bool:
    receipt = path.with_suffix(".json")
    if path.exists() and receipt.exists():
        previous = json.loads(receipt.read_text(encoding="utf-8"))
        if sha256_file(path) != previous["sha256"]:
            raise ValueError("Vegetation checkpoint checksum mismatch")
        if previous["dependency"] == dependency:
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".part")
    frames = build()
    frames = [frames] if isinstance(frames, pd.DataFrame) else frames
    writer = None
    rows = 0
    try:
        for frame in frames:
            table = pa.Table.from_pandas(frame, preserve_index=False)
            table = table.replace_schema_metadata(
                {**(table.schema.metadata or {}), **(metadata or {})}
            )
            if writer is None:
                writer = pq.ParquetWriter(temporary, table.schema, compression="zstd")
            writer.write_table(table, row_group_size=100_000)
            rows += len(frame)
    finally:
        if writer is not None:
            writer.close()
    if writer is None:
        raise ValueError("No vegetation output rows")
    temporary.replace(path)
    atomic_json(
        receipt,
        {
            "dependency": dependency,
            "sha256": sha256_file(path),
            "rows": rows,
            "bytes": path.stat().st_size,
        },
    )
    return True


def _source_code_hash() -> str:
    return fingerprint({p.name: sha256_file(p) for p in Path(__file__).parent.glob("*.py")})


def build(root: Path, *, through_year: int | None = None) -> dict[str, Any]:
    output = root / "datasets" / "processed" / "vegetation"
    output.mkdir(parents=True, exist_ok=True)
    with FileLock(str(output / "build.lock"), timeout=0), Resources(output) as resources:
        return _build(root, output, resources, through_year)


def _build(
    root: Path, output: Path, resources: Resources, through_year: int | None
) -> dict[str, Any]:
    tick = time.perf_counter()
    contract_path = root / "configs" / "extension_vegetation.yaml"
    contract_hash = sha256_file(contract_path)
    contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    if (
        contract["vegetation"]["quality"]["min_valid_pixel_area_fraction"] != 0.5
        or contract["vegetation"]["temporal"]["availability_lag_days"] != 16
    ):
        raise ValueError("Vegetation implementation does not match the registered contract")
    composites, source = discover(root)
    if through_year is not None:
        composites = [c for c in composites if c.start.year <= through_year]
    if not composites:
        raise ValueError("No downloaded vegetation composites in selected years")
    cells_path = root / "datasets" / "processed" / "cells.parquet"
    cells = pd.read_parquet(cells_path).reset_index(drop=True)
    if cells.cell_id.duplicated().any():
        raise ValueError("Duplicate vegetation grid cell IDs")
    country, _ = load_boundary_geometries(Paths(root))
    boxes = shapely.box(cells.west, cells.south, cells.east, cells.north)
    shapely.prepare(country)
    parts = boxes.copy()
    crossing = ~shapely.covers(country, boxes)
    parts[crossing] = shapely.intersection(boxes[crossing], country)
    source.update(
        {
            "extension_contract_sha256": contract_hash,
            "cells_sha256": sha256_file(cells_path),
            "implementation_sha256": _source_code_hash(),
            "no_labels_accessed": True,
        }
    )
    with rasterio.open(composites[0].files["ndvi"]) as src:
        grid = {"crs_wkt": src.crs.to_wkt(), "shape": src.shape, "transform": list(src.transform)}
        expected_grid = (src.shape, tuple(src.transform), src.crs.to_wkt())
    weight_identity = fingerprint(
        {
            **grid,
            "cells": source["cells_sha256"],
            "country_wkb": fingerprint(country.wkb_hex),
            "method": sha256_file(Path(__file__).with_name("weights.py")),
        }
    )
    weight_path = output / "weights" / f"{weight_identity}.npz"
    weight_path.parent.mkdir(exist_ok=True)
    weight_receipt = weight_path.with_suffix(".json")
    if weight_path.exists() and weight_receipt.exists():
        record = json.loads(weight_receipt.read_text(encoding="utf-8"))
        if record["sha256"] != sha256_file(weight_path):
            raise ValueError("Vegetation weight cache checksum mismatch")
        weights = WeightMap.load(weight_path)
    else:
        print(
            f"Vegetation: computing national intersection weights for {len(cells)} cells",
            flush=True,
        )
        weights = build_weights(composites[0].files["ndvi"], parts, checkpoint=resources.check)
        resources.check(256 * 1024**2)
        weights.save(weight_path)
        sums = np.bincount(weights.cell, weights.area, minlength=len(cells))
        atomic_json(
            weight_receipt,
            {
                "sha256": sha256_file(weight_path),
                "grid": grid,
                "cells": len(cells),
                "intersections": len(weights.area),
                "maximum_relative_national_area_error": float(
                    np.max(abs(sums / weights.denominator - 1))
                ),
                "cells_with_uncovered_national_area_above_tolerance": int(
                    (sums / weights.denominator < 1 - 1e-5).sum()
                ),
                "border_cells": int((cells.national_fraction < 0.999999).sum()),
            },
        )
    source["weights"] = {
        "path": weight_path.relative_to(output).as_posix(),
        **json.loads(weight_receipt.read_text(encoding="utf-8")),
    }
    arrays = {
        name: np.empty((len(composites), len(cells)), dtype=dtype)
        for name, dtype in (
            ("ndvi", "float32"),
            ("evi", "float32"),
            ("valid_fraction", "float32"),
            ("oldest_day", "int32"),
            ("latest_day", "int32"),
        )
    }
    report: dict[str, Any] = {
        "composites_written": 0,
        "composites_reused": 0,
        "features_written": 0,
        "features_reused": 0,
        "aggregate_coverage": [],
    }
    for index, composite in enumerate(composites):
        resources.check(256 * 1024**2)
        path = output / "composites" / f"{composite.key}.parquet"
        dependency = fingerprint(
            {
                "layers": composite.hashes,
                "weights": weight_identity,
                "code": fingerprint(
                    {
                        name: sha256_file(Path(__file__).with_name(name))
                        for name in ("pixels.py", "weights.py")
                    }
                ),
                "period": [str(composite.start), str(composite.end)],
                "contract": contract_hash,
            }
        )

        def calculate(c=composite, p=path) -> pd.DataFrame:
            pixels = read_pixels(
                c.files, str(c.start.date()), str(c.end.date()), expected_grid=expected_grid
            )
            result = pd.DataFrame(
                aggregate(weights, pixels.ndvi, pixels.evi, pixels.accepted, pixels.days)
            )
            result.insert(0, "cell_id", cells.cell_id)
            atomic_json(p.with_suffix(".audit.json"), pixels.audit)
            return result

        written = cached_table(path, dependency, calculate)
        report["composites_written" if written else "composites_reused"] += 1
        frame = pd.read_parquet(path)
        if not frame.cell_id.equals(cells.cell_id):
            raise ValueError("Composite cell ordering differs from the grid")
        for name, values in arrays.items():
            values[index] = frame[name]
        report["aggregate_coverage"].append(
            {
                "start": str(composite.start.date()),
                "year": composite.start.year,
                "month": composite.start.month,
                "cells": len(cells),
                "coverage_valid_cells": int(np.isfinite(frame.ndvi).sum()),
                "mean_valid_area_fraction": float(frame.valid_fraction.mean()),
            }
        )
        print(f"Vegetation: {composite.key} {'written' if written else 'reused'}", flush=True)
    series = CompositeSeries(
        pd.DatetimeIndex([c.start for c in composites]),
        pd.DatetimeIndex([c.end for c in composites]),
        arrays["ndvi"],
        arrays["evi"],
        arrays["valid_fraction"],
        arrays["oldest_day"],
        arrays["latest_day"],
        np.array([c.confirmation_ok for c in composites]),
    )
    snapshot = fingerprint(
        {
            "aggregates": {
                c.key: sha256_file(output / "composites" / f"{c.key}.parquet") for c in composites
            },
            "contract": contract_hash,
            "cells": source["cells_sha256"],
            "code": source["implementation_sha256"],
        }
    )
    source["vegetation_snapshot_sha256"] = snapshot
    atomic_json(output / "source.json", source)
    report["feature_coverage"] = []
    years = sorted({c.start.year for c in composites if 2013 <= c.start.year <= 2026})
    references: dict[tuple, tuple[np.ndarray, np.ndarray]] = {}
    for year in years:
        issues = pd.date_range(
            max(pd.Timestamp(year, 1, 1), pd.Timestamp("2013-01-07")),
            min(pd.Timestamp(year, 12, 31), pd.Timestamp("2026-07-20")),
            freq="W-MON",
        )
        for lag in (16, 32):
            for geography in ("temporal", "geographic"):
                resources.check(256 * 1024**2)
                path = (
                    output / "features" / f"lag={lag}" / f"geography={geography}" / f"year={year}"
                )
                metadata = {
                    b"extension_contract_sha256": contract_hash.encode(),
                    b"vegetation_snapshot_sha256": snapshot.encode(),
                    b"vegetation_lag_days": str(lag).encode(),
                    b"vegetation_geography": geography.encode(),
                    b"vegetation_source_manifest": b"source.json",
                }

                def issue_frames(y=year, lag_days=lag, geo=geography, dates=issues):
                    for issue in dates:
                        resources.check()
                        frame = series.for_issue(issue, lag_days)
                        for index in frame.composite_index.unique():
                            if index < 0:
                                continue
                            slot = min((series.starts[index].dayofyear - 1) // 16, 22)
                            chosen = frame.composite_index.to_numpy() == index
                            for name, values in (("ndvi", series.ndvi), ("evi", series.evi)):
                                key = y, slot, geo, name
                                if key not in references:
                                    references[key] = seasonal_reference(
                                        series.starts, values, cells, y, slot, geography=geo
                                    )
                                means, _ = references[key]
                                frame.loc[chosen, f"veg_{name}_seasonal_anomaly"] = (
                                    values[index, chosen] - means[chosen]
                                ).astype(np.float32)
                        frame.insert(0, "cell_id", cells.cell_id)
                        frame.insert(1, "issue_date", issue)
                        frame["vegetation_available"] = np.isfinite(frame[FEATURES]).all(axis=1)
                        frame["vegetation_lag_days"] = np.int16(lag_days)
                        yield frame

                written = cached_table(
                    path / "part.parquet",
                    fingerprint([snapshot, year, lag, geography]),
                    issue_frames,
                    metadata=metadata,
                )
                report["features_written" if written else "features_reused"] += 1
                frame = pd.read_parquet(
                    path / "part.parquet",
                    columns=[
                        "issue_date",
                        "veg_ndvi_latest",
                        "veg_ndvi_seasonal_anomaly",
                        "vegetation_available",
                    ],
                )
                frame["month"] = frame.issue_date.dt.month
                for month, subset in frame.groupby("month"):
                    report["feature_coverage"].append(
                        {
                            "year": year,
                            "month": int(month),
                            "lag": lag,
                            "geography": geography,
                            "rows": len(subset),
                            "latest_valid_rows": int(subset.veg_ndvi_latest.notna().sum()),
                            "anomaly_valid_rows": int(
                                subset.veg_ndvi_seasonal_anomaly.notna().sum()
                            ),
                            "complete_rows": int(subset.vegetation_available.sum()),
                            "missing_fraction": float(1 - subset.vegetation_available.mean()),
                        }
                    )
                print(
                    f"Vegetation: year {year}, lag {lag}, {geography} "
                    f"{'written' if written else 'reused'}",
                    flush=True,
                )
    report.update(
        {
            "runtime_seconds": time.perf_counter() - tick,
            **resources.record(),
            "output_bytes": sum(p.stat().st_size for p in output.rglob("*") if p.is_file()),
            "source_years": sorted({c.start.year for c in composites}),
            "feature_years": years,
            "vegetation_snapshot_sha256": snapshot,
            "contract_sha256": contract_hash,
            "cells": len(cells),
        }
    )
    atomic_json(output / "report.json", report)
    return report
