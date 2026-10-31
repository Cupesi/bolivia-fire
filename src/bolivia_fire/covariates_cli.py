from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import date
from typing import Any

from bolivia_fire.config import Paths


def run(args: argparse.Namespace, paths: Paths) -> int:
    import httpx
    import pandas as pd
    from filelock import FileLock

    from bolivia_fire.config import load_protocol
    from bolivia_fire.covariates.chirps import (
        build_features,
        chirps_url,
        extract_year,
    )
    from bolivia_fire.covariates.mapbiomas import URL_TEMPLATE, extract_mapbiomas
    from bolivia_fire.covariates.raster import read_rainfall_window
    from bolivia_fire.covariates.storage import Budget, atomic_json, stored_bytes
    from bolivia_fire.pipeline_cli import peak_ram_mb

    tick = time.perf_counter()
    root = paths.datasets / "processed" / "covariates"
    budget = Budget(root)
    before = budget.transferred
    result: dict[str, Any]
    if args.operation == "probe":
        if args.product == "chirps":
            values, result = read_rainfall_window(chirps_url(args.date), budget)
            result["window_sha256"] = hashlib.sha256(values.astype("<f4").tobytes()).hexdigest()
            result["window_shape"] = list(values.shape)
        else:
            url = URL_TEMPLATE.format(year=args.year)
            with httpx.Client(timeout=60, follow_redirects=True) as client:
                head = client.head(url)
                with client.stream("GET", url, headers={
                    "Range": "bytes=0-15", "Accept-Encoding": "identity",
                }) as response:
                    length = int(response.headers.get("content-length", 0))
                    body = b""
                    if length and length <= 4096:
                        budget.consume(length)
                        body = response.read()
                    result = {
                        "url": url, "head_status": head.status_code,
                        "http_status": response.status_code,
                        "http_size": int(head.headers.get("content-length", 0)),
                        "content_range": response.headers.get("content-range"),
                        "response_bytes": len(body),
                        "range_supported": response.status_code == 206,
                        "error": body.decode("utf-8", errors="replace")
                        if response.status_code >= 400 else None,
                    }
    elif args.operation == "chirps":
        if args.start < date(2012, 1, 1) or args.end > date(2026, 1, 4):
            raise ValueError("CHIRPS range must stay within 2012-01-01 through 2026-01-04")
        if args.end < args.start:
            raise ValueError("End date precedes start date")
        result = {"years": []}
        with FileLock(str(root / ".chirps.lock"), timeout=0):
            for year in range(args.start.year, args.end.year + 1):
                record = extract_year(year, root, budget=budget, start=args.start,
                                      end=args.end, workers=args.workers)
                result["years"].append(record)
                print(json.dumps({"completed_year": year, "days": record["days"],
                                  "bytes_transferred": budget.transferred}), flush=True)
    elif args.operation == "features":
        protocol = load_protocol(paths.root)
        cells = pd.read_parquet(paths.datasets / "processed" / "cells.parquet")
        start = args.start_issue or date.fromisoformat(protocol["time"]["first_issue"])
        end = args.end_issue or date.fromisoformat(protocol["time"]["last_issue"])
        first_train, last_train = protocol["partitions"]["train"]
        years = range(date.fromisoformat(first_train).year, date.fromisoformat(last_train).year + 1)
        result = build_features(root, cells, pd.date_range(start, end, freq="W-MON"), years)
    elif args.operation == "mapbiomas":
        cells = pd.read_parquet(paths.datasets / "processed" / "cells.parquet")
        result = extract_mapbiomas(args.year, root, cells)
    else:
        result = {"bytes_transferred_total": budget.transferred}
    result.update(
        runtime_seconds=time.perf_counter() - tick, peak_ram_mb=peak_ram_mb(),
        bytes_transferred_run=budget.transferred-before,
        bytes_transferred_total=budget.transferred, bytes_stored=stored_bytes(root),
    )
    atomic_json(root / f"last_{args.operation}.json", result)
    if args.operation == "probe":
        atomic_json(root / f"probe_{args.product}.json", result)
    print(json.dumps(result, sort_keys=True, allow_nan=False), flush=True)
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    commands = subparsers.add_parser("covariates").add_subparsers(dest="operation", required=True)
    probe = commands.add_parser("probe")
    probe.add_argument("--product", choices=("chirps", "mapbiomas"), required=True)
    probe.add_argument("--date", type=date.fromisoformat, default=date(2024, 1, 1))
    probe.add_argument("--year", type=int, default=2023)
    rain = commands.add_parser("chirps")
    rain.add_argument("--start", type=date.fromisoformat, required=True)
    rain.add_argument("--end", type=date.fromisoformat, required=True)
    rain.add_argument("--workers", type=int, choices=(1,), default=1)
    features = commands.add_parser("features")
    features.add_argument("--start-issue", type=date.fromisoformat)
    features.add_argument("--end-issue", type=date.fromisoformat)
    land = commands.add_parser("mapbiomas")
    land.add_argument("--year", type=int, choices=range(2012, 2025), required=True)
    summary = commands.add_parser("summary")
    for parser in (probe, rain, features, land, summary):
        parser.set_defaults(pipeline_handler=run)
