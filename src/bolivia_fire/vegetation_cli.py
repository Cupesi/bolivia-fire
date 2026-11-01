from __future__ import annotations

import argparse
import json

from bolivia_fire.config import Paths


def run(args: argparse.Namespace, paths: Paths) -> int:
    from bolivia_fire.vegetation.acquire import acquire

    result = acquire(
        paths.root,
        full=args.operation == "full",
        workers=args.workers,
        timeout=args.timeout,
        minimum_free_gb=args.minimum_free_gb,
    )
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "status",
                    "runtime_seconds",
                    "bytes_transferred",
                    "bytes_stored",
                    "free_bytes_after",
                    "reason",
                )
                if key in result
            }
        )
    )
    return 0 if result["status"] == "complete" else 1


def register(commands: argparse._SubParsersAction) -> None:
    vegetation = commands.add_parser("vegetation").add_subparsers(dest="operation", required=True)
    for operation in ("pilot", "full"):
        parser = vegetation.add_parser(operation)
        parser.add_argument("--workers", type=int, default=2)
        parser.add_argument("--timeout", type=float, default=14400)
        parser.add_argument("--minimum-free-gb", type=float, default=55)
        parser.set_defaults(pipeline_handler=run)
