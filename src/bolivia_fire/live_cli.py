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
from bolivia_fire.live.source import LiveClient
from bolivia_fire.redact import RedactionFilter, Redactor


def run(args: argparse.Namespace, paths: Paths) -> int:
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
    for name in ("bootstrap", "update"):
        parser = operations.add_parser(name)
        parser.add_argument("--out", type=Path, required=True)
        parser.add_argument("--cache", type=Path)
        if name == "update":
            parser.add_argument("--issue", type=date.fromisoformat)
        parser.set_defaults(pipeline_handler=run)
