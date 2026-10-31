from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from bolivia_fire.boundaries import acquire_boundaries
from bolivia_fire.config import Paths, load_key, load_protocol
from bolivia_fire.covariates_cli import register as register_covariates
from bolivia_fire.experiments_cli import register as register_experiments
from bolivia_fire.firms.acquire import Acquisition, partition_plan
from bolivia_fire.firms.client import FirmsClient
from bolivia_fire.firms.quota import Quota
from bolivia_fire.firms.storage import durable_write
from bolivia_fire.live_cli import register as register_live
from bolivia_fire.pipeline_cli import register
from bolivia_fire.redact import RedactionFilter, Redactor


def _logging(paths: Paths, redactor: Redactor) -> None:
    paths.temporary.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    for handler in root.handlers:
        handler.addFilter(RedactionFilter(redactor))
    logger = logging.getLogger("bolivia_fire")
    logger.setLevel(logging.INFO)
    for old in list(logger.handlers):
        logger.removeHandler(old)
        old.close()
    for handler in (
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(paths.temporary / "acquire_firms.log", encoding="utf-8"),
    ):
        handler.addFilter(RedactionFilter(redactor))
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(handler)
    logger.propagate = False
    # HTTP debug output may include credentials before message formatting.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bolivia-fire")
    commands = parser.add_subparsers(dest="command", required=True)
    firms = commands.add_parser("firms").add_subparsers(dest="operation", required=True)
    firms.add_parser("status")
    firms.add_parser("availability")
    acquire = commands.add_parser("acquire").add_subparsers(dest="dataset", required=True)
    area = acquire.add_parser("firms")
    area.add_argument("--start", type=date.fromisoformat, required=True)
    area.add_argument("--end", type=date.fromisoformat, required=True)
    area.add_argument("--dry-run", action="store_true")
    area.add_argument("--max-partitions", type=int)
    area.add_argument("--budget", type=int, default=4000)
    area.add_argument(
        "--transactions-per-day", type=int, default=2,
        help="Quota reservation per requested day, including retries",
    )
    area.add_argument("--minimum-free-gb", type=float, default=5)
    area.add_argument(
        "--force", action="store_true", help="Re-fetch and preserve differing snapshots"
    )
    acquire.add_parser("boundaries")
    register(commands)
    register_covariates(commands)
    register_experiments(commands)
    register_live(commands)
    return parser


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    args = _parser().parse_args(argv)
    paths = Paths(root) if root is not None else Paths()
    redactor = Redactor()
    try:
        if hasattr(args, "pipeline_handler"):
            return args.pipeline_handler(args, paths)
        if args.command == "acquire" and args.dataset == "boundaries":
            _logging(paths, redactor)
            for record in acquire_boundaries(paths):
                print(json.dumps(record))
            return 0
        if args.command == "acquire" and args.dry_run:
            if args.max_partitions is not None and args.max_partitions < 1:
                raise ValueError("max_partitions must be positive")
            for index, partition in enumerate(partition_plan(args.start, args.end)):
                if args.max_partitions is not None and index >= args.max_partitions:
                    break
                print(
                    f"{partition.filename} start={partition.start} day_range={partition.day_range}"
                )
            return 0
        key = load_key(paths.root)
        redactor = Redactor([key])
        _logging(paths, redactor)
        quota = Quota(paths.temporary, budget=getattr(args, "budget", 4000))
        with FirmsClient(
            key, paths.quarantine, quota=quota,
            transactions_per_day=getattr(args, "transactions_per_day", 2),
        ) as client:
            if args.command == "firms":
                if args.operation == "status":
                    print(json.dumps(client.status(fresh=True)))
                else:
                    body = client.availability()
                    name = f"firms_availability_{datetime.now(UTC).date()}.csv"
                    path = paths.manifests / name
                    durable_write(path, body)
                    print(
                        f"availability={path.relative_to(paths.root).as_posix()} bytes={len(body)}"
                    )
            else:
                protocol = load_protocol(paths.root)
                if protocol["target"]["source_product"] != "VIIRS_SNPP_SP":
                    raise ValueError("Protocol source product is not VIIRS_SNPP_SP")
                bbox = tuple(float(value) for value in protocol["study_area"]["acquisition_bbox"])
                if len(bbox) != 4:
                    raise ValueError("Protocol bbox must have four coordinates")
                Acquisition(
                    paths,
                    client,
                    bbox=bbox,
                    minimum_free_bytes=int(args.minimum_free_gb * 1024**3),
                ).run(args.start, args.end, max_partitions=args.max_partitions, force=args.force)
        return 0
    except (RuntimeError, ValueError, OSError) as exc:
        print(redactor.text(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
