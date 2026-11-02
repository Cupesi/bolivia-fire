from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass
class ExtensionSection:
    tables: list[tuple[str, pd.DataFrame]] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)

    def add(self, title: str, rows: list[dict[str, Any]]) -> None:
        if rows:
            self.tables.append((title, pd.json_normalize(rows)))


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _cell_row(path: Path, cell_id: str, issue: pd.Timestamp | None = None) -> pd.DataFrame:
    filters = [("cell_id", "==", cell_id)]
    if issue is not None:
        filters.append(("issue_date", "==", issue.tz_localize(None)))
    result = pd.read_parquet(path, filters=filters)
    if len(result) > 1:
        raise ValueError(f"Duplicate example rows in {path.name}")
    return result


def _vegetation_details(
    section: ExtensionSection, root: Path, row: dict[str, Any], issue: pd.Timestamp,
) -> None:
    base = root / "datasets/processed/vegetation"
    end = pd.to_datetime(row.get("composite_end_boundary"), utc=True)
    oldest = pd.to_datetime(row.get("oldest_observation"), utc=True)
    lag = row.get("vegetation_lag_days")
    available = end + pd.Timedelta(days=lag) if lag is not None else pd.NaT
    section.add("Availability and observation age (UTC)", [{
        "issue_time": issue, "composite_start": row.get("composite_start"),
        "composite_end_boundary": end, "lag_days": lag, "available_from_utc": available,
        "available_by_issue": available <= issue if pd.notna(available) else None,
        "oldest_observation": oldest, "latest_observation": row.get("latest_observation"),
        "oldest_observation_age_days": (
            (issue - oldest) / pd.Timedelta(days=1) if pd.notna(oldest) else None
        ),
        "recorded_latest_age_days": row.get("veg_observation_age_days"),
        "recorded_valid_area_fraction": row.get("veg_valid_pixel_fraction"),
    }])
    start = pd.to_datetime(row.get("composite_start"))
    if pd.isna(start):
        section.messages.append("Pending: no qualifying composite recorded for this cell-issue.")
        return
    key = start.strftime("%Y%j")
    composite_path = base / "composites" / f"{key}.parquet"
    if composite_path.is_file():
        composite = _cell_row(composite_path, row["cell_id"])
        if not composite.empty:
            values = composite.iloc[0].to_dict()
            area_path = root / "datasets/processed/cells.parquet"
            areas = _cell_row(area_path, row["cell_id"]) if area_path.is_file() else pd.DataFrame()
            national = areas.iloc[0].get("national_area_km2") if not areas.empty else None
            fraction = values.get("valid_fraction")
            section.add("Area weighting: saved national cell aggregate", [{
                "cell_id": row["cell_id"], "composite_start": start,
                "national_cell_area_km2": national, "accepted_area_fraction": fraction,
                "accepted_area_km2": national * fraction
                if national is not None and fraction is not None else None,
                "weighted_ndvi": values.get("ndvi"), "weighted_evi": values.get("evi"),
            }])
    else:
        section.messages.append("Pending: saved composite aggregates for the example.")
    audit = composite_path.with_suffix(".audit.json")
    if audit.is_file():
        section.add("QA audit (whole source composite, not cell-specific)", [_read_json(audit)])
    else:
        section.messages.append("Pending: saved QA/scaling audit for the example composite.")
    source = base / "source.json"
    if source.is_file():
        provenance = _read_json(source).get("composites", {}).get(key, {})
        section.add("Selected composite provenance", [provenance] if provenance else [])


def _land_cover_example(
    section: ExtensionSection, root: Path, cell_id: str, issue: pd.Timestamp,
) -> None:
    base = root / "datasets/processed/landcover"
    paths = sorted((base / "features").glob(f"**/year={issue.year}/part.parquet"))
    annual = False
    if not paths:
        # Pilot and partial rasters do not establish full-grid feature availability.
        paths = sorted(base.glob("mapbiomas_[0-9][0-9][0-9][0-9].parquet"), reverse=True)
        paths = [p for p in paths if 1 <= issue.year - int(p.stem.split("_")[-1]) <= 2]
        paths = paths[:1]
        annual = True
    for path in paths:
        example = _cell_row(path, cell_id, None if annual else issue)
        if example.empty:
            continue
        row = example.iloc[0].to_dict()
        row["artifact"] = path.relative_to(root).as_posix()
        if annual:
            row["nominal_map_year"] = issue.year - 1
            row["reference_age_years"] = issue.year - int(row["map_year"])
        section.add("Land-cover fractions (retrospective; saved output)", [row])
        return
    section.messages.append("Pending: land-cover fractions for this cell-issue.")


def extension_feature_example(
    root: Path, cell_id: str, issue: str | pd.Timestamp,
) -> ExtensionSection:
    section = ExtensionSection()
    issue = pd.to_datetime(issue, utc=True)
    path = root / (
        "datasets/processed/vegetation/features/lag=16/geography=temporal/"
        f"year={issue.year}/part.parquet"
    )
    if path.is_file():
        example = _cell_row(path, cell_id, issue)
        if not example.empty:
            row = example.iloc[0].to_dict()
            section.add("Recorded vegetation features: primary temporal lag", [row])
            _vegetation_details(section, root, row, issue)
        else:
            section.messages.append("Pending: vegetation row for this exact cell-issue.")
    else:
        section.messages.append("Pending: processed vegetation features.")
    _land_cover_example(section, root, cell_id, issue)
    return section


def _artifact_paths(root: Path, *patterns: str) -> list[Path]:
    directory = root / "artifacts/evaluation"
    directories = [directory, *sorted((directory / "amendments").glob("*"))]
    return [
        path for folder in directories
        for path in sorted({p for pattern in patterns for p in folder.glob(pattern)})
    ]


def _selection_records(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    paths = _artifact_paths(root, "extension_*selection*.json", "selection_extension_*.json")
    records = []
    for path in paths:
        if path.stem.endswith("_sha256"):
            continue
        record = _read_json(path)
        if isinstance(record, dict) and ("runs" in record or "run" in record):
            records.append((path, record))
    # Amendment records supersede individual runs, leaving other hypotheses intact.
    run_sources = {name: path for path, record in records for name in _runs(record)}
    hypothesis_sources = {
        name: path for path, record in records for name in record.get("hypotheses", {})
    }
    return [
        (path, {**record,
                "runs": {name: run for name, run in _runs(record).items()
                         if run_sources[name] == path},
                "hypotheses": {name: value for name, value in record.get("hypotheses", {}).items()
                               if hypothesis_sources[name] == path}})
        for path, record in records
    ]


def _runs(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if "runs" in record:
        return record["runs"]
    run = record.get("run", {})
    return {run.get("run_id", record.get("run_id", "")): record}


def _identity(path: Path, root: Path, name: str, run: dict[str, Any]) -> dict[str, Any]:
    metadata = run.get("run", {})
    return {
        "artifact": path.relative_to(root).as_posix(), "run_id": name,
        **{key: metadata[key] for key in (
            "hypothesis", "geography", "availability_lag_days", "role", "comparator",
        ) if key in metadata},
    }


def extension_selections(root: Path) -> ExtensionSection:
    section = ExtensionSection()
    rows = []
    for path, record in _selection_records(root):
        for name, run in _runs(record).items():
            identity = _identity(path, root, name, run)
            rows.append({
                **identity,
                **{key: run.get(key) for key in (
                    "status", "reason", "selected_model", "calibration_status", "selection_rule",
                )},
            })
            section.add(f"Recorded search: {name}", run.get("search", []))
            if run.get("train_support"):
                section.add(f"Recorded training support: {name}", [run["train_support"]])
            provenance = {
                key: value for key, value in {**record, **run}.items()
                if key.endswith("sha256") or key in {
                    "registration_commit", "artifact_hashes", "input_hashes", "resources",
                }
            }
            if provenance:
                section.add(f"Recorded provenance and resources: {name}", [provenance])
    if rows:
        section.tables.insert(0, ("Selection records", pd.json_normalize(rows)))
    else:
        section.messages.append("Pending: extension selection records.")
    return section


def _paired_row(identity: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    bootstrap = report.get("bootstrap", {})
    paired = (bootstrap.get("paired_difference") or {}).get("average_precision", {})
    return {
        **identity,
        **{key: report.get(key) for key in (
            "rows", "positives", "prevalence", "represented_months", "row_key_sha256",
        )},
        "calibrated_ap": report.get("calibrated", {}).get("average_precision"),
        "comparator_ap": report.get("comparator", {}).get("average_precision"),
        "paired_ap_lower": paired.get("lower"), "paired_ap_upper": paired.get("upper"),
        "interval_reason": paired.get("reason"),
        "replicates": bootstrap.get("replicates"), "seed": bootstrap.get("seed"),
        "blocks": bootstrap.get("blocks"),
        "status": report.get("status"), "reason": report.get("reason"),
        **report.get("decision", {}),
    }


def _support_rows(
    identity: dict[str, Any], support: dict[str, Any], stage: str,
) -> list[dict[str, Any]]:
    return [
        {**identity, "stage": stage, "member": member, **values}
        for member, values in support.items()
    ]


def _logged_opening(root: Path, record: dict[str, Any]) -> dict[str, Any] | None:
    path = root / "artifacts/evaluation/final_test_log.json"
    if not path.is_file():
        return None
    for opening in _read_json(path):
        if (
            opening.get("opening_id") == record.get("opening_id")
            and opening.get("type") == "extension_test"
            and opening.get("state") == "opened"
            and opening.get("reopening") is True
            and opening.get("classification") == "exploratory"
            and opening.get("reason")
            and opening.get("selection_sha256")
            and opening.get("selection_sha256") == record.get("selection_sha256")
            and record.get("classification") == "exploratory"
        ):
            return opening
    return None


def extension_evaluation(root: Path) -> ExtensionSection:
    section = ExtensionSection()
    decisions, validation, support = [], [], []
    selections = _selection_records(root)
    recorded_hypotheses = {
        name for _, record in selections for name in record.get("hypotheses", {})
    }
    for path, record in selections:
        for name, value in record.get("hypotheses", {}).items():
            decisions.append({"artifact": path.relative_to(root).as_posix(),
                              "hypothesis": name, **value})
        for name, run in _runs(record).items():
            identity = _identity(path, root, name, run)
            if (
                identity.get("hypothesis")
                and identity["hypothesis"] not in recorded_hypotheses
                and identity.get("geography") == "temporal"
                and identity.get("role") == "primary"
            ):
                decision = run.get("validation", {}).get("decision")
                if decision:
                    decisions.append({**identity, **decision})
                elif run.get("status") == "not_evaluated":
                    decisions.append({**identity, "status": run["status"],
                                      "reason": run.get("reason")})
            if run.get("validation"):
                validation.append(_paired_row(identity, run["validation"]))
            for stage, members in run.get("support", {}).items():
                support.extend(_support_rows(identity, members, stage))
        _lag_tables(section, path, root, record)
    section.add("H3/H4/H5: recorded primary validation decisions", decisions)
    section.add("Paired validation intervals and recorded row counts", validation)
    section.add("Identical-row support: selection and calibration", support)
    if not decisions:
        section.messages.append("Pending: frozen H3/H4/H5 validation decisions.")
    tests = _artifact_paths(root, "extension_test_*.json")
    if not tests:
        section.messages.append("Pending: logged exploratory extension test results.")
    logged_tests = []
    for path in tests:
        record = _read_json(path)
        opening = _logged_opening(root, record)
        if opening is None:
            section.messages.append(f"Pending: matching logged reopening for {path.name}.")
            continue
        logged_tests.append((path, record, opening))
    test_sources = {name: path for path, record, _ in logged_tests for name in _runs(record)}
    for path, record, opening in logged_tests:
        section.add(f"Logged reopening: {path.name}", [opening])
        paired, counts = [], []
        for name, run in _runs(record).items():
            if test_sources[name] != path:
                continue
            identity = {"artifact": path.relative_to(root).as_posix(), "run_id": name}
            paired.append(_paired_row(identity, run))
            counts.extend(_support_rows(identity, run.get("support", {}), "test"))
            section.add(f"Exploratory subgroups: {name}", [
                _paired_row({**identity, "subgroup": subgroup}, values)
                for subgroup, values in run.get("exploratory_subgroups", {}).items()
            ])
        section.add(f"Logged exploratory test: {path.name}", paired)
        section.add(f"Identical-row test support: {path.name}", counts)
        _lag_tables(section, path, root, record)
    guards = []
    for path in _artifact_paths(root, "extension_confirmation_guard.json"):
        guard = _read_json(path)
        guards.append({"artifact": path.relative_to(root).as_posix(), **{
            key: guard.get(key) for key in (
                "status", "reason", "blockers", "no_confirmation_labels_or_scores_accessed",
                "confirmation_opening_consumed",
            )
        }})
    section.add("Recorded confirmation guards (no confirmation scores)", guards)
    return section


def _lag_tables(
    section: ExtensionSection, path: Path, root: Path, record: dict[str, Any],
) -> None:
    for name, values in record.get("lag_sensitivity", {}).items():
        identity = {"artifact": path.relative_to(root).as_posix(), "run_id": name,
                    "role": values.get("role")}
        section.add(f"Lag sensitivity comparison: {name}", [
            _paired_row(identity, values.get("comparison", {})),
        ])
        section.add(f"Identical-row lag intersection: {name}", _support_rows(
            identity, values.get("support", {}), "lag_intersection",
        ))
