from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from bolivia_fire.config import Paths
from bolivia_fire.landcover.usage import UsageLedger, UsagePaused, atomic_json, redact_project


def _verify_evidence(report_path: Path | None, attestation_path: Path | None) -> None:
    if report_path is None or attestation_path is None:
        raise UsagePaused("A verification report and owner attestation are required")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
    checks = report["checks"]
    if (
        attestation.get("earth_engine_tier") != "Community"
        or attestation.get("billing_account_linked") is not False
        or checks.get("initialization", {}).get("status") != "passed"
        or checks.get("registration", {}).get("registration_state") != "REGISTERED_NOT_COMMERCIALLY"
        or checks.get("service_state_earthengine.googleapis.com", {}).get("service_state")
        != "ENABLED"
        or checks.get("eecu_visibility", {}).get("status") != "passed"
        or report.get("owner_attestation_sha256")
        != hashlib.sha256(attestation_path.read_bytes()).hexdigest()
    ):
        raise UsagePaused("Zero-cost verification evidence is incomplete or mismatched")


def run(args: argparse.Namespace, paths: Paths) -> int:
    project = ""
    try:
        state = json.loads(args.ledger.read_text(encoding="utf-8"))
        if args.operation == "status":
            fields = (
                "status",
                "submissions_authorized",
                "project_monthly_budget_eecu_hours",
                "workstream_safety_cap_eecu_hours",
                "project_month_eecu_hours",
                "workstream_eecu_hours",
                "unreconciled_overhead_reserve_eecu_hours",
                "full_aggregation_estimate_eecu_hours",
            )
            print(json.dumps({key: state.get(key) for key in fields}, allow_nan=False))
            return 0
        if state.get("status") != "active" or state.get("submissions_authorized") is not True:
            raise UsagePaused("Usage ledger is paused; new Earth Engine submissions are disabled")
        _verify_evidence(args.verification_report, args.owner_attestation)
        if args.operation == "raster":
            from bolivia_fire.landcover.acquire import run_raster

            return run_raster(args, paths)
        from bolivia_fire.landcover.earthengine import (
            EarthEngineSession,
            ResourceMonitor,
            load_cells,
            load_project,
        )

        project = load_project(paths.root)
        ledger = UsageLedger(args.ledger)
        destination = (
            paths.datasets / "processed/landcover" / f"pilot_{args.cells}_{args.year}.parquet"
        )
        if destination.exists():
            raise UsagePaused("Pilot output exists; refusing an unreviewed replacement")
        with ResourceMonitor(paths.root) as resources:
            resources.check()
            cells, country = load_cells(paths)
            indices = [
                round(index * (len(cells) - 1) / (args.cells - 1)) for index in range(args.cells)
            ]
            sample = cells.iloc[indices]
            session = EarthEngineSession(paths, ledger, resources)
            frame, report = session.batch(
                sample,
                country,
                args.year,
                f"pilot-{args.cells}-{args.year}",
                1.0,
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(destination, index=False)
            report.update(
                output=destination.relative_to(paths.root).as_posix(),
                output_bytes=destination.stat().st_size,
                output_sha256=hashlib.sha256(destination.read_bytes()).hexdigest(),
                classification="retrospective",
                collection="MapBiomas Bolivia Collection 4",
                release_date="2026-10-01",
                licence="CC BY 4.0",
                full_aggregation_estimate_eecu_hours=(
                    report["upper_eecu_seconds"] / len(sample) * len(cells) * 14 / 3600
                ),
            )
            serialized = redact_project(
                json.dumps(report, ensure_ascii=True, allow_nan=False), project
            )
            with (paths.manifests / "mapbiomas_pilot.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(serialized + "\n")
            state = json.loads(args.ledger.read_text(encoding="utf-8"))
            state.update(
                status="paused",
                submissions_authorized=False,
                pause_reason="Pilot completed; review measured full-grid estimate before new work",
                full_aggregation_estimate_eecu_hours=report["full_aggregation_estimate_eecu_hours"],
            )
            atomic_json(args.ledger, state)
            summary = {
                key: value for key, value in report.items() if key not in ("profile", "cell_ids")
            }
            print(redact_project(json.dumps(summary, allow_nan=False), project))
        return 0
    except (RuntimeError, ValueError, OSError, KeyError) as exc:
        print(redact_project(str(exc), project), file=sys.stderr)
        return 2


def register(subparsers: Any) -> None:
    commands = subparsers.add_parser("landcover").add_subparsers(dest="operation", required=True)
    status = commands.add_parser("status")
    pilot = commands.add_parser("pilot")
    raster = commands.add_parser("raster")
    for parser in (status, pilot, raster):
        parser.add_argument("--ledger", type=Path, required=True)
        parser.set_defaults(pipeline_handler=run)
    pilot.add_argument("--verification-report", type=Path, required=True)
    pilot.add_argument("--owner-attestation", type=Path, required=True)
    pilot.add_argument("--cells", type=int, choices=(100, 1000), default=100)
    pilot.add_argument("--year", type=int, choices=range(2012, 2026), default=2012)
    raster.add_argument("--verification-report", type=Path, required=True)
    raster.add_argument("--owner-attestation", type=Path, required=True)
    raster.add_argument("--report", type=Path, required=True)
