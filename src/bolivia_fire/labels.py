from __future__ import annotations

import pandas as pd

from bolivia_fire.detections import selection_mask


def target_rows(detections: pd.DataFrame, issue: str | pd.Timestamp) -> pd.DataFrame:
    start = pd.Timestamp(issue)
    start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
    return detections[
        detections.in_bolivia
        & (detections.acq_datetime >= start)
        & (detections.acq_datetime < start + pd.Timedelta(days=7))
    ]


def labels_for_issue(
    cells: pd.DataFrame,
    detections: pd.DataFrame,
    acquisition_days: pd.DataFrame,
    issue: str | pd.Timestamp,
) -> pd.DataFrame:
    start = pd.Timestamp(issue).tz_localize(None).normalize()
    target = target_rows(detections, start)
    days = acquisition_days.set_index("date").reindex(pd.date_range(start, periods=7))
    complete = bool((days.acquired.fillna(False) & days.observed.fillna(False)).all())
    low_coverage = bool(
        ((days.bbox_detection_count.fillna(0) == 0) & days.index.month.isin(range(6, 12))).any()
    )
    result = cells[["cell_id"]].reset_index(drop=True).copy()
    for variant, all_confidence in (("primary", False), ("allconf", True)):
        counts = target[
            selection_mask(target, all_confidence=all_confidence)
        ].cell_id.value_counts()
        result[f"n_{variant}"] = result.cell_id.map(counts).fillna(0).astype("int32")
        result[f"y_{variant}"] = (result[f"n_{variant}"] > 0).astype("Int8")
        if not complete:
            result[f"y_{variant}"] = pd.array([pd.NA] * len(result), dtype="Int8")
    result["target_complete"] = complete
    result["target_low_coverage"] = low_coverage
    return result


def explain_label(
    detections: pd.DataFrame, cell: str, issue: str | pd.Timestamp, *, all_confidence: bool = False
) -> pd.DataFrame:
    target = target_rows(detections, issue)
    return target[(target.cell_id == cell) & selection_mask(target, all_confidence=all_confidence)]
