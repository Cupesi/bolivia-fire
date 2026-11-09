from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pandas as pd
from filelock import FileLock

from bolivia_fire.config import Paths, load_key
from bolivia_fire.firms.quota import Quota
from bolivia_fire.live.bootstrap import bootstrap
from bolivia_fire.live.pipeline import update
from bolivia_fire.live.retire import refuse_retired, retire
from bolivia_fire.live.source import LiveClient
from bolivia_fire.redact import RedactionFilter, Redactor


def run_backfill(args: argparse.Namespace, paths: Paths) -> int:
    from bolivia_fire.live.backfill import backfill

    out, cache = args.out.resolve(), args.cache.resolve()
    try:
        with FileLock(str(out.parent / f".{out.name}.lock"), timeout=0):
            result = backfill(
                paths,
                out,
                cache,
                pd.Timestamp(args.first),
                pd.Timestamp(args.last),
                datetime.now(UTC),
                check_parity=args.check_parity,
            )
            print(json.dumps(result, allow_nan=False))
    except (OSError, ValueError, RuntimeError, AssertionError) as exc:
        print(str(exc))
        return 1
    return 0


def run(args: argparse.Namespace, paths: Paths) -> int:
    if args.operation == "retire":
        out = args.out.resolve()
        cache = args.cache.resolve() if args.cache else out.parent / f".{out.name}-cache"
        try:
            with FileLock(str(out.parent / f".{out.name}.lock"), timeout=0):
                print(json.dumps(retire(out, cache), allow_nan=False))
        except (OSError, ValueError, RuntimeError, httpx.HTTPError) as exc:
            print(str(exc))
            return 1
        return 0
    try:
        refuse_retired(args.out.resolve())
    except ValueError as exc:
        print(str(exc))
        return 1
    started = time.perf_counter()
    key = load_key(paths.root)
    redactor = Redactor([key])
    out = args.out.resolve()
    cache = args.cache.resolve() if args.cache else out.parent / f".{out.name}-cache"
    cache.mkdir(parents=True, exist_ok=True)
    handler = logging.StreamHandler()
    handler.addFilter(RedactionFilter(redactor))
    logger = logging.getLogger("bolivia_fire.live")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    try:
        # Share the existing conservative quota ledger with other FIRMS acquisition commands.
        with (
            FileLock(str(out.parent / f".{out.name}.lock"), timeout=0),
            LiveClient(
                key,
                cache / "quarantine",
                quota=Quota(paths.temporary),
            ) as client,
        ):
            now = datetime.now(UTC)
            if args.operation == "bootstrap":
                result = bootstrap(paths, out, cache, client, now)
            else:
                issue = pd.Timestamp(args.issue) if args.issue is not None else None
                scheduled = (
                    os.environ.get("GITHUB_ACTIONS") == "true"
                    and os.environ.get("GITHUB_EVENT_NAME") == "schedule"
                )
                result = update(paths, out, cache, client, now, issue, scheduled=scheduled)
            result["runtime_seconds"] = round(time.perf_counter() - started, 3)
            print(json.dumps(redactor.sanitize(result), allow_nan=False))
    except (OSError, ValueError, RuntimeError, httpx.HTTPError) as exc:
        logger.error("%s", redactor.text(exc))
        return 1
    finally:
        logger.removeHandler(handler)
    return 0


def register(commands: argparse._SubParsersAction) -> None:
    operations = commands.add_parser("live").add_subparsers(dest="operation", required=True)
    parser = operations.add_parser("backfill")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--from", dest="first", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="last", type=date.fromisoformat, required=True)
    parser.add_argument("--check-parity", action="store_true")
    parser.set_defaults(pipeline_handler=run_backfill)
    for name in ("bootstrap", "update", "retire"):
        parser = operations.add_parser(name)
        parser.add_argument("--out", type=Path, required=True)
        parser.add_argument("--cache", type=Path)
        if name == "update":
            parser.add_argument("--issue", type=date.fromisoformat)
        parser.set_defaults(pipeline_handler=run)
