from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from bolivia_fire.config import Paths, load_protocol
from bolivia_fire.detections import build_detections, resolve_detection_bundle
from bolivia_fire.grid import write_cells
from bolivia_fire.labels import explain_label, labels_for_issue
from bolivia_fire.panel import build_panel, print_summary


def peak_ram_mb() -> float:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class MemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = MemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(MemoryCounters),
            wintypes.DWORD,
        ]
        if not psapi.GetProcessMemoryInfo(
            kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb
        ):
            raise OSError("Unable to read process peak working set")
        return counters.PeakWorkingSetSize / 1024**2
    import resource

    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value / (1024**2 if sys.platform == "darwin" else 1024)


def run(args: argparse.Namespace, paths: Paths) -> int:
    tick = time.perf_counter()
    protocol = load_protocol(paths.root)
    if args.command == "build":
        if args.dataset == "cells":
            cells = write_cells(paths, protocol, args.output_dir)
            summary: dict[str, Any] = {
                "eligible_cells": len(cells),
                "national_area_km2": float(cells.national_area_km2.sum()),
                "blocks": cells.block_id.nunique(),
                "held_out_blocks": cells.loc[cells.geo_holdout, "block_id"].nunique(),
                "santa_cruz_cells": int(cells.santa_cruz.sum()),
            }
        elif args.dataset == "detections":
            summary = build_detections(
                paths, protocol, args.output_dir, manifest_snapshot=args.manifest_snapshot
            )
        else:
            summary = build_panel(
                paths, protocol, args.start_issue, args.end_issue, args.output_dir
            )
    else:
        cells = pd.read_parquet(paths.datasets / "processed" / "cells.parquet")
        cell = cells[cells.cell_id == args.cell_id]
        if cell.empty:
            raise ValueError("Cell is not eligible")
        detection_source, days_source, _ = resolve_detection_bundle(paths, protocol)
        data = pd.read_parquet(detection_source)
        days = pd.read_parquet(days_source)
        labels = labels_for_issue(cell, data, days, args.issue_date)
        rows = explain_label(data, args.cell_id, args.issue_date)
        complete = bool(labels.target_complete.iloc[0])
        summary = {
            "cell_id": args.cell_id,
            "issue_date": args.issue_date.isoformat(),
            "n_primary": len(rows),
            "y_primary": int(labels.y_primary.iloc[0]) if complete else None,
            "target_complete": complete,
            "source_rows": rows[["source_file", "source_line"]].to_dict(orient="records"),
        }
    summary["runtime_seconds"] = time.perf_counter() - tick
    summary["peak_ram_mb"] = peak_ram_mb()
    print_summary(summary)
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    build = subparsers.add_parser("build").add_subparsers(dest="dataset", required=True)
    for name in ("cells", "detections", "panel"):
        command = build.add_parser(name)
        command.set_defaults(pipeline_handler=run)
        command.add_argument("--output-dir", type=Path)
        if name == "detections":
            command.add_argument("--manifest-snapshot", type=Path)
        if name == "panel":
            command.add_argument("--start-issue", type=date.fromisoformat)
            command.add_argument("--end-issue", type=date.fromisoformat)
    explain = subparsers.add_parser("explain-label")
    explain.add_argument("cell_id")
    explain.add_argument("issue_date", type=date.fromisoformat)
    explain.set_defaults(pipeline_handler=run)
