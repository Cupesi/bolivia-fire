from __future__ import annotations

import argparse
import json
import time

from bolivia_fire.config import Paths
from bolivia_fire.experiments import Experiment
from bolivia_fire.pipeline_cli import peak_ram_mb


def run(args: argparse.Namespace, paths: Paths) -> int:
    tick = time.perf_counter()
    experiment = Experiment(
        paths,
        variant=args.variant,
        selection=args.selection,
        drop_group=args.drop_group,
        exclude_low_coverage=args.exclude_low_coverage,
    )
    if args.command == "train":
        result = experiment.train()
        if not args.variant.endswith("_geo"):
            experiment.geographic().train()
    elif args.command == "select":
        result = experiment.select()
        if not args.variant.endswith("_geo"):
            experiment.geographic().select()
    elif args.command == "evaluate":
        result = experiment.evaluate(
            split=args.split, open_final_test=args.open_final_test, reopen_reason=args.reopen_reason
        )
    else:
        result = experiment.predict(split=args.split)
    summary = {name: result[name] for name in ("selected_model", "rows") if name in result}
    summary.update(runtime_seconds=time.perf_counter() - tick, peak_ram_mb=peak_ram_mb())
    print(json.dumps(summary))
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    for name in ("train", "select", "evaluate", "predict"):
        command = subparsers.add_parser(name)
        command.add_argument(
            "--variant",
            choices=[
                "strict",
                "retrospective",
            ]
            + (["strict_geo", "retrospective_geo"] if name in {"train", "select"} else []),
            default="strict",
        )
        command.add_argument(
            "--selection", choices=["primary", "all_confidence"], default="primary"
        )
        command.add_argument("--drop-group", choices=["neighborhood", "climatology", "national"])
        command.set_defaults(pipeline_handler=run)
        command.add_argument("--exclude-low-coverage", action="store_true")
        if name in {"evaluate", "predict"}:
            command.add_argument(
                "--split", choices=["val_select", "val_calib", "test"], default="test"
            )
        if name == "evaluate":
            command.add_argument("--open-final-test", action="store_true")
            command.add_argument("--reopen-reason")
