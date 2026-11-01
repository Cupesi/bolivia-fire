from __future__ import annotations

import hashlib
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
from numpy.typing import NDArray

from bolivia_fire.evaluation.bootstrap import block_bootstrap
from bolivia_fire.evaluation.metrics import metrics
from bolivia_fire.models.data import PanelData


def row_keys(data: PanelData) -> NDArray:
    days = pd.to_datetime(data.column("issue_date"), utc=True).strftime("%Y-%m-%d")
    return np.rec.fromarrays(
        [data.column("cell_id").astype(str), days.to_numpy(dtype=str)], names="cell,issue"
    )


def row_hash(data: PanelData) -> str:
    digest = hashlib.sha256()
    for cell, issue in row_keys(data):
        digest.update(f"{cell}\t{issue}\n".encode())
    return digest.hexdigest()


def subset(data: PanelData, indices: NDArray) -> PanelData:
    return PanelData(
        data.x[indices],
        data.y[indices],
        data.metadata.take(pa.array(indices)),
        data.features,
        data.exclusions,
    )


def common_support(panels: dict[str, PanelData]) -> tuple[dict[str, PanelData], dict[str, Any]]:
    keys = {name: row_keys(data) for name, data in panels.items()}
    for value in keys.values():
        if len(np.unique(value)) != len(value):
            raise ValueError("Duplicate cell-issue row keys")
    common = None
    for value in keys.values():
        common = np.unique(value) if common is None else np.intersect1d(common, value)
    aligned, counts = {}, {}
    for name, data in panels.items():
        order = np.argsort(keys[name])
        indices = order[np.searchsorted(keys[name][order], common)]
        aligned[name] = subset(data, indices)
        counts[name] = {
            "eligible_before_intersection": len(data.y),
            "lost_to_common_support": len(data.y) - len(indices),
            "paired_rows": len(indices),
            "row_key_sha256": row_hash(aligned[name]),
            "exclusions": data.exclusions,
        }
    if aligned:
        reference = next(iter(aligned.values())).y
        if any(not np.array_equal(reference, data.y, equal_nan=True) for data in aligned.values()):
            raise ValueError("Labels disagree on identical row keys")
    return aligned, counts


def decision(
    interval: dict[str, Any],
    *,
    calibration_status: str = "fitted",
    available: bool = True,
    reason: str | None = None,
) -> dict[str, Any]:
    if not available:
        return {"status": "not_evaluated", "reason": reason or "required_group_unavailable"}
    bounds = [interval.get("lower"), interval.get("upper")]
    positive = (
        calibration_status == "fitted"
        and all(value is not None and np.isfinite(value) for value in bounds)
        and 0 < bounds[0] <= bounds[1]
    )
    return {
        "status": "adds_information" if positive else "inconclusive",
        "reason": "finite_interval_above_zero"
        if positive
        else ("identity_calibration" if calibration_status != "fitted" else "no_detected_gain"),
    }


def paired_report(
    data: PanelData,
    probability: NDArray,
    baseline: NDArray,
    *,
    calibration_status: str,
    spatial: bool = False,
) -> dict[str, Any]:
    if probability.shape != data.y.shape or baseline.shape != data.y.shape:
        raise ValueError("Predictions must align with identical evaluation rows")
    if not np.isfinite(probability).all() or not np.isfinite(baseline).all():
        raise ValueError("Filter joint finite prediction support before comparison")
    dates = pd.to_datetime(data.column("issue_date"), utc=True)
    blocks = data.column("block_id") if spatial else dates.strftime("%Y-%m").to_numpy()
    area, issue = data.column("national_area_km2"), data.column("issue_date")
    boot = block_bootstrap(
        data.y,
        probability,
        blocks,
        baseline=baseline,
        area=area,
        issue=issue,
        replicates=500,
        seed=7,
    )
    return {
        "rows": len(data.y),
        "positives": int(data.y.sum()),
        "prevalence": float(data.y.mean()) if len(data.y) else None,
        "represented_months": len(np.unique(dates.strftime("%Y-%m"))),
        "row_key_sha256": row_hash(data),
        "calibrated": metrics(data.y, probability, area=area, issue=issue),
        "comparator": metrics(data.y, baseline, area=area, issue=issue),
        "bootstrap": boot,
        "decision": decision(
            boot["paired_difference"]["average_precision"], calibration_status=calibration_status
        ),
        "inference_scope": "fixed models and calibrators; marginal interval",
    }


def compare_lags(
    primary: PanelData,
    primary_p: NDArray,
    sensitivity: PanelData,
    sensitivity_p: NDArray,
    *,
    calibration_status: str,
    spatial: bool = False,
) -> dict[str, Any]:
    aligned, counts = common_support(
        {
            "primary": PanelData(primary_p[:, None], primary.y, primary.metadata, ["p"], {}),
            "sensitivity": PanelData(
                sensitivity_p[:, None], sensitivity.y, sensitivity.metadata, ["p"], {}
            ),
        }
    )
    return {
        "support": counts,
        "comparison": paired_report(
            aligned["primary"],
            aligned["sensitivity"].x[:, 0],
            aligned["primary"].x[:, 0],
            calibration_status=calibration_status,
            spatial=spatial,
        ),
        "role": "sensitivity_only; never selects a lag",
    }
