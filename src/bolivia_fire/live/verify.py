from __future__ import annotations

import csv
import io
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from bolivia_fire.evaluation.metrics import metrics
from bolivia_fire.firms.storage import durable_write

TRACK_COLUMNS = (
    "issue_date",
    "n_cells",
    "positives",
    "ap",
    "brier",
    "roc_auc",
    "recall_area_5",
    "recall_area_10",
    "ece",
    "climatology_ap",
    "prospective",
    "generated_utc",
    "publication_lag_hours",
    "notes",
)


def verification_row(
    cells: pd.DataFrame,
    forecast: dict[str, Any],
    observed: dict[str, Any],
    climatology: NDArray,
) -> dict[str, Any] | None:
    if not observed["complete"]:
        return None
    if forecast["cell_index"] != observed["cell_index"]:
        raise ValueError("Forecast and observation cells are not aligned")
    positions = np.asarray(forecast["cell_index"], dtype=int)
    probability = np.asarray(forecast["p"], dtype=float)
    labels = np.asarray(observed["y"], dtype=float)
    area = cells.iloc[positions].national_area_km2.to_numpy()
    result = metrics(labels, probability, area=area, issue=np.zeros(len(labels)))
    baseline = np.asarray(climatology, dtype=float)[positions]
    baseline_ap = None
    if np.isfinite(baseline).all():
        baseline_ap = metrics(labels, np.clip(baseline, 1e-6, 1 - 1e-6))["average_precision"]
    reasons = result["undefined_reasons"]
    notes = "; ".join(f"{key}: {value}" for key, value in reasons.items())
    if baseline_ap is None:
        notes += "; climatology_ap: unavailable or single class"
    if not forecast.get("prospective", False):
        notes += (
            "; backfill: generated after issue time using only inputs acquired before the issue"
        )
    notes += "; climatology uses department fallback for cells without training cell climatology"
    return {
        "issue_date": forecast["issue_date"],
        "n_cells": len(labels),
        "positives": int(labels.sum()),
        "ap": result["average_precision"],
        "brier": result["brier_score"],
        "roc_auc": result["roc_auc"],
        "recall_area_5": result["recall_at_area_budget_5pct"],
        "recall_area_10": result["recall_at_area_budget_10pct"],
        "ece": result["expected_calibration_error"],
        "climatology_ap": baseline_ap,
        "prospective": forecast.get("prospective", False),
        "generated_utc": forecast.get("generated_utc", ""),
        "publication_lag_hours": forecast.get("publication_lag_hours", ""),
        "notes": notes.strip("; "),
    }


def read_track(out: Path) -> list[dict[str, str]]:
    path = out / "verification/track_record.csv"
    return list(csv.DictReader(io.StringIO(path.read_text()))) if path.exists() else []


def record_verification(out: Path, issue: str, row: dict[str, Any] | None) -> None:
    rows: list[dict[str, Any]] = list(read_track(out))
    rows = [old for old in rows if old["issue_date"] != issue]
    if row is not None:
        rows.append(row)
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=TRACK_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(sorted(rows, key=lambda item: item["issue_date"]))
    path = out / "verification/track_record.csv"
    temporary = path.with_suffix(".csv.part")
    durable_write(temporary, stream.getvalue().encode())
    os.replace(temporary, path)
