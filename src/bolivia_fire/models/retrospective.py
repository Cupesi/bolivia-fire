from __future__ import annotations

import json

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from numpy.typing import NDArray

from bolivia_fire.config import Paths, load_protocol
from bolivia_fire.covariates import join_retrospective
from bolivia_fire.firms.storage import sha256_file

COMPLETENESS = {
    "rain_sum_7d": ["rain_complete_7d"],
    "rain_sum_30d": ["rain_complete_30d"],
    "rain_sum_90d": ["rain_complete_90d"],
    "dry_days_30d": ["rain_complete_30d"],
    "rain_anomaly_90d": ["rain_complete_90d", "rain_climatology_complete"],
}


class RetrospectiveUnavailable(ValueError):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"Retrospective inputs unavailable: {detail} ({reason})")
        self.reason = reason


def completeness_columns(features: list[str]) -> list[str]:
    unsupported = sorted(set(features) - set(COMPLETENESS))
    if unsupported:
        raise RetrospectiveUnavailable(
            "retrospective_provenance_invalid", f"unrecognized CHIRPS features: {unsupported}",
        )
    return sorted({flag for name in features for flag in COMPLETENESS[name]})


def join_verified(table: pa.Table, paths: Paths, features: list[str]) -> pa.Table:
    from bolivia_fire.covariates.chirps import PRODUCT

    flags = completeness_columns(features)
    root = paths.datasets / "processed/covariates"
    protocol = load_protocol(paths.root)
    first, last = (int(day[:4]) for day in protocol["partitions"]["train"])
    for year in pc.year(table["issue_date"]).unique().to_pylist():
        source = root / "chirps_features" / f"year={year}" / "part.parquet"
        if not source.exists():
            raise RetrospectiveUnavailable(
                "retrospective_columns_missing", f"CHIRPS year {year} is missing: {features}",
            )
        schema = pq.read_schema(source)
        missing = sorted(set([*features, *flags]) - set(schema.names))
        if missing:
            raise RetrospectiveUnavailable(
                "retrospective_columns_missing", f"CHIRPS columns are missing: {missing}",
            )
        metadata = schema.metadata or {}
        manifest = root / "chirps_manifest.jsonl"
        try:
            valid = (
                metadata.get(b"product") == PRODUCT.encode()
                and metadata.get(b"protocol_sha256")
                == sha256_file(paths.root / "configs/protocol.yaml").encode()
                and json.loads(metadata.get(b"train_years", b"null"))
                == list(range(first, last + 1))
                and metadata.get(b"reference_rule")
                == b"training years only, reference window end <= issue"
                and manifest.exists()
                and metadata.get(b"manifest_sha256") == sha256_file(manifest).encode()
            )
        except (ValueError, UnicodeError):
            valid = False
        if not valid:
            raise RetrospectiveUnavailable(
                "retrospective_provenance_invalid", f"CHIRPS year {year} provenance is invalid",
            )
    try:
        joined = join_retrospective(table, root=root)
    except ValueError as exc:
        raise RetrospectiveUnavailable("retrospective_provenance_invalid", str(exc)) from None
    return joined


def require_complete(table: pa.Table, features: list[str], eligible: NDArray) -> None:
    for name in features:
        values = table[name].to_numpy(zero_copy_only=False)[eligible]
        if not np.isfinite(values.astype(float)).all():
            raise RetrospectiveUnavailable(
                "retrospective_covariates_incomplete",
                f"CHIRPS {name} is incomplete for eligible rows",
            )
    for name in completeness_columns(features):
        values = pc.fill_null(table[name], False).to_numpy(zero_copy_only=False)[eligible]
        if not np.equal(values, True).all():
            raise RetrospectiveUnavailable(
                "retrospective_covariates_incomplete",
                f"CHIRPS {name} is incomplete for eligible rows",
            )
